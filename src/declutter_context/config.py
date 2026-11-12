"""Configuration loading: defaults, then the global TOML file, then the project file.

Loading never raises. Invalid or unknown entries and unparseable files are ignored and
reported through :func:`last_warnings`.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from declutter_context.fsutil import safe_read_text
from declutter_context.model import DEFAULT_THRESHOLDS, Config, ReorgConfig

PROJECT_CONFIG_NAME = "declutter.toml"
_LANGS = frozenset({"es", "en"})
_SCALAR_KEYS = frozenset(
    {
        "lang",
        "bytes_per_token",
        "codex_budget_bytes",
        "import_max_depth",
        "memory_max_lines",
        "memory_max_bytes",
    }
)

# ``[reorg]`` scalars (spec §3, §6.4): key -> (min, max) inclusive; ``None`` = no upper bound.
REORG_SCALARS: Mapping[str, tuple[int, int | None]] = {
    "l1_max_bytes": (1024, None),
    "l1_max_lines": (20, None),
    "zone_max_lines": (20, None),
    "codex_warn_pct": (1, 99),
}
# ``[reorg.lexicon]`` categories (spec §5.3); each value is a list of extra expressions.
LEXICON_CATEGORIES = ("clinical", "secrets", "publishable", "device", "irreversible")

_warnings: list[str] = []


def last_warnings() -> list[str]:
    """Warnings collected by the most recent :func:`load_config` call."""
    return list(_warnings)


def config_dir(home: Path | None = None) -> Path:
    """``$XDG_CONFIG_HOME/declutter`` if set, else ``<home>/.config/declutter``."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "declutter"
    return (home or Path.home()) / ".config" / "declutter"


def registry_path(home: Path | None = None) -> Path:
    """Path of the project registry file."""
    return config_dir(home) / "projects.toml"


def default_lang() -> str:
    """``"es"`` if the first non-empty of LC_ALL / LC_MESSAGES / LANG starts with ``es``."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").strip()
        if value:
            return "es" if value.lower().startswith("es") else "en"
    return "en"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read_toml(path: Path, label: str) -> Mapping[str, Any] | None:
    """Parse a TOML file, or return None (silently if missing, with a warning if broken)."""
    text = safe_read_text(path)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        _warnings.append(f"{label}: cannot parse {path.name}: {exc}")
        return None


def _validate_scalar(key: str, value: object) -> object | None:
    """Return the accepted value for a scalar key, or None when invalid."""
    if key == "lang":
        return value if isinstance(value, str) and value in _LANGS else None
    if isinstance(value, bool):
        return None
    if key == "bytes_per_token":
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return None
    if not isinstance(value, int):
        return None
    if key == "import_max_depth":
        return value if value >= 0 else None
    # codex_budget_bytes, memory_max_lines, memory_max_bytes
    return value if value > 0 else None


def _parse_threshold(value: object) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    yellow, red = value
    if not (_is_number(yellow) and _is_number(red)):
        return None
    if float(yellow) > float(red):
        return None
    return float(yellow), float(red)


def _valid_reorg_scalar(key: str, value: object) -> int | None:
    """Return ``value`` if it is an int (not bool) within the range of ``key``, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    low, high = REORG_SCALARS[key]
    if value < low or (high is not None and value > high):
        return None
    return value


def _has_group_reference(expr: str) -> bool:
    """Return True if ``expr`` has a backreference or a group-conditional construct.

    Numbered backreferences (``\\1``) change meaning once the expression is joined with
    other alternatives, and named ones or conditionals depend on groups defined elsewhere.
    Escaped backslashes are skipped, so ``\\\\1`` (a literal backslash then ``1``) is fine.
    """
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "\\" and i + 1 < len(expr):
            if expr[i + 1] in "123456789":
                return True
            i += 2
            continue
        if expr.startswith("(?P=", i) or expr.startswith("(?(", i):
            return True
        i += 1
    return False


def _valid_lexicon_expr(expr: str) -> bool:
    """Return True if ``expr`` is safe as one member of a ``|``-joined lexicon pattern.

    Each extension is later joined to the built-in pattern with ``|``. It must therefore
    compile as a non-first alternative (no global inline flags such as ``(?i)``), and it
    may not define named groups or refer to groups, since those can clash with, or be
    renumbered by, the other members of the union.
    """
    try:
        compiled = re.compile("(?:x)|" + expr, re.IGNORECASE)
    except (re.error, OverflowError, RecursionError, ValueError):
        return False
    return not compiled.groupindex and not _has_group_reference(expr)


