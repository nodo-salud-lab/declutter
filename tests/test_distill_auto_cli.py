"""CLI of distill-auto and distill-undo --auto (fase 3A §5, §8, §11.14)."""

from __future__ import annotations

import io
import json

import pytest

from _distill_helpers import RID, Fixture, applied, fill
from conftest import MakeProject
from declutter_context import cli


class _NoStdin(io.StringIO):
    def read(self, *a: object) -> str:  # type: ignore[override]
        raise AssertionError("stdin read")

    def readline(self, *a: object) -> str:  # type: ignore[override]
        raise AssertionError("stdin read")


@pytest.fixture
def fx(make_project: MakeProject) -> Fixture:
    return applied(make_project)


def test_parser_has_distill_auto_and_undo_auto() -> None:
    p = cli._build_parser()
    a = p.parse_args(["reorg", "distill-auto", RID, "--skip", "D-002", "--json", "--dry-run"])
    assert a.skip == ["D-002"] and a.json and a.dry_run
    u = p.parse_args(["reorg", "distill-undo", RID, "--auto", "3"])
    assert u.auto == 3 and u.card is None
    with pytest.raises(SystemExit):
        p.parse_args(["reorg", "distill-undo", RID, "--auto", "3", "--card", "D-001"])
    with pytest.raises(SystemExit):
        p.parse_args(["reorg", "distill-undo", RID])
    with pytest.raises(SystemExit):
        p.parse_args(["reorg", "distill-auto", RID, "--yes"])
    with pytest.raises(SystemExit):
        p.parse_args(["reorg", "distill-auto", RID, "--card", "D-001"])
    with pytest.raises(SystemExit):
        p.parse_args(["reorg", "distill-auto", RID, "--no-git"])


# 14
def test_distill_auto_never_reads_stdin_and_json_is_clean(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fill(fx, "D-001")
    monkeypatch.setattr("sys.stdin", _NoStdin())
    code = cli.main(["reorg", "distill-auto", RID, "-C", str(fx.root), "--json"])
    out = capsys.readouterr()
    assert code == 0
    obj = json.loads(out.out)
    assert obj["applied"][0]["card"] == "D-001"
    assert "[1/1]" in out.err


def test_distill_auto_without_json_prints_notices_on_stdout(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fill(fx, "D-001")
    monkeypatch.setattr("sys.stdin", _NoStdin())
    code = cli.main(["reorg", "distill-auto", RID, "-C", str(fx.root)])
    out = capsys.readouterr()
    assert code == 0
    assert "[1/1]" in out.out


def test_distill_undo_auto_unknown_batch_exits_1(
    fx: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["reorg", "distill-undo", RID, "-C", str(fx.root), "--auto", "9"])
    assert code == 1
    assert "no_batch" in capsys.readouterr().err


def test_json_without_a_report_emits_keys_only(
    fx: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["reorg", "distill-auto", RID, "-C", str(fx.root), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out) == {
        "schema": "declutter.distill-auto-report/1",
        "state": "nothing",
        "issues": [],
    }
    fill(fx, "D-001")
    code = cli.main(["reorg", "distill-auto", RID, "-C", str(fx.root), "--json", "--skip", "D-009"])
    obj = json.loads(capsys.readouterr().out)
    assert code == 1 and obj["state"] == "refused"
    assert obj["issues"] == ["distill.auto.skip_unknown"]


def test_dry_run_notice_on_start(fx: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    fill(fx, "D-001")
    cli.main(["reorg", "distill-auto", RID, "-C", str(fx.root), "--dry-run"])
    assert "Dry run: nothing is written" in capsys.readouterr().out
