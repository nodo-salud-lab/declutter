"""Regression tests for review findings (slug, FIFOs, huge files, cache, symlinks, radar, init)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest

from conftest import MakeProject, write_tree
from declutter_context import cache, cli, fsutil, gitutil, hooks, loadset, measure, radar, scaffold
from declutter_context.i18n import MESSAGES
from declutter_context.model import (
    Config,
    Estado,
    EstadoItem,
    Level,
    Measurement,
    Project,
    RadarRow,
    RegistryEntry,
    Signal,
)
from declutter_context.render import radar_markdown, radar_text
from declutter_context.signals_files import s1_instruction_lines, s2_startup_tokens, s5_memory
from declutter_context.signals_repo import is_handoff_doc, s6_compactions, s9_docs_without_status

CFG = Config()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


def _js_slug(path: str) -> str:
    """Independent oracle: Claude Code's slug, computed over UTF-16 code units."""
    units = path.encode("utf-16-le")
    out = []
    for i in range(0, len(units), 2):
        cu = int.from_bytes(units[i : i + 2], "little")
        ch = chr(cu) if cu < 0xD800 or cu > 0xDFFF else "\0"
        out.append(ch if re.fullmatch(r"[A-Za-z0-9]", ch) else "-")
    return "".join(out)


def _run_with_timeout(fn: Callable[[], object], seconds: float = 5.0) -> object:
    box: list[object] = []
    th = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    th.start()
    th.join(seconds)
    assert not th.is_alive(), "call blocked"
    return box[0]


# --------------------------------------------------------------------------- slug


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/home/user/projects/demo ñandú /App",
            "-home-user-projects-demo--and---App",
        ),
        (
            "/home/user/projects/notas de investigación/demo-app",
            "-home-user-projects-notas-de-investigaci-n-demo-app",
        ),
        ("/home/user/demo-z/.claude/worktrees/x_y", "-home-user-demo-z--claude-worktrees-x-y"),
        ("/tmp/a.b_c d", "-tmp-a-b-c-d"),
        ("/p/😀x", "-p---x"),
    ],
)
def test_slug_matches_claude_code(path: str, expected: str) -> None:
    assert loadset.claude_project_slug(path) == expected
    assert loadset.claude_project_slug(path) == _js_slug(path)


def _odd_project(tmp_path: Path, dirname: str) -> Project:
    home = tmp_path / "home"
    root = home / "projects" / dirname / "App"
    root.mkdir(parents=True)
    (home / ".claude").mkdir()
    return Project.at(root, home=home, claude_home=home / ".claude")


def test_memory_and_transcript_found_for_path_with_space_accent_underscore(
    tmp_path: Path,
) -> None:
    p = _odd_project(tmp_path, "demo ñandú_v1.2 ")
    folder = p.claude_home / "projects" / _js_slug(unicodedata.normalize("NFC", str(p.cwd)))
    (folder / "memory").mkdir(parents=True)
    (folder / "memory" / "MEMORY.md").write_text("- a\n- b\n", encoding="utf-8")
    (folder / "s.jsonl").write_text('{"type":"compact_boundary"}\n', encoding="utf-8")
    assert loadset.memory_md_path(p) == folder / "memory" / "MEMORY.md"
    s5 = s5_memory(p, CFG)
    assert s5.level is Level.GREEN and s5.value == 2
    s6 = s6_compactions(p, CFG)
    assert s6.value == 1
    assert any(f.kind == "memory" for f in loadset.claude_loadset(p, CFG).files)


def test_slug_falls_back_to_nfd_spelling(tmp_path: Path) -> None:
    p = _odd_project(tmp_path, unicodedata.normalize("NFC", "ñandú"))
    nfd = loadset.claude_project_slug(unicodedata.normalize("NFD", str(p.cwd)))
    (p.claude_home / "projects" / nfd).mkdir(parents=True)
    assert loadset.claude_project_dir(p).name == nfd


