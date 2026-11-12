"""Byte-exact safe read and atomic write (spec §4.1, §11.2 step 6.3, §13)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from conftest import MakeProject
from declutter_context import fsutil
from declutter_context.fsutil import (
    UnsafeReadError,
    UnsafeWriteError,
    atomic_write_bytes,
    safe_read_bytes,
)


def _temps(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if ".declutter-tmp-" in p.name]


def test_safe_read_bytes_returns_exact_bytes(make_project: MakeProject) -> None:
    root = make_project().root
    data = b"a\r\nb\xef\xbb\xbf"
    (root / "CLAUDE.md").write_bytes(data)
    assert safe_read_bytes(root / "CLAUDE.md", root) == data


def test_safe_read_bytes_returns_non_utf8_bytes_undecoded(make_project: MakeProject) -> None:
    root = make_project().root
    data = b"\xff\xfe\x00raw"
    (root / "x.md").write_bytes(data)
    assert safe_read_bytes(root / "x.md", root) == data


def test_safe_read_bytes_too_large_never_truncates(make_project: MakeProject) -> None:
    root = make_project().root
    (root / "big.md").write_bytes(b"12345")
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "big.md", root, max_bytes=4)
    assert exc.value.reason == "too_large"
    assert exc.value.path == root / "big.md"
    (root / "ok.md").write_bytes(b"1234")
    assert safe_read_bytes(root / "ok.md", root, max_bytes=4) == b"1234"


def test_safe_read_bytes_default_limit_read_at_call_time(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project().root
    (root / "x.md").write_bytes(b"abc")
    monkeypatch.setattr(fsutil, "MAX_READ_BYTES", 2)
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "x.md", root)
    assert exc.value.reason == "too_large"


def test_safe_read_bytes_fifo_not_regular(make_project: MakeProject) -> None:
    root = make_project().root
    fifo = root / "pipe.md"
    os.mkfifo(fifo)
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            safe_read_bytes(fifo, root)
        except UnsafeReadError as e:
            result["reason"] = e.reason

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "safe_read_bytes blocked on a FIFO"
    assert result.get("reason") == "not_regular"


def test_safe_read_bytes_symlink_to_env_is_secret(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project().root
    (root / ".env").write_bytes(b"SYNTHETIC=1\n")
    (root / "notes.md").symlink_to(root / ".env")
    opened: list[str] = []
    real_open = os.open

    def spy(path: Any, *args: Any, **kwargs: Any) -> int:
        opened.append(os.fspath(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "notes.md", root)
    assert exc.value.reason == "secret_path"
    with pytest.raises(UnsafeReadError) as exc2:
        safe_read_bytes(root / ".env", root)
    assert exc2.value.reason == "secret_path"
    assert not any(Path(p).name in {".env", "notes.md"} for p in opened)


def test_safe_read_bytes_outside_root(make_project: MakeProject, tmp_path: Path) -> None:
    root = make_project().root
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"x\n")
    (root / "link.md").symlink_to(outside)
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "link.md", root)
    assert exc.value.reason == "outside_root"
    # Without roots there is no confinement.
    assert safe_read_bytes(root / "link.md") == b"x\n"


def test_safe_read_bytes_missing(make_project: MakeProject) -> None:
    root = make_project().root
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "nope.md", root)
    assert exc.value.reason == "missing"


def test_safe_read_bytes_dangling_symlink_is_not_regular(make_project: MakeProject) -> None:
    root = make_project().root
    (root / "dangling.md").symlink_to(root / "gone.md")
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "dangling.md", root)
    assert exc.value.reason == "not_regular"


def test_safe_read_bytes_io_error(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project().root
    (root / "x.md").write_bytes(b"abc")

    def boom(fd: int, n: int) -> bytes:
        raise OSError("synthetic")

    monkeypatch.setattr(os, "read", boom)
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "x.md", root)
    assert exc.value.reason == "io_error"


def test_unsafe_read_error_message_has_no_content(make_project: MakeProject) -> None:
    root = make_project().root
    (root / "big.md").write_bytes(b"ZQX-SEED-7731 body")
    with pytest.raises(UnsafeReadError) as exc:
        safe_read_bytes(root / "big.md", root, max_bytes=3)
    assert "ZQX-SEED-7731" not in str(exc.value)
    assert "too_large" in str(exc.value)


def test_atomic_write_bytes_writes_and_fsyncs(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project().root
    calls: list[int] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    data = b"line\r\n\xef\xbb\xbfno-final"
    target = root / "docs" / "historial" / "2026-10.md"
    atomic_write_bytes(target, data, root)
    assert target.read_bytes() == data
    assert len(calls) >= 2
    assert _temps(target.parent) == []


def test_atomic_write_bytes_replaces_existing_file(make_project: MakeProject) -> None:
    root = make_project({"CLAUDE.md": "old\n"}).root
    atomic_write_bytes(root / "CLAUDE.md", b"new\n", root)
    assert (root / "CLAUDE.md").read_bytes() == b"new\n"
    assert _temps(root) == []


def test_atomic_write_bytes_cleans_temp_on_error(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project().root

    def fail(src: Any, dst: Any) -> None:
        raise OSError("synthetic")

    monkeypatch.setattr(os, "replace", fail)
    target = root / "AGENTS.md"
    with pytest.raises(UnsafeWriteError) as exc:
        atomic_write_bytes(target, b"x\n", root)
    assert exc.value.reason == "io_error"
    assert not os.path.lexists(target)
    assert _temps(root) == []


def test_atomic_write_bytes_reraises_non_oserror_and_cleans(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project().root

    def interrupt(src: Any, dst: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_bytes(root / "AGENTS.md", b"x\n", root)
    assert _temps(root) == []


def test_atomic_write_bytes_rejects_symlink_destination(make_project: MakeProject) -> None:
    root = make_project({"docs/real.md": "r\n"}).root
    (root / "docs" / "x.md").symlink_to(root / "docs" / "real.md")
    with pytest.raises(UnsafeWriteError) as exc:
        atomic_write_bytes(root / "docs" / "x.md", b"y\n", root)
    assert exc.value.reason == "symlink"
    assert (root / "docs" / "real.md").read_bytes() == b"r\n"
    assert (root / "docs" / "x.md").is_symlink()


def test_atomic_write_bytes_replace_symlink_only_root_claude_md(
    make_project: MakeProject, tmp_path: Path
) -> None:
    root = make_project({"AGENTS.md": "agents\n", "docs/AGENTS.md": "z\n"}).root
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    atomic_write_bytes(root / "CLAUDE.md", b"@AGENTS.md\n", root, replace_symlink=True)
    assert not (root / "CLAUDE.md").is_symlink()
    assert (root / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n"
    assert (root / "AGENTS.md").read_bytes() == b"agents\n"

    # Not the root CLAUDE.md -> rejected even with replace_symlink.
    (root / "docs" / "CLAUDE.md").symlink_to("AGENTS.md")
    with pytest.raises(UnsafeWriteError) as exc:
        atomic_write_bytes(root / "docs" / "CLAUDE.md", b"x\n", root, replace_symlink=True)
    assert exc.value.reason == "symlink"

    # Root CLAUDE.md whose link resolves outside the root -> rejected.
    outside = tmp_path / "outside-agents.md"
    outside.write_bytes(b"o\n")
    (root / "CLAUDE.md").unlink()
    (root / "CLAUDE.md").symlink_to(outside)
    with pytest.raises(UnsafeWriteError) as exc2:
        atomic_write_bytes(root / "CLAUDE.md", b"x\n", root, replace_symlink=True)
    assert exc2.value.reason == "symlink"
    assert outside.read_bytes() == b"o\n"

    # Root CLAUDE.md symlink without replace_symlink -> rejected.
    (root / "CLAUDE.md").unlink()
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    with pytest.raises(UnsafeWriteError) as exc3:
        atomic_write_bytes(root / "CLAUDE.md", b"x\n", root)
    assert exc3.value.reason == "symlink"


def test_atomic_write_bytes_rejects_parent_outside_root(
    make_project: MakeProject, tmp_path: Path
) -> None:
    root = make_project().root
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "docs").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(UnsafeWriteError) as exc:
        atomic_write_bytes(root / "docs" / "sub" / "x.md", b"x\n", root)
    assert exc.value.reason == "outside_root"
    assert list(elsewhere.iterdir()) == []


def test_atomic_write_bytes_rejects_secret_name(make_project: MakeProject) -> None:
    root = make_project().root
    with pytest.raises(UnsafeWriteError) as exc:
        atomic_write_bytes(root / ".env", b"X=1\n", root)
    assert exc.value.reason == "secret_path"
    assert not os.path.lexists(root / ".env")


def test_atomic_write_bytes_rejects_non_regular_destination(make_project: MakeProject) -> None:
    root = make_project().root
    (root / "dir.md").mkdir()
    with pytest.raises(UnsafeWriteError) as exc:
        atomic_write_bytes(root / "dir.md", b"x\n", root)
    assert exc.value.reason == "not_regular"


def test_atomic_write_bytes_keeps_existing_mode(make_project: MakeProject) -> None:
    root = make_project({"run.md": "a\n"}).root
    (root / "run.md").chmod(0o600)
    atomic_write_bytes(root / "run.md", b"b\n", root)
    assert (root / "run.md").stat().st_mode & 0o777 == 0o600
