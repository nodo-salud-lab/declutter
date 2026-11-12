"""``declutter reorg distill-apply`` / ``distill-undo`` / ``status`` through the CLI: exit codes,
no TTY, i18n (``lang = en``), the G7 header and no network (fase 2b §8-§9, §12, §13,
§15.1 items 12, 21, 22). Synthetic projects only."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from _distill_helpers import NOW, RID, SEED, Fixture, applied, fill
from conftest import MakeProject
from declutter_context import cli
from test_apply import FakePrompter


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr(cli, "_now", lambda: NOW)


def _tty(monkeypatch: pytest.MonkeyPatch, *answers: str) -> FakePrompter:
    p = FakePrompter(tty=True, answers=list(answers))
    monkeypatch.setattr(cli, "StdPrompter", lambda: p)
    return p


def _run(fx: Fixture, *argv: str) -> int:
    return cli.main([*argv[:2], *argv[2:], "-C", str(fx.root)])


def test_no_tty_exits_1(make_project: MakeProject, capsys: pytest.CaptureFixture[str]) -> None:
    fx = applied(make_project)
    fill(fx, "D-002")
    before = (fx.root / "AGENTS.md").read_bytes()
    assert _run(fx, "reorg", "distill-apply", RID, "--card", "D-002") == 1
    assert _run(fx, "reorg", "distill-apply", RID, "--card", "D-002", "--yes") == 1
    out = capsys.readouterr()
    assert f"{RID} D-002 planned" in out.out
    assert "distill.no_tty" in out.err
    assert (fx.root / "AGENTS.md").read_bytes() == before


def test_card_is_required(make_project: MakeProject) -> None:
    fx = applied(make_project)
    assert _run(fx, "reorg", "distill-apply", RID) == 1
    assert _run(fx, "reorg", "distill-undo", RID) == 1


def test_full_flow_and_status(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx = applied(make_project)
    fill(fx, "D-002")
    p = _tty(monkeypatch, "motivo", "confirmo", "s")
    assert _run(fx, "reorg", "distill-apply", RID, "--card", "D-002") == 0
    assert any(x.startswith("Commit") for x in p.prompts)  # TTY: the commit is asked
    out = capsys.readouterr().out
    assert f"{RID} D-002 committed" in out
    assert SEED not in out and SEED not in "\n".join(p.said)
    assert _run(fx, "reorg", "verify", RID, "--strict") == 0
    assert _run(fx, "reorg", "verify", RID, "--rev", "HEAD") == 0
    assert _run(fx, "ratchet", "check") == 0
    capsys.readouterr()
    assert _run(fx, "reorg", "status") == 0
    text = capsys.readouterr().out
    assert "  D-002 committed" in text and "2026-11-04" in text
    assert _run(fx, "reorg", "status", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["distills"][0]["card"] == "D-002"
    assert rows[0]["distills"][0]["state"] == "committed"
    assert _run(fx, "reorg", "distill-check", RID, "--card", "D-002") == 0
    assert _run(fx, "reorg", "distill-undo", RID, "--card", "D-002", "--yes") == 0
    assert _run(fx, "reorg", "verify", RID, "--strict") == 0


def test_english_run(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx = applied(make_project, "en")
    fill(
        fx,
        "D-001",
        what="Synthetic rule.",
        why="not stated",
        alternative="not stated",
        pitfall="not stated",
    )
    asked = _tty(monkeypatch, "synthetic reason", "confirm", "n")
    assert _run(fx, "reorg", "distill-apply", RID, "--card", "D-001", "--lang", "en") == 0
    assert any("Type 'confirm'" in x for x in asked.prompts)
    out = capsys.readouterr().out
    assert f"{RID} D-001 written" in out
    assert any("D-001 distilled" in x for x in asked.said)
    card = fx.card_path("D-001").read_text()
    assert card.split("\n")[1] == "state: distilled"
    agents = (fx.root / "AGENTS.md").read_text()
    assert "\n## Safety invariants (distilled)\n" in agents
    # G7: the header names («Safety invariants») a section title that exists.
    header = agents.split("\n")[1]
    named = header.split("«", 1)[1].split("»", 1)[0]
    titles = [x[3:] for x in agents.split("\n") if x.startswith("## ")]
    assert any(t.startswith(named) for t in titles)
    assert _run(fx, "reorg", "verify", RID, "--strict") == 0


def _no_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("network access attempted")


def test_no_network_in_distill(make_project: MakeProject, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = applied(make_project)
    fill(fx, "D-002")
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    _tty(monkeypatch, "motivo", "confirmo", "n")
    assert _run(fx, "reorg", "distill-check", RID, "--json") == 4  # D-001, D-003 unfilled
    assert _run(fx, "reorg", "distill-apply", RID, "--card", "D-002", "--dry-run") == 0
    assert _run(fx, "reorg", "distill-apply", RID, "--card", "D-002") == 0
    assert _run(fx, "reorg", "verify", RID) == 0
    assert _run(fx, "ratchet", "check") == 0
    assert _run(fx, "reorg", "status") == 0
    assert _run(fx, "reorg", "distill-undo", RID, "--card", "D-002", "--yes") == 0