def test_long_slug_matches_hashed_folder(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ("x" * 240)
    root.mkdir(parents=True)
    p = Project.at(root, home=home, claude_home=home / ".claude")
    slug = loadset.claude_project_slug(p.cwd)
    hashed = p.claude_home / "projects" / (slug[:200] + "-abc123")
    hashed.mkdir(parents=True)
    assert loadset.claude_project_dir(p) == hashed


# --------------------------------------------------------------------------- unsafe files


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_fifo_instruction_files_never_block(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "a\n"})
    os.mkfifo(p.root / "CLAUDE.md")
    os.mkfifo(p.root / "declutter.toml")
    assert _run_with_timeout(lambda: fsutil.safe_read_text(p.root / "CLAUDE.md")) is None
    assert _run_with_timeout(lambda: fsutil.safe_text_stats(p.root / "CLAUDE.md")) is None
    s1 = _run_with_timeout(lambda: s1_instruction_lines(p, CFG))
    assert isinstance(s1, Signal) and s1.value == 1
    payload = json.dumps({"cwd": str(p.root)})
    out = _run_with_timeout(
        lambda: hooks.statusline(payload, home=p.home, claude_home=p.claude_home)
    )
    assert isinstance(out, str) and out


def test_huge_files_are_counted_by_streaming(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fsutil, "MAX_READ_BYTES", 1000)
    p = make_project({"CLAUDE.md": "123456789\n" * 600, "AGENTS.md": ""})
    text = fsutil.safe_read_text(p.root / "CLAUDE.md")
    assert text is not None and len(text) == 1000
    assert fsutil.safe_text_stats(p.root / "CLAUDE.md") == fsutil.TextStats(6000, 600)
    assert s1_instruction_lines(p, CFG).value == 600
    ls = loadset.claude_loadset(p, CFG)
    claude = next(f for f in ls.files if f.path.name == "CLAUDE.md")
    assert (claude.bytes, claude.lines) == (6000, 600)
    assert s2_startup_tokens(p, CFG).value == 1667  # ceil(6000 / 3.6)


def test_text_stats_counts_last_line_without_newline(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_bytes(b"a\nb")
    assert fsutil.safe_text_stats(f) == fsutil.TextStats(3, 2)
    f.write_bytes(b"")
    assert fsutil.safe_text_stats(f) == fsutil.TextStats(0, 0)


def test_symlink_loops_do_not_explode(make_project: MakeProject) -> None:
    p = make_project({"sub/plan.md": "# plan\n"})
    for name in ("loop", "loop2", "loop3"):
        Path(p.root / "sub" / name).symlink_to("..")
    Path(p.root / "self").symlink_to(".")
    start = time.monotonic()
    found = list(fsutil.iter_files(p.root, "*.md"))
    assert found == [p.root / "sub" / "plan.md"]
    assert time.monotonic() - start < 2
    assert s9_docs_without_status(p, CFG).detail.startswith("1 of 1 ")


def test_symlink_to_secret_is_never_read(make_project: MakeProject) -> None:
    p = make_project({".env": "API_KEY=dummy\n" * 100, "CLAUDE.md": "@notes.md\n"})
    Path(p.root / "notes.md").symlink_to(".env")
    assert fsutil.safe_read_text(p.root / ".env") is None
    assert fsutil.safe_read_text(p.root / "notes.md") is None
    assert fsutil.safe_text_stats(p.root / "notes.md") is None
    kinds = {f.path.name: f.kind for f in loadset.claude_loadset(p, CFG).files}
    assert kinds == {"CLAUDE.md": "claude_md"}
    (p.root / "CLAUDE.md").unlink()
    Path(p.root / "CLAUDE.md").symlink_to(".env")
    assert s1_instruction_lines(p, CFG).level is Level.NA
    assert loadset.claude_loadset(p, CFG).files == ()


def test_project_symlink_cannot_reach_rest_of_home(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "a\n"})
    private = p.home / "private-notes.md"
    private.write_text("secret-ish\n" * 50, encoding="utf-8")
    Path(p.root / "CLAUDE.md").symlink_to(private)
    assert loadset.claude_loadset(p, CFG).files == ()
    assert s1_instruction_lines(p, CFG).detail == "CLAUDE.md 0 + AGENTS.md 1 lines"
    # A ~/ import from a project file is still read: Claude Code loads it too.
    (p.root / "CLAUDE.md").unlink()
    (p.root / "CLAUDE.md").write_text("@~/private-notes.md\n", encoding="utf-8")
    assert "private-notes.md" in {f.path.name for f in loadset.claude_loadset(p, CFG).files}


# --------------------------------------------------------------------------- cache


def _s2(m: Measurement) -> Level:
    return next(s.level for s in m.signals if s.id == "S2")


