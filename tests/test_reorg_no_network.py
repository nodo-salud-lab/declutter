"""No network anywhere in ``reorg`` and ``ratchet`` (spec §15.1 item 15).

``socket.socket``, ``socket.create_connection`` and ``socket.getaddrinfo`` are replaced by
functions that raise ``AssertionError``; then the whole command-line flow runs: ``plan``,
``check``, ``set``, ``apply --commit --yes``, ``verify``, ``undo`` and ``ratchet check``.
Git runs as a child process (``subprocess``), which needs no socket in this process.
"""

from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import cli
from declutter_context.reorg import manifest as mf
from test_apply import BASE, G2, K_L4, NOTE_L4, commit_all

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"


def _no_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("network access attempted")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr(cli, "_now", lambda: NOW)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)


def test_guard_really_blocks(no_network: None) -> None:
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("localhost", 80)
    with pytest.raises(AssertionError):
        socket.socket()


def test_no_network_anywhere_in_reorg_and_ratchet(
    make_project: MakeProject, no_network: None
) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes((BASE + NOTE_L4 + K_L4 + G2).encode())
    commit_all(project.root)
    root = str(project.root)

    def run(*argv: str) -> int:
        return cli.main(list(argv))

    assert run("reorg", "plan", "-C", root) == 0
    assert run("reorg", "check", RID, "-C", root) == 4  # one unit waits for acceptance
    m = mf.loads((project.root / mf.RUN_DIR / RID / mf.MANIFEST_NAME).read_bytes())
    for uid in mf.pending_units(m):
        assert run("reorg", "set", RID, uid, "-C", root, "--accept", "--reason", "revisada") == 0
    assert run("reorg", "check", RID, "-C", root) == 0
    assert run("reorg", "apply", RID, "-C", root, "--commit", "--yes") == 0
    assert run("reorg", "verify", RID, "-C", root) == 0
    assert run("reorg", "verify", RID, "-C", root, "--rev", "HEAD", "--strict") == 0
    assert run("ratchet", "check", "-C", root) == 0
    assert run("reorg", "status", "-C", root) == 0
    assert run("reorg", "undo", RID, "-C", root, "--yes") == 0
    assert run("reorg", "plan", "-C", root, "--dry-run") == 0
