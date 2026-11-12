"""``declutter reorg distill-check``: flags, exit codes, JSON, i18n and read-only behaviour
(fase 2b §5.7, §5.8, §12, §15.1 items 21-22). Synthetic projects only."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from _distill_helpers import INV_ES, RID, SEED, Fixture, applied, fill, fill_all
from conftest import MakeProject
from declutter_context import cli, ratchet


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


def _run(fx: Fixture, *extra: str) -> int:
    return cli.main(["reorg", "distill-check", RID, "-C", str(fx.root), *extra])


def _tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def fx(make_project: MakeProject) -> Fixture:
    f = applied(make_project)
    fill_all(f)
    return f


def test_json_ready_exit_0_and_nothing_written(
    fx: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _tree(fx.root)
    assert _run(fx, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "declutter.distill-check/1"
    assert (data["lang"], data["paths_lang"], data["code"]) == ("es", "es", 0)
    assert [c["state"] for c in data["cards"]] == ["ready"] * 3
    assert _tree(fx.root) == before


def test_text_not_ready_exit_4_without_content(
    fx: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    fill(fx, "D-001", [INV_ES["D-001"][0].replace(f" {SEED}", "")])
    assert _run(fx) == 4
    out = capsys.readouterr()
    assert "D-001 U02 not_ready" in out.out
    assert "token_missing" in out.out and "ident CLAUDE.md:" in out.out
    assert "no_card:L5m" in out.out
    assert SEED not in out.out + out.err


def test_card_flag(fx: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(fx, "--card", "D-003", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["card_id"] for c in data["cards"]] == ["D-003"]


@pytest.mark.parametrize(
    "extra",
    [("--card", "D-099"), ("--card", "X-1"), ("--lang", "fr")],
)
def test_usage_errors_exit_1(fx: Fixture, extra: tuple[str, ...]) -> None:
    assert _run(fx, *extra) == 1


def test_missing_run_exit_1(make_project: MakeProject, capsys: pytest.CaptureFixture[str]) -> None:
    project = make_project()
    assert cli.main(["reorg", "distill-check", RID, "-C", str(project.root)]) == 1
    assert "run_missing" in capsys.readouterr().err


def test_invalid_ratchet_log_exit_2(fx: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    log = fx.root / ratchet.LOG_REL
    log.write_bytes(log.read_bytes() + b'{"event": "nope"}\n')
    assert _run(fx) == 2
    assert "control_invalid" in capsys.readouterr().err


def test_secret_exit_5_without_value(fx: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    value = "AKIA" + "ABCDEFGHIJKLMNOP"
    fill(fx, "D-002", [*INV_ES["D-002"], value])
    assert _run(fx, "--json") == 5
    out = capsys.readouterr().out
    assert value not in out
    assert json.loads(out)["code"] == 5


def test_lang_en_messages(fx: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    fill(fx, "D-001", what="PENDIENTE")
    assert _run(fx, "--lang", "en") == 4
    out = capsys.readouterr().out
    assert "[not ready]" in out and "PENDING in «What»" in out
    assert _run(fx, "--lang", "es") == 4
    out = capsys.readouterr().out
    assert "[no lista]" in out and "PENDIENTE en «Qué»" in out


def test_english_run(make_project: MakeProject, capsys: pytest.CaptureFixture[str]) -> None:
    f = applied(make_project, "en")
    assert cli.main(["reorg", "distill-check", RID, "-C", str(f.root), "--json"]) == 4
    data = json.loads(capsys.readouterr().out)
    assert (data["lang"], data["paths_lang"]) == ("en", "en")
    keys = {r["key"] for r in data["cards"][0]["reasons"]}
    assert {"pending_what", "pending_invariant"} <= keys
    assert data["cards"][0]["path"].startswith("docs/decisions/")


def _no_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("network access attempted")


def test_no_network(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    assert _run(fx) == 0
    assert _run(fx, "--json", "--card", "D-002") == 0