def test_cache_separates_cwd_and_tracks_user_files(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "root\n", "sub/CLAUDE.md": "x" * 200_000})
    home = p.home
    sub = Project.at(p.root, cwd=p.root / "sub", home=p.home, claude_home=p.claude_home)
    cold_sub = measure.measure_cached(sub, CFG, home=home)
    cold_root = measure.measure_cached(p, CFG, home=home)
    assert _s2(cold_sub) is Level.RED and _s2(cold_root) is Level.GREEN
    warm_sub = measure.measure_cached(sub, CFG, home=home)
    assert _s2(warm_sub) is Level.RED
    assert _s2(measure.measure_cached(p, CFG, home=home)) is Level.GREEN

    s2 = next(s for s in measure.measure_cached(p, CFG, home=home).signals if s.id == "S2")
    (p.claude_home / "CLAUDE.md").write_text("u" * 200_000, encoding="utf-8")
    s2_after = next(s for s in measure.measure_cached(p, CFG, home=home).signals if s.id == "S2")
    assert s2_after.value is not None and s2.value is not None
    assert s2_after.value > s2.value


def test_cache_tracks_import_targets(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "@docs/big.md\n", "docs/big.md": "small\n"})
    first = measure.measure_cached(p, CFG, home=p.home)
    (p.root / "docs" / "big.md").write_text("y" * 100_000, encoding="utf-8")
    second = measure.measure_cached(p, CFG, home=p.home)
    assert _s2(first) is Level.GREEN and _s2(second) is Level.RED


def test_fingerprint_differs_by_cwd_even_without_memory(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "x\n", "sub/a.txt": "a"})
    sub = Project.at(p.root, cwd=p.root / "sub", home=p.home, claude_home=p.claude_home)
    assert cache.fingerprint(p) != cache.fingerprint(sub)
    assert measure.cache_key(p) != measure.cache_key(sub)


# --------------------------------------------------------------------------- radar / registry


def test_discover_relative_dir_saves_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_tree(tmp_path / "base", {"alpha/AGENTS.md": "a\n"})
    reg = tmp_path / "reg.toml"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["radar", "--discover", "base", "--save", "--registry", str(reg)]) == 0
    entries = radar.load_registry(reg)
    assert [e.path for e in entries] == [(tmp_path / "base" / "alpha").resolve()]
    assert entries[0].name == "alpha"
    monkeypatch.chdir("/")
    assert radar.build_row(entries[0], home=tmp_path).error is None


def test_discover_dot_when_base_is_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "solo"
    write_tree(proj, {"CLAUDE.md": "x\n"})
    reg = tmp_path / "reg.toml"
    monkeypatch.chdir(proj)
    assert cli.main(["radar", "--discover", ".", "--save", "--registry", str(reg)]) == 0
    (entry,) = radar.load_registry(reg)
    assert entry.name == "solo" and entry.path == proj.resolve()


def test_registry_problems_are_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = tmp_path / "reg.toml"
    reg.write_text('projects = ["/ruta"]\n', encoding="utf-8")
    assert cli.main(["radar", "--registry", str(reg)]) == 0
    err = capsys.readouterr().err
    assert "unknown key 'projects'" in err
    reg.write_text("[[project]\n", encoding="utf-8")
    assert radar.load_registry(reg) == []
    assert any("cannot parse" in w for w in radar.last_warnings())
    reg.write_text('[[project]]\nname = "x"\nextra = 1\n', encoding="utf-8")
    assert radar.load_registry(reg) == []
    joined = " ".join(radar.last_warnings())
    assert "unknown key 'extra'" in joined and "no 'path'" in joined


def test_session_field_selects_claude_folder(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "x\n"})
    session = p.home
    entry = RegistryEntry(path=p.root, name="proj", session=str(session))
    assert radar.session_cwd(entry, p.home) == session
    assert radar.session_cwd(RegistryEntry(p.root, "proj", session="label"), p.home) is None
    assert radar.session_cwd(RegistryEntry(p.root, "proj", session="~"), p.home) == p.home
    folder = p.claude_home / "projects" / loadset.claude_project_slug(session)
    folder.mkdir(parents=True)
    (folder / "s.jsonl").write_text("compact_boundary\n" * 11, encoding="utf-8")
    with_session = Project.at(p.root, home=p.home, claude_home=p.claude_home, session_cwd=session)
    assert s6_compactions(with_session, CFG).level is Level.RED
    assert s6_compactions(p, CFG).level is Level.NA


