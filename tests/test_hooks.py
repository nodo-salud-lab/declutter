"""Tests for the SessionStart hook and status line entry points."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import cache, hooks
from declutter_context.model import Project


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


RED_FILES = {"CLAUDE.md": "line\n" * 500, "AGENTS.md": "x\n"}
GREEN_FILES = {
    "CLAUDE.md": "# Rules\n- one\n",
    "AGENTS.md": "# Agents\n",
    "STATE.md": f"# State\nUpdated: {date.today().isoformat()}\n",
}


def _payload(project: Project, **extra: object) -> str:
    return json.dumps({"cwd": str(project.cwd), "hook_event_name": "SessionStart", **extra})


def _log(project: Project) -> str:
    log = cache.cache_dir(project.home) / cache.LOG_NAME
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_session_start_red_prints_one_line(make_project: MakeProject) -> None:
    p = make_project(RED_FILES)
    out = hooks.session_start(_payload(p), home=p.home, claude_home=p.claude_home)
    assert out
    assert "\n" not in out
    assert out.startswith("⚠️ declutter: ")
    assert "Root instruction lines" in out
    assert "CLAUDE.md 500" in out
    assert out.endswith("(/declutter:declutter-hygiene)")
    assert "501 lines" in out


def test_session_start_translates_to_spanish(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "es_CL.UTF-8")
    p = make_project(RED_FILES)
    out = hooks.session_start(_payload(p), home=p.home, claude_home=p.claude_home)
    assert "Líneas de instrucción raíz" in out
    assert "\n" not in out


def test_session_start_green_is_silent(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    out = hooks.session_start(_payload(p), home=p.home, claude_home=p.claude_home)
    assert out == ""


def test_session_start_invalid_json_logs_and_returns_empty(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    assert hooks.session_start("{not json", home=p.home, claude_home=p.claude_home) == ""
    assert "session-start" in _log(p)


def test_session_start_non_object_json_logs(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    assert hooks.session_start("[1, 2]", home=p.home, claude_home=p.claude_home) == ""
    assert "session-start" in _log(p)


def test_session_start_empty_stdin_never_raises(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    assert hooks.session_start("", home=p.home, claude_home=p.claude_home) == ""


def test_session_start_uses_git_toplevel_from_subdir(make_project: MakeProject) -> None:
    p = make_project(RED_FILES, git=True)
    sub = p.root / "src" / "deep"
    sub.mkdir(parents=True)
    out = hooks.session_start(json.dumps({"cwd": str(sub)}), home=p.home, claude_home=p.claude_home)
    assert "CLAUDE.md 500" in out


def test_project_from_cwd_git_and_plain(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES, git=True)
    sub = p.root / "a"
    sub.mkdir()
    got = hooks.project_from_cwd(sub, home=p.home, claude_home=p.claude_home)
    assert got.root == p.root
    assert got.cwd == sub.resolve()
    plain = make_project({"CLAUDE.md": "x\n"})
    got2 = hooks.project_from_cwd(plain.root, home=plain.home, claude_home=plain.claude_home)
    assert got2.root == plain.root


def test_statusline_green(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    data = {"context_window": {"used_percentage": 42}, "workspace": {"current_dir": str(p.cwd)}}
    out = hooks.statusline(json.dumps(data), home=p.home, claude_home=p.claude_home)
    assert out == "ctx 42% · 🟢"


def test_statusline_red_with_detail(make_project: MakeProject) -> None:
    p = make_project(RED_FILES)
    data = {"context_window": {"used_percentage": 42.4}, "cwd": str(p.cwd)}
    out = hooks.statusline(json.dumps(data), home=p.home, claude_home=p.claude_home)
    assert out == "ctx 42% · 🔴 S1 501 lines"
    assert "\n" not in out


def test_statusline_truncates_detail(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "l\n" * 500, "AGENTS.md": ""})
    data = {"workspace": {"current_dir": str(p.cwd)}}
    out = hooks.statusline(json.dumps(data), home=p.home, claude_home=p.claude_home)
    label = out.split("🔴 ", 1)[1]
    assert len(label) <= 60


def test_statusline_without_percentage_omits_prefix(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    for ctx in ({"used_percentage": None}, {}, "oops", {"used_percentage": True}):
        data = {"context_window": ctx, "cwd": str(p.cwd)}
        out = hooks.statusline(json.dumps(data), home=p.home, claude_home=p.claude_home)
        assert out == "🟢"


def test_statusline_invalid_json_logs(make_project: MakeProject) -> None:
    p = make_project(GREEN_FILES)
    assert hooks.statusline("nope", home=p.home, claude_home=p.claude_home) == ""
    assert "statusline" in _log(p)


def test_statusline_second_call_is_fast(make_project: MakeProject) -> None:
    files = dict(GREEN_FILES)
    for i in range(300):
        files[f"docs/note-{i}.md"] = f"# Note {i}\nbody\n"
    p = make_project(files)
    stdin = json.dumps({"context_window": {"used_percentage": 10}, "cwd": str(p.cwd)})
    first = hooks.statusline(stdin, home=p.home, claude_home=p.claude_home)
    start = time.perf_counter()
    second = hooks.statusline(stdin, home=p.home, claude_home=p.claude_home)
    elapsed = time.perf_counter() - start
    assert first == second
    assert elapsed < 0.3
