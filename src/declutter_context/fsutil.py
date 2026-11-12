"""Safe filesystem helpers. Every read in declutter goes through here."""

from __future__ import annotations

import contextlib
import fnmatch
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SECRET_PATTERNS: tuple[str, ...] = (".env", ".env.*", "*.pem", "*.key", "id_*", "secrets*")
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "coverage",
    }
)

# Upper bound on how much of one file is decoded into memory. Larger files are read
# only up to this size; their full size and line count come from ``safe_text_stats``.
MAX_READ_BYTES = 8 * 1024 * 1024
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class TextStats:
    """Size of a text file measured by streaming its bytes."""

    bytes: int
    lines: int


def is_secret_path(path: Path) -> bool:
    """True if the file name looks like a secret. Such files are never read."""
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS)


def is_within(path: Path, base: Path) -> bool:
    """True if ``path`` (after resolving symlinks) is inside ``base``."""
    try:
        path.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return False
    return True


def _allowed(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    """Shared gate: not a secret (by link name or resolved target) and inside a root."""
    if is_secret_path(path):
        return False
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    if is_secret_path(resolved):
        return False
    return not allowed_roots or any(is_within(path, r) for r in allowed_roots)


def _open_regular(path: Path) -> int | None:
    """Open ``path`` read-only without blocking and return the fd if it is a regular file.

    FIFOs, sockets and devices are rejected before any read, so they can never hang a
    hook or the status line.
    """
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
        fd = os.open(path, flags)
    except (OSError, ValueError):
        return None
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def safe_read_text(path: Path, *allowed_roots: Path, max_bytes: int | None = None) -> str | None:
    """Read a text file, or return None.

    Returns None if the file is missing, not a regular file (FIFO, device...), unreadable,
    looks like a secret (by its own name or the name of its resolved symlink target), or
    resolves outside every ``allowed_roots`` entry. At most ``max_bytes`` bytes (default:
    ``MAX_READ_BYTES``, looked up at call time) are read;
    use :func:`safe_text_stats` for the size of larger files. Undecodable bytes are
    replaced, never raised.
    """
    if not _allowed(path, allowed_roots):
        return None
    fd = _open_regular(path)
    if fd is None:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(max(0, MAX_READ_BYTES if max_bytes is None else max_bytes))
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


ReadReason = Literal[
    "missing", "not_regular", "secret_path", "outside_root", "too_large", "io_error"
]
WriteReason = Literal["outside_root", "symlink", "secret_path", "not_regular", "io_error"]


class UnsafeReadError(Exception):
    """A strict byte read was refused. The message carries the reason and path, never content."""

    def __init__(self, reason: ReadReason, path: Path) -> None:
        super().__init__(f"{reason}: {path}")
        self.reason: ReadReason = reason
        self.path = path


class UnsafeWriteError(Exception):
    """An atomic write was refused or failed. The message carries reason and path only."""

    def __init__(self, reason: WriteReason, path: Path) -> None:
        super().__init__(f"{reason}: {path}")
        self.reason: WriteReason = reason
        self.path = path


def safe_read_bytes(path: Path, *allowed_roots: Path, max_bytes: int | None = None) -> bytes:
    """Read a file's raw bytes or raise :class:`UnsafeReadError`. Never truncates or decodes.

    Same gate as :func:`safe_read_text` (secret name of the link or of its resolved target,
    confinement to ``allowed_roots``, regular files opened without blocking), but every
    refusal is an exception with a reason. At most ``max_bytes`` bytes are accepted
    (default: ``MAX_READ_BYTES``, looked up at call time); a larger file is ``too_large``.
    """
    if not os.path.lexists(path):
        raise UnsafeReadError("missing", path)
    if is_secret_path(path):
        raise UnsafeReadError("secret_path", path)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        raise UnsafeReadError("io_error", path) from None
    if is_secret_path(resolved):
        raise UnsafeReadError("secret_path", path)
    if allowed_roots and not any(is_within(path, r) for r in allowed_roots):
        raise UnsafeReadError("outside_root", path)
    fd = _open_regular(path)
    if fd is None:
        raise UnsafeReadError("not_regular", path)
    limit = max(0, MAX_READ_BYTES if max_bytes is None else max_bytes)
    chunks: list[bytes] = []
    got = 0
    try:
        try:
            while got <= limit:
                chunk = os.read(fd, min(_CHUNK, limit + 1 - got))
                if not chunk:
                    break
                chunks.append(chunk)
                got += len(chunk)
        finally:
            os.close(fd)
    except OSError:
        raise UnsafeReadError("io_error", path) from None
    if got > limit:
        raise UnsafeReadError("too_large", path)
    return b"".join(chunks)


def _nearest_existing(path: Path) -> Path:
    """``path`` or its closest ancestor that exists (``lexists``)."""
    p = path
    while not os.path.lexists(p) and p.parent != p:
        p = p.parent
    return p


def _fsync_dir(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(
    path: Path, data: bytes, root: Path, *, replace_symlink: bool = False
) -> None:
    """Write ``data`` to ``path`` atomically, confined to ``root``.

    Refuses secret names, a parent that resolves outside ``root`` and an existing
    destination that is a symlink. The only symlink accepted (with ``replace_symlink``)
    is the root ``CLAUDE.md`` whose link resolves inside ``root``: the link itself is
    replaced by a regular file and its target is left untouched. Writes a sibling temp
    file ``.<name>.declutter-tmp-<pid>``, fsyncs it, ``os.replace``s it over ``path`` and fsyncs
    the folder. On any failure the temp file is removed; an ``OSError`` is re-raised as
    ``UnsafeWriteError("io_error")``, anything else (e.g. ``KeyboardInterrupt``) as is.
    """
    if is_secret_path(path):
        raise UnsafeWriteError("secret_path", path)
    if not is_within(_nearest_existing(path.parent), root):
        raise UnsafeWriteError("outside_root", path)
    mode = 0o666
    if os.path.lexists(path):
        if path.is_symlink():
            ok = (
                replace_symlink
                and path.name == "CLAUDE.md"
                and _same_dir(path.parent, root)
                and is_within(path, root)
            )
            if not ok:
                raise UnsafeWriteError("symlink", path)
        else:
            try:
                st = path.lstat()
            except OSError:
                raise UnsafeWriteError("io_error", path) from None
            if not stat.S_ISREG(st.st_mode):
                raise UnsafeWriteError("not_regular", path)
            mode = stat.S_IMODE(st.st_mode)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise UnsafeWriteError("io_error", path) from None
    if not is_within(path.parent, root):
        raise UnsafeWriteError("outside_root", path)
    tmp = path.parent / f".{path.name}.declutter-tmp-{os.getpid()}"
    created = False
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        created = True
        try:
            if mode != 0o666:
                os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)  # noqa: PTH105 (spec §11.2 names os.replace; tests patch it)
        created = False
        _fsync_dir(path.parent)
    except BaseException as exc:
        if created:
            with contextlib.suppress(OSError):
                tmp.unlink()
        if isinstance(exc, OSError):
            raise UnsafeWriteError("io_error", path) from None
        raise


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


def safe_text_stats(path: Path, *allowed_roots: Path) -> TextStats | None:
    """Byte size and line count of a text file, streamed in binary with bounded memory.

    Same gate as :func:`safe_read_text`. Lines are counted like ``str.splitlines`` for
    ``\\n`` endings: a final line without a newline still counts.
    """
    if not _allowed(path, allowed_roots):
        return None
    fd = _open_regular(path)
    if fd is None:
        return None
    total = 0
    newlines = 0
    last = b""
    try:
        with os.fdopen(fd, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                newlines += chunk.count(b"\n")
                last = chunk[-1:]
    except OSError:
        return None
    lines = newlines + (1 if total and last != b"\n" else 0)
    return TextStats(bytes=total, lines=lines)


def is_dir(path: Path) -> bool:
    """``path.is_dir()`` that returns False on any OSError (e.g. a too-long name on 3.11)."""
    try:
        return path.is_dir()
    except OSError:
        return False


def is_file(path: Path) -> bool:
    """``path.is_file()`` that returns False on any OSError (e.g. a too-long name on 3.11)."""
    try:
        return path.is_file()
    except OSError:
        return False


def file_size(path: Path) -> int:
    """Size in bytes of ``path`` (following symlinks), or 0 if it cannot be stat'ed."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def iter_files(root: Path, pattern: str = "*") -> Iterator[Path]:
    """Yield regular files under ``root`` matching ``pattern``.

    ``SKIP_DIRS`` are skipped. Symlinked directories are never descended into (this rules
    out symlink cycles), and symlinked files are yielded only when they resolve inside
    ``root``.
    """
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    p = Path(e.path)
                    if e.is_file() and is_within(p, root) and fnmatch.fnmatch(e.name, pattern):
                        yield p
                    continue
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False) and fnmatch.fnmatch(e.name, pattern):
                    yield Path(e.path)
            except OSError:
                continue


def newest_mtime(root: Path) -> float | None:
    """Most recent mtime of any file under root (SKIP_DIRS excluded), or None."""
    best: float | None = None
    for f in iter_files(root):
        with contextlib.suppress(OSError):
            m = f.stat().st_mtime
            if best is None or m > best:
                best = m
    return best