def test_last_activity_sees_other_branches_and_is_cached(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project({"a.txt": "a"}, git=True)
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": "2099-01-01T00:00:00",
        "GIT_AUTHOR_DATE": "2099-01-01T00:00:00",
    }
    for cmd in (["checkout", "-q", "-b", "feature"], ["commit", "-q", "--allow-empty", "-m", "f"]):
        subprocess.run(["git", "-C", str(p.root), *cmd], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(p.root), "checkout", "-q", "main"], check=True)
    ts = gitutil.latest_ref_ts(p.root)
    assert ts is not None and datetime.fromtimestamp(ts).year == 2099
    first = radar.last_activity_ts(p.root, home=p.home)

    def boom(_root: Path) -> float | None:
        raise AssertionError("walked again despite a fresh cache")

    monkeypatch.setattr(fsutil, "newest_mtime", boom)
    assert radar.last_activity_ts(p.root, home=p.home) == first


# --------------------------------------------------------------------------- render


def _row(tmp_path: Path, level: Level, worst: Signal | None, estado: Estado | None) -> RadarRow:
    return RadarRow(
        entry=RegistryEntry(tmp_path, "p"),
        level=level,
        last_activity=None,
        dirty=0,
        ahead=0,
        estado=estado,
        worst=worst,
    )


def test_radar_lists_blocked_and_hides_green_worst(tmp_path: Path) -> None:
    estado = Estado(
        path=tmp_path / "ESTADO.md",
        updated=date(2026, 11, 1),
        phase=None,
        next_ai=(EstadoItem("n1"),),
        blocked=("waiting for ethics committee",),
    )
    green = Signal("S1", "Root instruction lines", 10, "lines", Level.GREEN, 200, 400, "d1")
    row = _row(tmp_path, Level.GREEN, green, estado)
    text = radar_text([row], "es")
    assert "Bloqueado:" in text and "waiting for ethics committee" in text
    assert "d1" not in text and text.splitlines()[1].rstrip().endswith("—")
    md = radar_markdown([row], "en", date(2026, 11, 4))
    assert "### Blocked" in md and "- waiting for ethics committee" in md


def test_radar_no_state_not_duplicated(tmp_path: Path) -> None:
    s7 = Signal("S7", "State freshness", None, "days", Level.YELLOW, 7, 21, "no ESTADO.md/STATE.md")
    text = radar_text([_row(tmp_path, Level.YELLOW, s7, None)], "en")
    assert "no ESTADO.md/STATE.md" not in text
    assert text.count("no state file") == 1


# --------------------------------------------------------------------------- names, hooks, S2, S9


def test_signal_names_match_catalog(make_project: MakeProject) -> None:
    m = measure.measure(make_project({"CLAUDE.md": "x\n"}), CFG)
    for s in m.signals:
        assert s.name == MESSAGES["en"][f"sig.{s.id}"], s.id


def test_statusline_and_hook_follow_language(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "es_CL.UTF-8")
    p = make_project({"CLAUDE.md": "l\n" * 450, "AGENTS.md": ""})
    data = {"context_window": {"used_percentage": 42}, "cwd": str(p.root)}
    out = hooks.statusline(json.dumps(data), home=p.home, claude_home=p.claude_home)
    assert out == "ctx 42% · 🔴 S1 450 líneas"
    line = hooks.session_start(json.dumps(data), home=p.home, claude_home=p.claude_home)
    assert "450 líneas" in line and line.endswith("(/declutter:declutter-hygiene)")


def test_s2_labels_distinguish_sources(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "p" * 400 + "\n@docs/x.md\n", "docs/x.md": "i" * 300})
    (p.claude_home / "CLAUDE.md").write_text("u" * 500, encoding="utf-8")
    (p.home / "CLAUDE.md").write_text("h" * 200, encoding="utf-8")
    detail = s2_startup_tokens(p, CFG).detail
    assert detail.startswith("~/.claude/CLAUDE.md ~")
    assert "CLAUDE.md ~" in detail and "import:docs/x.md ~" in detail
    full = s2_startup_tokens(p, Config(bytes_per_token=1000)).detail
    assert "~/.claude/CLAUDE.md" in full


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("plan.md", True),
        ("PLAN_2026.md", True),
        ("2026-10-01-traspaso.md", True),
        ("traspasos.md", True),
        ("planes.md", True),
        ("handoff notes.md", True),
        ("plan-maestro.md", True),
        ("explanation.md", False),
        ("planilla.md", False),
        ("airplane.md", False),
        ("planificacion.md", False),
    ],
)
def test_handoff_doc_names(name: str, expected: bool) -> None:
    assert is_handoff_doc(name) is expected


