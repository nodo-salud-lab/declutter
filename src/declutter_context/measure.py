"""Run all nine signals for a project and (de)serialize the result."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from declutter_context import cache
from declutter_context.loadset import claude_loadset, codex_loadset
from declutter_context.model import Config, Level, Measurement, Project, Signal
from declutter_context.signals_files import (
    s1_instruction_lines,
    s2_startup_tokens,
    s3_codex_budget,
    s4_changelog_ratio,
    s5_memory,
)
from declutter_context.signals_repo import (
    s6_compactions,
    s7_state_freshness,
    s8_branches_worktrees,
    s9_docs_without_status,
)

SignalFn = Callable[[Project, Config], Signal]

ALL_SIGNALS: tuple[SignalFn, ...] = (
    s1_instruction_lines,
    s2_startup_tokens,
    s3_codex_budget,
    s4_changelog_ratio,
    s5_memory,
    s6_compactions,
    s7_state_freshness,
    s8_branches_worktrees,
    s9_docs_without_status,
)
SIGNAL_IDS: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")


def measure(project: Project, cfg: Config) -> Measurement:
    """Run every signal in order; a failing signal becomes an NA signal, never a crash.

    The signal functions are looked up in ``ALL_SIGNALS`` at call time.
    """
    out: list[Signal] = []
    for sid, fn in zip(SIGNAL_IDS, ALL_SIGNALS, strict=True):
        try:
            out.append(fn(project, cfg))
        except Exception as exc:
            out.append(
                Signal(
                    id=sid,
                    name=sid,
                    value=None,
                    unit="",
                    level=Level.NA,
                    yellow=0.0,
                    red=0.0,
                    detail=f"error: {type(exc).__name__}",
                )
            )
    return Measurement(project=project, signals=tuple(out))


def _signal_to_dict(s: Signal) -> dict[str, object]:
    return {
        "id": s.id,
        "name": s.name,
        "value": s.value,
        "unit": s.unit,
        "level": s.level.name,
        "yellow": s.yellow,
        "red": s.red,
        "detail": s.detail,
        "action": s.action,
    }


def to_dict(m: Measurement) -> dict[str, object]:
    """Serialize a measurement's signals to a JSON-compatible dict."""
    return {"signals": [_signal_to_dict(s) for s in m.signals]}


def _num(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ValueError("expected a number")
    return float(v)


def _signal_from_dict(d: object) -> Signal:
    if not isinstance(d, dict):
        raise ValueError("signal must be an object")
    level_name = d["level"]
    if not isinstance(level_name, str):
        raise ValueError("level must be a string")
    return Signal(
        id=str(d["id"]),
        name=str(d["name"]),
        value=_num(d.get("value")),
        unit=str(d.get("unit", "")),
        level=Level[level_name],
        yellow=float(_num(d.get("yellow")) or 0.0),
        red=float(_num(d.get("red")) or 0.0),
        detail=str(d.get("detail", "")),
        action=str(d.get("action", "")),
    )


def from_dict(d: dict[str, object], project: Project) -> Measurement:
    """Rebuild a measurement from ``to_dict`` output. Raises ValueError/KeyError if malformed."""
    raw = d.get("signals")
    if not isinstance(raw, list):
        raise ValueError("signals must be a list")
    return Measurement(project=project, signals=tuple(_signal_from_dict(x) for x in raw))


def _config_digest(cfg: Config) -> str:
    """Stable short hash of every config field that can change a measurement."""
    parts = [f"{k}={float(v[0])!r},{float(v[1])!r}" for k, v in sorted(cfg.thresholds.items())]
    parts += [
        f"bpt={cfg.bytes_per_token!r}",
        f"codex={cfg.codex_budget_bytes}",
        f"depth={cfg.import_max_depth}",
        f"mlines={cfg.memory_max_lines}",
        f"mbytes={cfg.memory_max_bytes}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _import_deps(project: Project, cfg: Config) -> dict[str, str]:
    """Stat markers of every file the load sets actually read (covers ``@import`` targets)."""
    deps: dict[str, str] = {}
    for ls in (claude_loadset(project, cfg), codex_loadset(project, cfg)):
        for f in ls.files:
            deps[str(f.path)] = cache.stat_marker(f.path)
    return deps


def _deps_fresh(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    return all(cache.stat_marker(Path(str(p))) == m for p, m in raw.items())


def cache_key(project: Project) -> str:
    """Cache key: one entry per (root, cwd, session cwd)."""
    return f"measure:{project.root}|{project.cwd}|{project.session_cwd or ''}"


def measure_cached(
    project: Project,
    cfg: Config,
    *,
    home: Path | None = None,
    ttl_s: float = 600,
) -> Measurement:
    """Like ``measure`` but reuses a fingerprint-validated on-disk cache entry.

    The fingerprint covers every candidate file of the load sets, the effective config
    (thresholds and limits, not ``lang``) and, through a stored dependency list, the
    ``@import`` targets read by the previous measurement.
    """
    key = cache_key(project)
    fp = cache.fingerprint(project) + ":cfg=" + _config_digest(cfg)
    hit = cache.get(key, fp, ttl_s=ttl_s, home=home)
    if hit is not None and _deps_fresh(hit.get("deps")):
        try:
            return from_dict(hit, project)
        except (KeyError, ValueError, TypeError):
            pass
    m = measure(project, cfg)
    try:
        deps = _import_deps(project, cfg)
    except Exception:
        deps = {}
    cache.put(key, fp, {**to_dict(m), "deps": deps}, home=home)
    return m
