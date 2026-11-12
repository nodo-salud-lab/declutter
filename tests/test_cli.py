"""Tests for the command-line interface (exit codes, output, side effects)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import __version__, cli

RED_FILES = {"CLAUDE.md": "line\n" * 500, "AGENTS.md": "x\n"}
GREEN_FILES = {
    "CLAUDE.md": "# Rules\n- one\n",
    "AGENTS.md": "# Agents\n",
    "STATE.md": f"# State\nUpdated: {date.today().isoformat()}\n",
}


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


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"declutter {__version__}"


def test_no_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 1
    assert "usage" in capsys.readouterr().err.lower()


def test_unknown_option_maps_to_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["measure", "--bogus"]) == 1
    assert "unrecognized" in capsys.readouterr().err


def test_bad_choice_maps_to_exit_1() -> None:
    assert cli.main(["measure", "--lang", "fr"]) == 1


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "measure" in capsys.readouterr().out


def test_measure_text_green(make_project: MakeProject, capsys: pytest.CaptureFixture[str]) -> None:
    p = make_project(GREEN_FILES)
    assert cli.main(["measure", str(p.root)]) == 0
    assert "S1" in capsys.readouterr().out


def test_measure_json(make_project: MakeProject, capsys: pytest.CaptureFixture[str]) -> None:
    p = make_project(RED_FILES)
    assert cli.main(["measure", str(p.root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "signals" in data


def test_measure_lang_es(make_project: MakeProject, capsys: pytest.CaptureFixture[str]) -> None:
    p = make_project(RED_FILES)
    assert cli.main(["measure", str(p.root), "--lang", "es"]) == 0
    assert "líneas" in capsys.readouterr().out


def test_measure_fail_on_red(make_project: MakeProject) -> None:
    p = make_project(RED_FILES)
    assert cli.main(["measure", str(p.root), "--fail-on", "red"]) == 3
    assert cli.main(["measure", str(p.root), "--fail-on", "yellow"]) == 3


def test_measure_fail_on_not_reached(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    assert cli.main(["measure", str(p.root), "--fail-on", "red"]) == 0
    assert cli.main(["measure", str(p.root), "--fail-on", "yellow"]) == 0


def test_measure_yellow_only(make_project: MakeProject) -> None:
    files = dict(GREEN_FILES)
    files["CLAUDE.md"] = "line\n" * 250  # S1 yellow (>200, <=400)
    p = make_project(files)
    assert cli.main(["measure", str(p.root), "--fail-on", "red"]) == 0
    assert cli.main(["measure", str(p.root), "--fail-on", "yellow"]) == 3


def test_measure_missing_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["measure", str(tmp_path / "nope")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_measure_does_not_modify_project(make_project: MakeProject) -> None:
    p = make_project(RED_FILES)
    before = sorted(x.name for x in p.root.iterdir())
    cli.main(["measure", str(p.root)])
    assert sorted(x.name for x in p.root.iterdir()) == before


def test_internal_error_exit_2(
    monkeypatch: pytest.MonkeyPatch,
    make_project: MakeProject,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("kaput")

    monkeypatch.setattr(cli.measure_mod, "measure", boom)
    p = make_project(GREEN_FILES)
    assert cli.main(["measure", str(p.root)]) == 2
    assert "kaput" in capsys.readouterr().err


def test_radar_empty_registry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = tmp_path / "reg.toml"
    assert cli.main(["radar", "--registry", str(reg)]) == 0
    assert "--discover" in capsys.readouterr().out


def test_radar_discover_without_save(
    make_project: MakeProject, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = make_project(GREEN_FILES)
    reg = tmp_path / "reg.toml"
    assert cli.main(["radar", "--registry", str(reg), "--discover", str(p.root.parent)]) == 0
    out = capsys.readouterr().out
    assert str(p.root) in out
    assert not reg.exists()


def test_radar_discover_save_then_skip_known(
    make_project: MakeProject, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = make_project(GREEN_FILES)
    reg = tmp_path / "reg.toml"
    args = ["radar", "--registry", str(reg), "--discover", str(p.root.parent)]
    assert cli.main([*args, "--save"]) == 0
    assert reg.exists()
    capsys.readouterr()
    assert cli.main(args) == 0
    assert "No new projects" in capsys.readouterr().out


def test_radar_discover_bad_dir(tmp_path: Path) -> None:
    assert cli.main(["radar", "--registry", str(tmp_path / "r.toml"), "--discover", "/nope/x"]) == 1


def _register(make_project: MakeProject, tmp_path: Path, files: dict[str, str]) -> Path:
    p = make_project(files)
    reg = tmp_path / "reg.toml"
    assert cli.main(["radar", "--registry", str(reg), "--discover", str(p.root), "--save"]) == 0
    return reg


def test_radar_text(
    make_project: MakeProject, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = _register(make_project, tmp_path, GREEN_FILES)
    capsys.readouterr()
    assert cli.main(["radar", "--registry", str(reg)]) == 0
    assert "proj" in capsys.readouterr().out


def test_radar_json(
    make_project: MakeProject, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = _register(make_project, tmp_path, GREEN_FILES)
    capsys.readouterr()
    assert cli.main(["radar", "--registry", str(reg), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list | dict)


def test_radar_md_default_and_explicit(
    make_project: MakeProject,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _register(make_project, tmp_path, GREEN_FILES)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.chdir(out_dir)
    capsys.readouterr()
    assert cli.main(["radar", "--registry", str(reg), "--md"]) == 0
    assert (out_dir / "RADAR.md").is_file()
    assert "RADAR.md" in capsys.readouterr().out
    target = out_dir / "custom.md"
    assert cli.main(["radar", "--registry", str(reg), "--md", str(target)]) == 0
    assert target.is_file()


def test_init_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    assert cli.main(["init", str(root), "--dry-run", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "AGENTS.md" in out
    assert list(root.iterdir()) == []


def test_init_creates_and_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    assert cli.main(["init", str(root), "--lang", "es"]) == 0
    assert (root / "AGENTS.md").is_file()
    assert (root / "ESTADO.md").is_file()
    capsys.readouterr()
    assert cli.main(["init", str(root), "--lang", "es"]) == 0
    assert "Nada que crear" in capsys.readouterr().out


def test_init_default_lang_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "es_CL.UTF-8")
    root = tmp_path / "fresh"
    root.mkdir()
    assert cli.main(["init", str(root)]) == 0
    assert (root / "ESTADO.md").is_file()


def test_init_big_claude_md_note_and_untouched(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    p = make_project({"CLAUDE.md": "line\n" * 500})
    before = (p.root / "CLAUDE.md").read_text(encoding="utf-8")
    assert cli.main(["init", str(p.root), "--lang", "en"]) == 0
    assert "phase-2" in capsys.readouterr().out
    assert (p.root / "CLAUDE.md").read_text(encoding="utf-8") == before


def test_init_small_claude_md_no_note(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    p = make_project({"CLAUDE.md": "# hi\n"})
    assert cli.main(["init", str(p.root), "--lang", "en"]) == 0
    assert "phase-2" not in capsys.readouterr().out


def test_init_bad_path(tmp_path: Path) -> None:
    assert cli.main(["init", str(tmp_path / "nope")]) == 1


def test_hook_session_start_red_prints_line(
    make_project: MakeProject,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    p = make_project(RED_FILES)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(p.root)})))
    assert cli.main(["hook", "session-start"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("⚠️ declutter")
    assert out.count("\n") == 1


def test_hook_session_start_green_silent(
    make_project: MakeProject,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    p = make_project(GREEN_FILES)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(p.root)})))
    assert cli.main(["hook", "session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_hook_garbage_stdin_exit_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert cli.main(["hook", "session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_hook_missing_name_is_usage_error() -> None:
    assert cli.main(["hook"]) == 1


def test_hook_survives_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(_t: str) -> str:
        raise RuntimeError("x")

    monkeypatch.setattr(cli.hooks, "session_start", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert cli.main(["hook", "session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_statusline(
    make_project: MakeProject,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    p = make_project(RED_FILES)
    payload = {"workspace": {"current_dir": str(p.root)}, "context_window": {"used_percentage": 42}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["statusline"]) == 0
    out = capsys.readouterr().out
    assert "ctx 42%" in out
    assert "S1" in out


def test_statusline_garbage_exit_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["statusline"]) == 0
    assert capsys.readouterr().out == ""


def test_python_dash_m_version() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    res = subprocess.run(
        [sys.executable, "-m", "declutter_context", "--version"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(src), "PATH": ""},
        check=False,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == f"declutter {__version__}"
