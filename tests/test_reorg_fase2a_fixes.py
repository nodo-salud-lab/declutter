"""Regressions for the confirmed phase-2a defects found by the verification probes.

Each test reproduces one defect on a synthetic project (``make_project``) and fails
without its fix: ignored outputs, interruptions after the commit, prune then undo, a
crash between the branch switch and the journal, staged additions after a rejected
commit, fenced R6 markers, card overrides on demoted units, new dated rules under the
ratchet, verify after a repin and unchanged control files on a second run.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.model import Config, Project, ReorgConfig
from declutter_context.reorg import ReorgError, gitops, mdblocks, proof, undo
from declutter_context.reorg import apply as ap
from declutter_context.reorg import manifest as mf
from declutter_context.reorg.apply import ApplyOutcome
from declutter_context.reorg.lexicon import build_lexicon
from test_apply import (
    BASE,
    NOTE_L4,
    RID,
    TEXT,
    FakePrompter,
    _hook,
    commit_all,
    git,
    git_project,
    journal,
    keys,
    no_git_project,
    plan,
    run_apply,
    tree_bytes,
)

BRANCH = f"declutter/reorg-{RID}"
TAG = f"declutter/pre-reorg-{RID}"
NOW2 = datetime(2026, 11, 6, 9, 0)
TODAY = date(2026, 11, 5)


def run_undo(project: Project, prompter: FakePrompter | None = None) -> ApplyOutcome:
    return undo.undo(project, RID, prompter or FakePrompter(tty=False), yes=True, lang="es")


def porcelain(root: Path) -> str:
    return git(root, "status", "--porcelain", "--untracked-files=all")


def cached(root: Path) -> str:
    return git(root, "diff", "--cached", "--name-only")


# --------------------------------------------------------------------------- D1 ignored outputs


def test_root_gitignore_excluding_declutter_fails_plan_before_writing(
    make_project: MakeProject,
) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    (project.root / ".gitignore").write_bytes(b".declutter/\n")
    commit_all(project.root)
    with pytest.raises(ReorgError) as exc:
        plan(project)
    issues = exc.value.issues
    assert {i.key for i in issues} >= {"output_ignored"}
    hit = next(i for i in issues if i.key == "output_ignored")
    assert hit.code == 1 and hit.detail == ".gitignore:1"
    assert not (project.root / ".declutter" / "reorg").exists()


def test_root_gitignore_added_after_plan_blocks_apply_before_writing(
    make_project: MakeProject,
) -> None:
    project = git_project(make_project)
    plan(project)
    (project.root / ".gitignore").write_bytes(b"docs/\n")
    commit_all(project.root, "ignore docs")
    before = tree_bytes(project.root)
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 1 and "output_ignored" in keys(res), res.issues
    assert tree_bytes(project.root) == before
    assert gitops.head_branch(project.root) == "main"
    assert cached(project.root) == ""


def test_declutter_star_pattern_in_root_gitignore_still_commits(
    make_project: MakeProject,
) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    (project.root / ".gitignore").write_bytes(b".declutter/*\n")
    commit_all(project.root)
    assert plan(project).code == 0
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 0, res.issues


def test_git_add_failure_is_not_blamed_on_a_hook_and_leaves_index_clean(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    real_add = gitops.add

    def failing_add(root: Path, paths: Any) -> None:
        real_add(root, list(paths)[:1])  # a partial add, then the failure
        raise gitops.GitError("add", 1)

    monkeypatch.setattr(gitops, "add", failing_add)
    res = run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    assert res.code == 1
    assert "git_add_failed" in keys(res) and "commit_rejected" not in keys(res)
    assert not any("hook" in i.detail for i in res.issues)
    assert cached(project.root) == ""


# --------------------------------------------------------------------------- D2 after the commit


def _interrupt_rev_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    real = proof.verify

    def fake(*a: Any, **kw: Any) -> Any:
        if kw.get("checks") == proof.CHECKS_REV:
            raise KeyboardInterrupt
        return real(*a, **kw)

    monkeypatch.setattr(proof, "verify", fake)


def _stop_after_commit(monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]) -> None:
    real = gitops.commit_only

    def fake(*a: Any, **kw: Any) -> str:
        real(*a, **kw)
        raise exc

    monkeypatch.setattr(gitops, "commit_only", fake)


def test_ctrl_c_during_commit_proof_keeps_the_commit_and_undo_works(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    _interrupt_rev_proof(monkeypatch)
    res = run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    assert res.code == 130
    j = journal(project)
    assert j is not None and j.state == "committed" and j.commit
    assert gitops.head_branch(project.root) == BRANCH
    assert porcelain(project.root) == ""  # nothing restored over the committed tree
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert gitops.head_branch(project.root) == "main"
    assert tree_bytes(project.root) == before


def test_ctrl_c_right_after_git_commit_keeps_the_commit_and_undo_works(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    _stop_after_commit(monkeypatch, KeyboardInterrupt)
    res = run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    assert res.code == 130
    assert gitops.head_branch(project.root) == BRANCH
    assert porcelain(project.root) == ""
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert gitops.head_branch(project.root) == "main"
    assert tree_bytes(project.root) == before
    j = journal(project)
    assert j is not None and j.state == "undone"


def test_crash_between_commit_and_journal_then_undo_and_resume(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    _stop_after_commit(monkeypatch, SystemExit)
    with pytest.raises(SystemExit):
        run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    j = journal(project)
    assert j is not None and j.state == "written" and j.commit is None
    head = git(project.root, "rev-parse", "HEAD").strip()
    # resuming the commit records the existing one instead of failing
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 0, res.issues
    j = journal(project)
    assert j is not None and j.state == "committed" and j.commit == head
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert gitops.head_branch(project.root) == "main"
    assert tree_bytes(project.root) == before


def test_crash_between_commit_and_journal_then_undo(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    _stop_after_commit(monkeypatch, SystemExit)
    with pytest.raises(SystemExit):
        run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert gitops.head_branch(project.root) == "main"
    assert tree_bytes(project.root) == before
    assert porcelain(project.root) == ""


# --------------------------------------------------------------------------- D3 prune then undo


def test_prune_then_undo_committed_keeps_declutter_ignored(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    assert undo.prune(project, RID, FakePrompter(tty=False), yes=True).code == 0
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert (project.root / ".declutter" / ".gitignore").read_bytes() == b"*\n"
    assert porcelain(project.root) == ""


# --------------------------------------------------------------------------- D4 switch -c crash


@pytest.mark.parametrize("where", ["tag", "switch_create"])
def test_crash_after_tag_or_switch_then_undo_returns_to_base(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    real = getattr(gitops, where)

    def crash(root: Path, name: str) -> None:
        real(root, name)
        raise SystemExit("crash")

    monkeypatch.setattr(gitops, where, crash)
    with pytest.raises(SystemExit):
        run_apply(project, yes=True)
    monkeypatch.undo()
    j = journal(project)
    assert j is not None and j.base_branch == "main"
    prompter = FakePrompter(tty=False)
    out = run_undo(project, prompter)
    assert out.code == 0, out.issues
    assert gitops.head_branch(project.root) == "main"
    assert tree_bytes(project.root) == before
    said = "\n".join(prompter.said)
    assert f"git tag -d {TAG}" in said
    if where == "switch_create":
        assert f"git branch -D {BRANCH}" in said


# --------------------------------------------------------------------------- D5 index after reject


def test_rejected_commit_then_undo_leaves_no_staged_additions(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    _hook(project, "exit 1\n")
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 1 and "commit_rejected" in keys(res)
    assert cached(project.root) == ""
    (project.root / ".git" / "hooks" / "pre-commit").unlink()
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert gitops.head_branch(project.root) == "main"
    assert cached(project.root) == ""
    assert porcelain(project.root) == ""
    assert tree_bytes(project.root) == before


def test_crash_between_add_and_commit_then_undo_cleans_index(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)

    def crash(*a: Any, **kw: Any) -> str:
        raise SystemExit("crash")

    monkeypatch.setattr(gitops, "commit_only", crash)
    with pytest.raises(SystemExit):
        run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    assert cached(project.root) != ""
    out = run_undo(project)
    assert out.code == 0, out.issues
    assert cached(project.root) == ""
    assert porcelain(project.root) == ""
    assert tree_bytes(project.root) == before


# --------------------------------------------------------------------------- D6 R6 and fences


def test_r6_ignores_marker_lines_inside_a_moved_fence(make_project: MakeProject) -> None:
    fenced = (
        "\n## Sesión 2026-10-01\n```\n<!-- declutter:end U02 -->\n<!-- declutter:end U03 -->\n```\n"
    )
    project = no_git_project(make_project, BASE + NOTE_L4 + fenced)
    assert plan(project).code == 0
    res = run_apply(project, yes=True, no_git=True)
    assert res.code == 0, res.issues
    report = ratchet.check(project, Config(), staged=False)
    assert report.code == 0, report.failures


def test_r6_still_reports_an_altered_literal(make_project: MakeProject) -> None:
    fenced = "\n## Sesión 2026-10-01\n```\n<!-- declutter:end U03 -->\n```\n"
    project = no_git_project(make_project, BASE + NOTE_L4 + fenced)
    plan(project)
    assert run_apply(project, yes=True, no_git=True).code == 0
    hist = next((project.root / "docs" / "historial").glob("2026-10.md"))
    hist.write_bytes(hist.read_bytes().replace(b"Solo registro", b"Solo regist"))
    report = ratchet.check(project, Config(), staged=False)
    assert report.code == 3
    assert any(f.rule == "R6" and "altered" in f.detail for f in report.failures)


# --------------------------------------------------------------------------- D7 card on demoted


def test_card_override_on_budget_demoted_unit(make_project: MakeProject) -> None:
    body = "".join(f"- Nunca cambies el valor número {i} sin revisar.\n" for i in range(12))
    text = BASE + "\n## Regla del caché (2026-10-02)\n" + body
    project = make_project({"CLAUDE.md": text})
    cfg = Config(reorg=ReorgConfig(l1_max_bytes=600))
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=cfg, lang="es", now=NOW2))
    rid = out.manifest.id
    u = next(u for u in out.manifest.units if u.heading == "Regla del caché (2026-10-02)")
    assert u.dest == "L4" and u.card and "budget:demoted" in u.reasons
    res = mf.set_override(
        project, cfg, rid, u.heading_key, card=False, reason="sin ficha", now=NOW2
    )
    assert "card_not_l4" not in {i.key for i in res.issues}, res.issues
    res = mf.set_override(project, cfg, rid, u.heading_key, accept=True, reason="ok", now=NOW2)
    assert res.code == 0, res.issues
    v = next(x for x in res.manifest.units if x.heading_key == u.heading_key)
    assert v.dest == "L4" and not v.card


def test_card_override_on_unit_left_in_l1_is_still_rejected(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": BASE})
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=Config(), lang="es", now=NOW2))
    u = next(u for u in out.manifest.units if u.heading == "Cómo correr")
    res = mf.set_override(
        project, Config(), out.manifest.id, u.heading_key, card=True, reason="x", now=NOW2
    )
    assert res.code == 4 and "card_not_l4" in {i.key for i in res.issues}


# --------------------------------------------------------------------------- D8 dated rule + R3

NEW_RULE = b"\n## Regla nueva (05-11-2026)\n- Nunca borres la tabla de usuarios.\n"


def _relax(project: Project) -> None:
    """Room for the pointer lines a demotion adds (the documented lever, §12.3)."""
    for s1_to, s3_to in ((200, None), (None, 50)):
        tty = FakePrompter(tty=True, answers=["confirmo"])
        code = ratchet.relax(
            project, s1_to=s1_to, s3_to=s3_to, reason="margen", prompter=tty, today=TODAY
        )
        assert code == 0


def _second_run(project: Project) -> mf.PlanOutcome:
    (project.root / "AGENTS.md").write_bytes((project.root / "AGENTS.md").read_bytes() + NEW_RULE)
    _relax(project)
    commit_all(project.root, "nueva regla")
    report = ratchet.check(project, Config(), staged=False)
    assert "R3" in [f.rule for f in report.failures]
    return mf.plan_project(mf.PlanRequest(project=project, cfg=Config(), lang="es", now=NOW2))


def _apply2(project: Project, rid: str, *, commit: bool = True) -> ApplyOutcome:
    opts = ap.ApplyOptions(yes=True, commit=commit)
    return ap.apply(project, Config(), rid, opts, FakePrompter(tty=False), now=NOW2, lang="es")


def test_new_dated_rule_is_proposed_out_of_l1_and_ratchet_passes(
    make_project: MakeProject,
) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    git(project.root, "switch", "-q", "main")
    git(project.root, "merge", "-q", "--ff-only", BRANCH)
    out = _second_run(project)
    rid = out.manifest.id
    u = next(u for u in out.manifest.units if u.heading == "Regla nueva (05-11-2026)")
    assert u.dest == "L4" and u.needs_confirmation
    assert ratchet.WOULD_FAIL not in out.manifest.warnings
    res = mf.set_override(project, Config(), rid, u.heading_key, accept=True, reason="ok", now=NOW2)
    assert res.code == 0, res.issues
    applied = _apply2(project, rid)
    assert applied.code == 0, applied.issues
    assert ratchet.check(project, Config(), staged=False).code == 0


def test_new_dated_rule_kept_in_l1_by_the_human_is_allowed(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    git(project.root, "switch", "-q", "main")
    git(project.root, "merge", "-q", "--ff-only", BRANCH)
    out = _second_run(project)
    rid = out.manifest.id
    u = next(u for u in out.manifest.units if u.heading == "Regla nueva (05-11-2026)")
    res = mf.set_override(
        project, Config(), rid, u.heading_key, keep=True, reason="sigue vigente", now=NOW2
    )
    assert res.code == 0, res.issues
    assert ratchet.WOULD_FAIL not in res.manifest.warnings
    applied = _apply2(project, rid)
    assert applied.code == 0, applied.issues
    assert ratchet.check(project, Config(), staged=False).code == 0


# --------------------------------------------------------------------------- D9 verify after repin


def test_verify_fails_when_a_repinned_block_is_deleted(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    pins = ratchet.pins_loads((project.root / ".declutter" / "pins.json").read_bytes())
    pin = next(p for p in pins if p.role == "unit")
    agents = project.root / "AGENTS.md"
    lines = list(mdblocks.split_lines(agents.read_bytes()).lines)
    start = ratchet.find_window(lines, pin.sha256, pin.lines)
    assert start is not None
    end = start + pin.lines - 1
    last = lines[end - 1]
    lines[end - 1] = last[:-1] + (b"!" if not last.endswith(b"!") else b"?")  # same size
    agents.write_bytes(b"\n".join(lines) + b"\n")
    code = ratchet.repin(
        project,
        pin.id,
        lines=(start, end),
        reason="editado a mano",
        prompter=FakePrompter(tty=True, answers=["confirmo"]),
        lexicon=build_lexicon({}),
        today=TODAY,
    )
    assert code == 0
    assert ratchet.check(project, Config(), staged=False).code == 0
    assert undo.verify_run(project, Config(), RID, rev=None, strict=False).ok
    del lines[start - 1 : end]
    agents.write_bytes(b"\n".join(lines) + b"\n")
    assert ratchet.check(project, Config(), staged=False).code == 3
    res = undo.verify_run(project, Config(), RID, rev=None, strict=False)
    assert not res.ok
    assert all(state != "released" for _u, state in res.states)
    assert any(state in ("protected_altered", "pin_missing") for _u, state in res.states)


# --------------------------------------------------------------------------- D10 second run C8b


def test_second_run_with_unchanged_gitignore_commits_and_proves(
    make_project: MakeProject,
) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    git(project.root, "switch", "-q", "main")
    git(project.root, "merge", "-q", "--ff-only", BRANCH)
    claude = project.root / "CLAUDE.md"
    claude.write_bytes(claude.read_bytes() + b"\n## Nota 2026-11-02\nOtra sesion.\n")
    _relax(project)
    commit_all(project.root, "nota")
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=Config(), lang="es", now=NOW2))
    assert out.code == 0, out.issues
    rid = out.manifest.id
    res = _apply2(project, rid)
    assert res.code == 0, res.issues
    j = ap.read_journal(project.root / ".declutter" / "reorg" / rid)
    assert j is not None and j.state == "committed"
    diff = git(project.root, "diff", "--name-only", f"declutter/pre-reorg-{rid}", "HEAD").split()
    assert ".declutter/.gitignore" not in diff
    proof_rev = undo.verify_run(project, Config(), rid, rev="HEAD", strict=True)
    assert proof_rev.ok, proof_rev.failures
    assert json.loads((project.root / ".declutter" / "ratchet.json").read_bytes())["reorg"] == rid
