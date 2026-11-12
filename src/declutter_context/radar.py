"""Multi-project radar: registry file, project discovery and per-project rows."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import cache, config, estado, fsutil, gitutil, measure
from .model import Config, Level, Measurement, Project, RadarRow, RegistryEntry

_PROJECT_MARKERS: tuple[str, ...] = (".git", "AGENTS.md", "CLAUDE.md")
_REGISTRY_KEYS = frozenset({"project"})
_ENTRY_KEYS = frozenset({"path", "name", "session"})
ACTIVITY_TTL_S = 600.0

_warnings: list[str] = []


def last_warnings() -> list[str]:
    """Problems found by the most recent :func:`load_registry` call (never raised)."""
    return list(_warnings)


def _measure(project: Project, cfg: Config, home: Path | None) -> Measurement:
    """Measure a project through the cached entry point (monkeypatch hook for tests)."""
    return measure.measure_cached(project, cfg, home=home)


def _expand(raw: str, home: Path | None) -> Path:
    """Expand a leading "~" against ``home`` (or the real home directory)."""
    if raw == "~" or raw.startswith("~/"):
        base = home if home is not None else Path.home()
        return base / raw[2:] if raw != "~" else base
    return Path(raw)


def load_registry(path: Path, *, home: Path | None = None) -> list[RegistryEntry]:
    """Read ``[[project]]`` tables from a TOML file. Missing/invalid file gives ``[]``.

    Entries without a usable ``path`` string are skipped. ``name`` defaults to the
    directory name; ``session`` is optional. Problems (unparseable file, unknown keys,
    entries without ``path``) are reported through :func:`last_warnings`.
    """
    _warnings.clear()
    text = fsutil.safe_read_text(path)
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        _warnings.append(f"registry {path}: cannot parse: {exc}")
        return []
    for key in sorted(set(data) - _REGISTRY_KEYS):
        _warnings.append(
            f"registry {path}: unknown key '{key}'; ignored (expected [[project]] tables)"
        )
    raw_projects = data.get("project")
    if raw_projects is None:
        return []
    if not isinstance(raw_projects, list):
        _warnings.append(f"registry {path}: 'project' must be [[project]] tables; ignored")
        return []
    entries: list[RegistryEntry] = []
    for n, item in enumerate(raw_projects, start=1):
        if not isinstance(item, dict):
            _warnings.append(f"registry {path}: project #{n} is not a table; ignored")
            continue
        for key in sorted(set(item) - _ENTRY_KEYS):
            _warnings.append(f"registry {path}: project #{n}: unknown key '{key}'; ignored")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            _warnings.append(f"registry {path}: project #{n} has no 'path' string; ignored")
            continue
        p = _expand(raw_path, home)
        raw_name = item.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else p.name
        raw_session = item.get("session")
        session = raw_session if isinstance(raw_session, str) and raw_session else None
        entries.append(RegistryEntry(path=p, name=name or str(p), session=session))
    return entries


_ESCAPES: dict[str, str] = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_str(value: str) -> str:
    """Encode ``value`` as a TOML basic string."""
    out: list[str] = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def save_registry(path: Path, entries: Sequence[RegistryEntry]) -> None:
    """Write the registry as TOML atomically, creating the parent directory."""
    lines: list[str] = ["# declutter project registry", ""]
    for e in entries:
        lines.append("[[project]]")
        lines.append(f"path = {_toml_str(str(e.path))}")
        lines.append(f"name = {_toml_str(e.name)}")
        if e.session:
            lines.append(f"session = {_toml_str(e.session)}")
        lines.append("")
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".toml")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _is_project_dir(d: Path) -> bool:
    return any((d / m).exists() for m in _PROJECT_MARKERS)


def discover(base: Path, *, max_depth: int = 3) -> list[Path]:
    """Find project directories under ``base`` (including ``base`` itself).

    A directory is a project if it contains ``.git``, ``AGENTS.md`` or ``CLAUDE.md``.
    The walk does not descend into a found project, into ``fsutil.SKIP_DIRS`` or
    through symlinked directories. Results are sorted.
    """
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(base, 0)]
    while stack:
        d, depth = stack.pop()
        if _is_project_dir(d):
            found.append(d)
            continue
        if depth >= max_depth:
            continue
        try:
            children = list(d.iterdir())
        except OSError:
            continue
        for c in children:
            if c.name in fsutil.SKIP_DIRS or c.is_symlink():
                continue
            if c.is_dir():
                stack.append((c, depth + 1))
    return sorted(found)


def _error_row(entry: RegistryEntry, message: str) -> RadarRow:
    return RadarRow(
        entry=entry,
        level=Level.NA,
        last_activity=None,
        dirty=None,
        ahead=None,
        estado=None,
        worst=None,
        error=message,
    )


def session_cwd(entry: RegistryEntry, home: Path | None = None) -> Path | None:
    """The Claude session folder named by ``entry.session``, if it is a path.

    ``session`` is the folder Claude Code sessions for this project are started from
    (absolute or ``~/...``). It selects the memory and transcript folder for S2, S5 and
    S6. Any other value is only a label shown in the radar.
    """
    raw = entry.session
    if not raw:
        return None
    if raw == "~" or raw.startswith("~/") or Path(raw).is_absolute():
        return _expand(raw, home)
    return None


def _activity_fingerprint(root: Path) -> str:
    git = root / ".git"
    markers = [
        cache.stat_marker(p)
        for p in (root, git / "HEAD", git / "index", git / "packed-refs", git / "refs" / "heads")
    ]
    return "|".join(markers)


def last_activity_ts(root: Path, *, home: Path | None = None) -> float | None:
    """Newest of the latest commit on any local branch and the newest file mtime.

    The file walk is the slow part, so the result is cached per project for
    ``ACTIVITY_TTL_S`` seconds and invalidated when the root folder or git refs change.
    """
    key = f"activity:{root}"
    fp = _activity_fingerprint(root)
    hit = cache.get(key, fp, ttl_s=ACTIVITY_TTL_S, home=home)
    if hit is not None:
        value = hit.get("ts")
        if value is None or (isinstance(value, int | float) and not isinstance(value, bool)):
            return None if value is None else float(value)
    stamps: list[float] = []
    if (root / ".git").exists():
        ts = gitutil.latest_ref_ts(root)
        if ts is not None:
            stamps.append(float(ts))
    mt = fsutil.newest_mtime(root)
    if mt is not None:
        stamps.append(mt)
    best = max(stamps) if stamps else None
    cache.put(key, fp, {"ts": best}, home=home)
    return best


def build_row(
    entry: RegistryEntry,
    *,
    home: Path | None = None,
    claude_home: Path | None = None,
) -> RadarRow:
    """Inspect one project. Any failure yields a row with level NA and an error text."""
    try:
        if not entry.path.is_dir():
            return _error_row(entry, "path not found")
        project = Project.at(
            entry.path,
            home=home,
            claude_home=claude_home,
            session_cwd=session_cwd(entry, home),
        )
        cfg = config.load_config(project.root, home=home)
        m = _measure(project, cfg, home)
        st = estado.load_estado(project.root)
        is_git = (project.root / ".git").exists()
        dirty = gitutil.dirty_count(project.root) if is_git else None
        ahead = gitutil.ahead_count(project.root) if is_git else None
        ts = last_activity_ts(project.root, home=home)
        last = datetime.fromtimestamp(ts) if ts is not None else None
        return RadarRow(
            entry=entry,
            level=m.level,
            last_activity=last,
            dirty=dirty,
            ahead=ahead,
            estado=st,
            worst=m.worst,
        )
    except Exception as exc:
        return _error_row(entry, f"{type(exc).__name__}: {exc}")


def build_rows(
    entries: Sequence[RegistryEntry],
    *,
    home: Path | None = None,
    claude_home: Path | None = None,
    max_workers: int = 8,
) -> list[RadarRow]:
    """Build rows concurrently, preserving the input order."""
    if not entries:
        return []
    workers = max(1, min(max_workers, len(entries)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda e: build_row(e, home=home, claude_home=claude_home), entries))