# --------------------------------------------------------------------------- init


def test_init_dot_uses_folder_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "mi proyecto"
    proj.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(proj)
    assert cli.main(["init", "--lang", "es"]) == 0
    assert (proj / "ESTADO.md").read_text(encoding="utf-8").startswith("# Estado — mi proyecto")
    assert scaffold.apply_init(Path(), "es", date(2026, 11, 4)) == []


def test_init_survives_dangling_docs_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "dg"
    proj.mkdir()
    Path(proj / "docs").symlink_to(proj / "missing")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["init", str(proj), "--lang", "es"]) == 0
    assert (proj / "AGENTS.md").is_file() and (proj / "ESTADO.md").is_file()
    assert not (proj / "missing").exists()


def test_init_big_claude_md_prints_measurement_and_import_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    proj = tmp_path / "big"
    write_tree(proj, {"CLAUDE.md": "line\n" * 450})
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["init", str(proj), "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "🔴 S1 Root instruction lines: " in out
    assert "token counts are estimates" in out
    assert "phase-2" in out
    assert "@AGENTS.md" in out
    assert "AGENTS" not in (proj / "CLAUDE.md").read_text(encoding="utf-8")


def test_init_quiet_when_claude_md_imports_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    proj = tmp_path / "ok"
    write_tree(proj, {"CLAUDE.md": "@AGENTS.md\n"})
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["init", str(proj), "--lang", "en"]) == 0
    assert "@AGENTS.md" not in capsys.readouterr().out


def test_resolve_import_uses_is_absolute(tmp_path: Path) -> None:
    target = tmp_path / "abs.md"
    assert loadset._resolve_import(str(target), tmp_path / "x", tmp_path) == target
    assert sys.platform != "win32" or loadset._resolve_import("C:\\a.md", tmp_path, tmp_path)


# ------------------------------------------------------------------- S4 over mdblocks (§4.7)


def _s4_headings(text: str) -> list[str]:
    from declutter_context import signals_files

    return signals_files._level2_headings(text)


def test_s4_tilde_fence_with_backticks_inside_does_not_toggle(make_project: MakeProject) -> None:
    """Phase 1: any ```/~~~ line toggled the fence, so the inner ``` closed the ~~~ fence
    and "## 2026-01-01 in" was counted as a dated heading (1 of 4). Now: the ~~~ fence
    only closes with ~~~, so the inner heading is code and 0 of 3 are dated."""
    from declutter_context.signals_files import s4_changelog_ratio

    text = "## A\n~~~\n```\n## 2026-01-01 in\n```\n~~~\n## Out\n## B\n"
    assert _s4_headings(text) == ["A", "Out", "B"]
    s = s4_changelog_ratio(make_project({"CLAUDE.md": text}), CFG)
    assert s.detail == "0 of 3 headings dated"


def test_s4_indented_fence_is_recognized() -> None:
    """Phase 1: fences were recognized only at column 0, so "## no" inside a fence
    indented by 1-3 spaces was counted. Now: indented fences hide their contents."""
    text = "## A\n   ```\n## no\n   ```\n## B\n    ```\n## C\n"
    assert _s4_headings(text) == ["A", "B", "C"]


def test_s4_backtick_info_string_with_backtick_is_not_fence() -> None:
    """Phase 1: a prose line starting with ```inline``` toggled the fence and hid the
    next headings. Now: a backtick info string containing ` is inline code, not a fence."""
    text = "## A\n```npm run dev``` starts it\n## B\n## C\n"
    assert _s4_headings(text) == ["A", "B", "C"]


def test_s4_h2_in_block_comment_or_front_matter_not_counted() -> None:
    """Phase 1: "##" lines inside an HTML block comment or the front matter counted as
    headings. Now: neither counts; an inline "<!--" mid-line opens nothing."""
    text = "---\n## fm\n---\n## A\n<!--\n## hidden\n-->\nsay <!-- here\n## B\n"
    assert _s4_headings(text) == ["A", "B"]


def test_level2_headings_tolerant_of_open_fence_at_end() -> None:
    """Phase 1: an open fence at the end just hid the rest (no error). Now: same
    tolerant result, without raising, because measure must never fail; the text is
    split on "\\n" only, so a lone CR or U+2028 no longer splits a line."""
    text = "## A\n## B\u2028## not split\n```\n## inside\n"
    assert _s4_headings(text) == ["A", "B\u2028## not split"]