def _overlay_lexicon(value: object, label: str, lexicon: dict[str, list[str]]) -> None:
    """Add the valid expressions of a ``[reorg.lexicon]`` table to ``lexicon`` (union)."""
    if not isinstance(value, dict):
        _warnings.append(f"{label}: 'reorg.lexicon' must be a table; ignored")
        return
    for cat, exprs in value.items():
        name = f"reorg.lexicon.{cat}"
        if cat not in LEXICON_CATEGORIES:
            _warnings.append(f"{label}: unknown key '{name}'; ignored")
            continue
        if not isinstance(exprs, list) or not all(isinstance(e, str) for e in exprs):
            _warnings.append(f"{label}: invalid value for '{name}' (expected strings); ignored")
            continue
        bucket = lexicon.setdefault(cat, [])
        for index, expr in enumerate(exprs):
            if not _valid_lexicon_expr(expr):
                # The expression itself is not echoed: report its position only.
                _warnings.append(f"{label}: invalid expression #{index + 1} in '{name}'; ignored")
                continue
            if expr not in bucket:
                bucket.append(expr)


def _overlay_reorg(
    value: object, label: str, reorg: dict[str, int], lexicon: dict[str, list[str]]
) -> None:
    """Apply one ``[reorg]`` table: scalars replace earlier values, lexicons are unioned."""
    if not isinstance(value, dict):
        _warnings.append(f"{label}: 'reorg' must be a table; ignored")
        return
    for key, sub in value.items():
        if key == "lexicon":
            _overlay_lexicon(sub, label, lexicon)
        elif key in REORG_SCALARS:
            accepted = _valid_reorg_scalar(key, sub)
            if accepted is None:
                _warnings.append(f"{label}: invalid value for 'reorg.{key}'; ignored")
            else:
                reorg[key] = accepted
        else:
            _warnings.append(f"{label}: unknown key 'reorg.{key}'; ignored")


def _overlay(
    data: Mapping[str, Any],
    label: str,
    scalars: dict[str, object],
    thresholds: dict[str, tuple[float, float]],
    reorg: dict[str, int],
    lexicon: dict[str, list[str]],
) -> None:
    """Apply one parsed file onto the accumulated scalars, thresholds and reorg settings."""
    for key, value in data.items():
        if key == "reorg":
            _overlay_reorg(value, label, reorg, lexicon)
        elif key == "thresholds":
            if not isinstance(value, dict):
                _warnings.append(f"{label}: 'thresholds' must be a table; ignored")
                continue
            for tkey, tval in value.items():
                if tkey not in DEFAULT_THRESHOLDS:
                    _warnings.append(f"{label}: unknown threshold '{tkey}'; ignored")
                    continue
                parsed = _parse_threshold(tval)
                if parsed is None:
                    _warnings.append(
                        f"{label}: invalid threshold '{tkey}' (expected [yellow, red] "
                        "numbers with yellow <= red); ignored"
                    )
                    continue
                thresholds[tkey] = parsed
        elif key in _SCALAR_KEYS:
            accepted = _validate_scalar(key, value)
            if accepted is None:
                _warnings.append(f"{label}: invalid value for '{key}'; ignored")
            else:
                scalars[key] = accepted
        else:
            _warnings.append(f"{label}: unknown key '{key}'; ignored")


def load_config(
    project_root: Path | None,
    *,
    global_path: Path | None = None,
    home: Path | None = None,
) -> Config:
    """Build the effective :class:`Config`: defaults, then global file, then project file."""
    _warnings.clear()
    scalars: dict[str, object] = {"lang": default_lang()}
    thresholds: dict[str, tuple[float, float]] = dict(DEFAULT_THRESHOLDS)
    reorg: dict[str, int] = {}
    lexicon: dict[str, list[str]] = {}

    sources: list[tuple[Path, str]] = [
        (global_path or config_dir(home) / "config.toml", "global config")
    ]
    if project_root is not None:
        sources.append((project_root / PROJECT_CONFIG_NAME, "project config"))
    for path, label in sources:
        data = _read_toml(path, label)
        if data is not None:
            _overlay(data, label, scalars, thresholds, reorg, lexicon)

    reorg_cfg = ReorgConfig(
        **reorg,
        extra_lexicon={cat: tuple(exprs) for cat, exprs in lexicon.items() if exprs},
    )
    return Config(thresholds=thresholds, reorg=reorg_cfg, **scalars)  # type: ignore[arg-type]
