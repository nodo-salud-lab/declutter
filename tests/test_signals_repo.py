"""Tests for signals S6-S9."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import signals_repo
from declutter_context.loadset import claude_project_slug
from declutter_context.model import DEFAULT_THRESHOLDS, Config, Level, Project

CFG = Config()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _transcript(project: Project, name: str, lines: list[str]) -> Path:
    slug = claude_project_slug(project.cwd)
    d = project.claude_home / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---- S6 ----


def test_s6_na_without_transcript(make_project: MakeProject) -> None:
    sig = signals_repo.s6_compactions(make_project(), CFG)
    assert sig.level is Level.NA
    assert sig.value is None


def test_s6_red_with_twelve_boundaries(make_project: MakeProject) -> None:
    project = make_project()
    lines = ['{"type":"user"}'] + ['{"subtype":"compact_boundary"}'] * 12
    p = _transcript(project, "a.jsonl", lines)
    sig = signals_repo.s6_compactions(project, CFG)
    assert sig.value == 12
    assert sig.level is Level.RED
    assert sig.action == "act.new_session"
    assert "12 compactions in a.jsonl" in sig.detail
    assert "MB" in sig.detail
    assert p.name in sig.detail


def test_s6_counts_lines_not_occurrences(make_project: MakeProject) -> None:
    project = make_project()
    _transcript(project, "a.jsonl", ["compact_boundary compact_boundary", "x"])
    assert signals_repo.s6_compactions(project, CFG).value == 1


def test_s6_picks_newest_by_mtime(make_project: MakeProject) -> None:
    import os

    project = make_project()
    old = _transcript(project, "old.jsonl", ['{"subtype":"compact_boundary"}'] * 8)
    new = _transcript(project, "new.jsonl", ['{"subtype":"compact_boundary"}'] * 2)
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    sig = signals_repo.s6_compactions(project, CFG)
    assert sig.value == 2
    assert sig.level is Level.GREEN
    assert "new.jsonl" in sig.detail


def test_s6_yellow(make_project: MakeProject) -> None:
    project = make_project()
    _transcript(project, "a.jsonl", ["compact_boundary"] * 6)
    assert signals_repo.s6_compactions(project, CFG).level is Level.YELLOW


# ---- S7 ----


def test_s7_absent_state_is_yellow(make_project: MakeProject) -> None:
    sig = signals_repo.s7_state_freshness(make_project(), CFG)
    assert sig.level is Level.YELLOW
    assert sig.value is None
    assert sig.detail == "no ESTADO.md/STATE.md"
    assert sig.action == "act.update_state"


def test_s7_no_valid_date_is_yellow(make_project: MakeProject) -> None:
    project = make_project({"ESTADO.md": "# Estado\n\n## Next (AI)\n- a\n"})
    sig = signals_repo.s7_state_freshness(project, CFG)
    assert sig.level is Level.YELLOW
    assert sig.value is None
    assert sig.detail == "no valid Updated/Actualizado date"


@pytest.mark.parametrize(
    ("updated", "days", "level"),
    [
        ("2026-11-04", 0, Level.GREEN),
        ("2026-10-28", 7, Level.GREEN),
        ("2026-10-27", 8, Level.YELLOW),
        ("2026-10-14", 21, Level.YELLOW),
        ("2026-10-13", 22, Level.RED),
    ],
)
def test_s7_freshness_levels(
    make_project: MakeProject,
    monkeypatch: pytest.MonkeyPatch,
    updated: str,
    days: int,
    level: Level,
) -> None:
    monkeypatch.setattr(signals_repo, "_today", lambda: date(2026, 11, 4))
    project = make_project({"ESTADO.md": f"# Estado\n\nActualizado: {updated}\n"})
    sig = signals_repo.s7_state_freshness(project, CFG)
    assert sig.value == days
    assert sig.level is level
    assert sig.unit == "days"


# ---- S8 ----


def test_s8_na_without_git(make_project: MakeProject) -> None:
    sig = signals_repo.s8_branches_worktrees(make_project(), CFG)
    assert sig.level is Level.NA
    assert sig.value is None


def test_s8_counts_merged_branches_and_worktrees(make_project: MakeProject) -> None:
    project = make_project({"a.txt": "x"}, git=True)
    for n in ("f1", "f2"):
        _git(project.root, "branch", n)
    wt = project.root.parent / "wt1"
    _git(project.root, "worktree", "add", "-q", "-b", "wtb", str(wt))
    sig = signals_repo.s8_branches_worktrees(project, CFG)
    assert sig.value == 3  # f1, f2, wtb are all merged into main
    assert sig.detail == "3 merged branches, 2 worktrees"
    assert sig.level is Level.GREEN
    assert sig.action == "act.prune_branches"


def test_s8_worktree_threshold_drives_level(make_project: MakeProject) -> None:
    project = make_project({"a.txt": "x"}, git=True)
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds["S8_worktrees"] = (0, 5)
    sig = signals_repo.s8_branches_worktrees(project, Config(thresholds=thresholds))
    assert sig.level is Level.YELLOW  # 1 worktree > 0
    assert (sig.yellow, sig.red) == (0, 5)


def test_s8_branch_threshold_red(make_project: MakeProject) -> None:
    project = make_project({"a.txt": "x"}, git=True)
    for n in ("f1", "f2", "f3"):
        _git(project.root, "branch", n)
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds["S8_branches"] = (1, 2)
    sig = signals_repo.s8_branches_worktrees(project, Config(thresholds=thresholds))
    assert sig.level is Level.RED


# ---- S9 ----


def test_s9_counts_docs_without_status(make_project: MakeProject) -> None:
    project = make_project(
        {
            "docs/traspaso-1.md": "# Traspaso\n\nEstado: cerrado\n",
            "docs/traspaso-2.md": "# Traspaso\n\nsin encabezado\n",
            "docs/PLAN-x.md": "# Plan\n- **Status:** open\n",
            "docs/handoff.md": "# Handoff\nnada\n",
            "docs/notes.md": "# Notes\n",
            "node_modules/pkg/plan.md": "# Plan\n",
            "docs/plan.txt": "no md\n",
        }
    )
    sig = signals_repo.s9_docs_without_status(project, CFG)
    assert sig.value == 2
    assert sig.unit == "count"
    assert sig.detail == "2 of 4 handoff/plan docs lack a status header"
    assert sig.level is Level.GREEN
    assert sig.action == "act.mark_docs"


def test_s9_status_must_be_in_first_15_lines(make_project: MakeProject) -> None:
    body = "\n".join(["x"] * 15) + "\nestado: late\n"
    ok = "\n".join(["x"] * 14) + "\nestado: ok\n"
    project = make_project({"plan-a.md": body, "plan-b.md": ok})
    sig = signals_repo.s9_docs_without_status(project, CFG)
    assert sig.value == 1


def test_s9_mid_line_status_does_not_count(make_project: MakeProject) -> None:
    project = make_project({"plan.md": "see the status: below\n"})
    assert signals_repo.s9_docs_without_status(project, CFG).value == 1


def test_s9_yellow_threshold(make_project: MakeProject) -> None:
    files = {f"plan-{i}.md": "x\n" for i in range(21)}
    sig = signals_repo.s9_docs_without_status(make_project(files), CFG)
    assert sig.value == 21
    assert sig.level is Level.YELLOW


def test_s9_no_docs(make_project: MakeProject) -> None:
    sig = signals_repo.s9_docs_without_status(make_project(), CFG)
    assert sig.value == 0
    assert sig.level is Level.GREEN
