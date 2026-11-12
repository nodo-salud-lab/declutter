"""Small on-disk cache keyed by a cheap fingerprint of the files that matter.

Used by hooks and the radar so the statusline stays fast. Nothing here ever raises
on I/O problems: a broken cache simply behaves like an empty one.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from declutter_context import fsutil
from declutter_context.model import Project

LOG_NAME = "hook.log"
LOG_MAX_BYTES = 256 * 1024
_MISSING = "missing"


def cache_dir(home: Path | None = None) -> Path:
    """Cache directory: ``$XDG_CACHE_HOME/declutter`` or ``<home>/.cache/declutter``."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "declutter"
    return (home or Path.home()) / ".cache" / "declutter"


def _stat_marker(path: Path) -> str:
    """``mtime_ns:size`` for an existing file, a fixed marker otherwise."""
    try:
        st = path.stat()
    except OSError:
        return _MISSING
    return f"{st.st_mtime_ns}:{st.st_size}"


def stat_marker(path: Path) -> str:
    """Public alias of the per-file marker used in fingerprints (``mtime_ns:size``)."""
    return _stat_marker(path)


def candidate_paths(project: Project) -> list[Path]:
    """Every file whose change can alter a measurement, whether it exists or not.

    Mirrors the Claude and Codex load sets (user and ancestor CLAUDE.md files, rules,
    auto-memory, AGENTS.md chain) plus the state file, project config and git markers.
    ``@import`` targets depend on file contents, so ``measure.measure_cached`` tracks
    them separately. Transcripts are deliberately excluded: they change on every message.
    """
    # Local import: loadset imports gitutil/fsutil; avoid any import-order surprises.
    from declutter_context import loadset

    root = project.root
    paths: list[Path] = [project.claude_home / "CLAUDE.md"]
    for d in loadset.ancestor_dirs(project):
        paths.extend((d / "CLAUDE.md", d / "CLAUDE.local.md"))
    for d in loadset.codex_chain(root, project.cwd):
        paths.extend((d / "AGENTS.override.md", d / "AGENTS.md"))
    paths.extend(
        root / name
        for name in ("CLAUDE.md", "AGENTS.md", "ESTADO.md", "STATE.md", "declutter.toml")
    )
    for rules_dir in (root / ".claude" / "rules", project.claude_home / "rules"):
        paths.append(rules_dir)  # directory mtime catches added/removed rules
        if fsutil.is_dir(rules_dir):
            paths.extend(sorted(fsutil.iter_files(rules_dir)))
    paths.append(loadset.memory_md_path(project))
    paths.extend((root / ".git" / "HEAD", root / ".git" / "index"))
    out: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def fingerprint(project: Project) -> str:
    """SHA-256 over (full path, mtime_ns, size) of every candidate path of the project.

    Each label is the full path, so a missing file in one cwd never matches a missing
    file in another.
    """
    h = hashlib.sha256()
    h.update(f"cwd\0{project.cwd}\0session\0{project.session_cwd}\n".encode())
    for path in candidate_paths(project):
        h.update(f"{path}\0{_stat_marker(path)}\n".encode())
    return h.hexdigest()


def _entry_path(key: str, home: Path | None) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return cache_dir(home) / f"{digest}.json"


def get(
    key: str,
    fp: str,
    *,
    ttl_s: float = 600,
    home: Path | None = None,
    now: float | None = None,
) -> dict[str, object] | None:
    """Return the cached data if the fingerprint matches and the entry is fresh, else None."""
    path = _entry_path(key, home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    ts = raw.get("ts")
    data = raw.get("data")
    if raw.get("fp") != fp or isinstance(ts, bool) or not isinstance(ts, int | float):
        return None
    if not isinstance(data, dict):
        return None
    current = time.time() if now is None else now
    if current - ts > ttl_s:
        return None
    return {str(k): v for k, v in data.items()}


def put(
    key: str,
    fp: str,
    data: Mapping[str, object],
    *,
    home: Path | None = None,
    now: float | None = None,
) -> None:
    """Atomically write a cache entry. Never raises."""
    directory = cache_dir(home)
    ts = time.time() if now is None else now
    tmp_name: str | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps({"fp": fp, "ts": ts, "data": dict(data)})
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        Path(tmp_name).replace(_entry_path(key, home))
        tmp_name = None
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink()


def log_error(message: str, *, home: Path | None = None) -> None:
    """Append one timestamped line to ``hook.log``, keeping it under 256 KB. Never raises."""
    directory = cache_dir(home)
    log = directory / LOG_NAME
    line = f"{datetime.now().isoformat(timespec='seconds')} {' '.join(message.split())}\n"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with log.open("ab") as fh:
            fh.write(line.encode("utf-8"))
        if log.stat().st_size > LOG_MAX_BYTES:
            blob = log.read_bytes()
            tail = blob[len(blob) // 2 :]
            nl = tail.find(b"\n")
            if nl != -1:
                tail = tail[nl + 1 :]  # drop the partial first line
            log.write_bytes(tail)
    except OSError:
        pass
