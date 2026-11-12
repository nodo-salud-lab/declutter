"""``declutter reorg undo``, ``status``, ``prune`` and ``verify`` of a run (spec §6.1, §7, §11.4,
§11.5, §13).

Synthetic projects only (``make_project``); the intermediate journal states are produced
by stopping a real ``apply`` with ``SystemExit`` (which its rollback does not catch).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from conftest import MakeProject
from declutter_context.model import Config, Project
from declutter_context.reorg import ReorgError, gitops, proof, undo
from declutter_context.reorg import apply as ap
from declutter_context.reorg.apply import ApplyOutcome
from test_apply import (
    BASE,
    NOTE_L4,
    RID,
    TEXT,
    FakePrompter,
    commit_all,
    git,
    git_project,
    journal,
    keys,
    no_git_project,
    plan,
    run_apply,
    run_path,
    tree_bytes,
)

BRANCH = f"declutter/reorg-{RID}"
TAG = f"declutter/pre-reorg-{RID}"


# --------------------------------------------------------------------------- helpers


def run_undo(project: Project, prompter: FakePrompter | None = None, **kw: Any) -> ApplyOutcome:
    kw.setdefault("yes", True)
    return undo.undo(project, RID, prompter or FakePrompter(tty=False), lang="es", **kw)


def stop_at(state: str, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run ``apply`` and stop it (SystemExit, not rolled back) right after ``state``."""
    if state == "staged":
        real_verify = proof.verify

        def stop_verify(*a: Any, **kw: Any) -> Any:
            if kw.get("checks") == proof.CHECKS_STAGE:
                raise SystemExit("stop")
            return real_verify(*a, **kw)

        monkeypatch.setattr(proof, "verify", stop_verify)
    elif state == "tagged":

        def stop_backup(*a: Any, **kw: Any) -> None:
            raise SystemExit("stop")

        monkeypatch.setattr(ap, "_backup", stop_backup)
    elif state == "backed_up":

        def stop_write(*a: Any, **kw: Any) -> None:
            raise SystemExit("stop")

        monkeypatch.setattr(ap, "_write_all", stop_write)
    elif state == "writing":
        run = str(run_path(project))
        real_replace = os.replace
        count = {"n": 0}

        def crash(src: Any, dst: Any, *a: Any, **kw: Any) -> None:
            real_replace(src, dst, *a, **kw)
            if not str(dst).startswith(run):
                count["n"] += 1
                if count["n"] == 3:
                    raise SystemExit("crash")

        monkeypatch.setattr(os, "replace", crash)
    else:  # pragma: no cover
        raise AssertionError(state)
    with pytest.raises(SystemExit):
        run_apply(project, yes=True)
    monkeypatch.undo()
    j = journal(project)
    assert j is not None and j.state == state


def reach(state: str, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the run of ``project`` in journal ``state`` (git project)."""
    if state == "planned":
        return
    if state in ("staged", "tagged", "backed_up", "writing"):
        stop_at(state, project, monkeypatch)
    elif state == "written":
        assert run_apply(project, yes=True).code == 0
    elif state in ("committed", "merged"):
        assert run_apply(project, yes=True, commit=True).code == 0
        if state == "merged":
            git(project.root, "switch", "-q", "main")
            git(project.root, "merge", "-q", "--no-ff", "-m", "merge reorg", BRANCH)
    else:  # pragma: no cover
        raise AssertionError(state)


STATES = ["planned", "staged", "tagged", "backed_up", "writing", "written", "committed", "merged"]


# --------------------------------------------------------------------------- undo table


@pytest.mark.parametrize("state", STATES)
def test_undo_each_state(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    reach(state, project, monkeypatch)
    main_before = git(project.root, "rev-parse", "main").strip()
    prompter = FakePrompter(tty=False)
    res = run_undo(project, prompter)
    assert res.code == 0, res.issues
    assert tree_bytes(project.root) == before, state
    assert gitops.head_branch(project.root) == "main"
    assert not (run_path(project) / "stage").exists()
    j = journal(project)
    if state == "planned":
        assert j is None
        return
    assert j is not None and j.state == "undone"
    assert gitops.operation_in_progress(project.root) is None
    if state == "merged":
        # one revert commit on top of the merge; nothing else is pending
        assert git(project.root, "rev-parse", "HEAD~1").strip() == main_before
        assert git(project.root, "status", "--porcelain") == ""
    else:
        assert git(project.root, "rev-parse", "main").strip() == main_before
    if state != "staged":
        said = "\n".join(prompter.said)
        assert f"git branch -D {BRANCH}" in said
        assert f"git tag -d {TAG}" in said


def test_undo_staged_keeps_journal_entries(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    stop_at("staged", project, monkeypatch)
    assert (run_path(project) / "stage").is_dir()
    assert run_undo(project).code == 0
    j = journal(project)
    assert j is not None and j.state == "undone" and j.entries
    assert not gitops.ref_exists(project.root, f"refs/tags/{TAG}")


def test_undo_undone_again_is_noop(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    assert run_undo(project).code == 0
    before = tree_bytes(project.root)
    assert run_undo(project).code == 0
    assert tree_bytes(project.root) == before


INTERRUPTED = {
    "planned": False,
    "staged": True,
    "tagged": True,
    "backed_up": True,
    "writing": True,
    "written": False,
    "committed": False,
}


@pytest.mark.parametrize("state", sorted(INTERRUPTED))
def test_status_marks_interrupted_states(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    project = git_project(make_project)
    plan(project)
    reach(state, project, monkeypatch)
    rows = undo.status(project.root, RID)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == RID and row.state == state
    assert row.interrupted is INTERRUPTED[state]
    if state == "committed":
        assert row.commit
    else:
        assert row.commit is None
    assert undo.status(project.root) == rows


def test_status_lists_every_run_sorted(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    plan(project)
    plan2 = run_path(project).with_name(RID + "-2")
    shutil.copytree(run_path(project), plan2)
    manifest = plan2 / "manifest.json"
    manifest.write_bytes(
        manifest.read_bytes().replace(f'"id": "{RID}"'.encode(), f'"id": "{RID}-2"'.encode())
    )
    (project.root / ".declutter" / "reorg" / "not-a-run").mkdir()
    rows = undo.status(project.root)
    assert [r.id for r in rows] == [RID, RID + "-2"]
    assert all(r.state == "planned" and not r.interrupted for r in rows)


def test_status_missing_run_exit_1(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    assert undo.status(project.root) == ()
    with pytest.raises(ReorgError) as exc:
        undo.status(project.root, RID)
    assert exc.value.code == 1


# --------------------------------------------------------------------------- undo details


def test_undo_does_not_touch_file_edited_after_apply(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    edited = b"editado despues\n"
    (project.root / "ESTADO.md").write_bytes(edited)
    prompter = FakePrompter(tty=False)
    res = run_undo(project, prompter)
    assert res.code == 0, res.issues
    assert (project.root / "ESTADO.md").read_bytes() == edited
    assert (project.root / "CLAUDE.md").read_bytes() == TEXT.encode()
    assert not (project.root / "AGENTS.md").exists()
    assert gitops.head_branch(project.root) == "main"
    assert "ESTADO.md" in "\n".join(prompter.said)


def test_undo_exit_4_when_source_not_restored(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    edited = (project.root / "CLAUDE.md").read_bytes() + b"nota nueva\n"
    (project.root / "CLAUDE.md").write_bytes(edited)
    res = run_undo(project)
    assert res.code == 4
    assert "source_not_restored" in keys(res)
    assert (project.root / "CLAUDE.md").read_bytes() == edited
    j = journal(project)
    assert j is not None and j.state == "written"


def test_undo_recreates_claude_symlink(make_project: MakeProject) -> None:
    text = BASE + NOTE_L4
    project = make_project({"AGENTS.md": text})
    (project.root / "CLAUDE.md").symlink_to("AGENTS.md")
    plan(project)
    before = tree_bytes(project.root)
    res = run_apply(project, yes=True, no_git=True, replace_symlink=True)
    assert res.code == 0, res.issues
    assert not (project.root / "CLAUDE.md").is_symlink()
    res = run_undo(project)
    assert res.code == 0, res.issues
    claude = project.root / "CLAUDE.md"
    assert claude.is_symlink() and str(claude.readlink()) == "AGENTS.md"
    assert (project.root / "AGENTS.md").read_bytes() == text.encode()
    assert tree_bytes(project.root) == before


def test_undo_never_deletes_branch_or_tag(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    j = journal(project)
    assert j is not None and j.commit
    prompter = FakePrompter(tty=False)
    assert run_undo(project, prompter).code == 0
    assert gitops.ref_exists(project.root, f"refs/heads/{BRANCH}")
    assert gitops.ref_exists(project.root, f"refs/tags/{TAG}")
    assert git(project.root, "rev-parse", BRANCH).strip() == j.commit
    said = "\n".join(prompter.said)
    assert f"git branch -D {BRANCH}" in said and f"git tag -d {TAG}" in said


def test_undo_committed_switch_refused_lists_and_exit_1(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    agents = project.root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + b"cambio sin commit\n")
    res = run_undo(project)
    assert res.code == 1
    assert any(i.dest == "AGENTS.md" for i in res.issues)
    assert gitops.head_branch(project.root) == BRANCH
    j = journal(project)
    assert j is not None and j.state == "committed"


def test_undo_revert_conflict_aborts_exit_1(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    reach("merged", project, monkeypatch)
    agents = project.root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + b"editado en main\n")
    commit_all(project.root, "edit agents")
    head = git(project.root, "rev-parse", "HEAD").strip()
    after_edit = tree_bytes(project.root)
    res = run_undo(project)
    assert res.code == 1
    assert "revert_conflict" in keys(res)
    assert any(i.dest == "AGENTS.md" for i in res.issues)
    assert gitops.operation_in_progress(project.root) is None
    assert git(project.root, "rev-parse", "HEAD").strip() == head
    assert tree_bytes(project.root) == after_edit
    j = journal(project)
    assert j is not None and j.state == "committed"


def test_undo_merged_needs_confirmation(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    reach("merged", project, monkeypatch)
    head = git(project.root, "rev-parse", "HEAD").strip()
    res = run_undo(project, FakePrompter(tty=False), yes=False)
    assert res.code == 1 and "needs_yes" in keys(res)
    prompter = FakePrompter(tty=True, answers=["n"])
    res = run_undo(project, prompter, yes=False)
    assert res.code == 1 and "declined" in keys(res)
    assert prompter.prompts and "[s/N]" in prompter.prompts[0]
    assert git(project.root, "rev-parse", "HEAD").strip() == head
    assert git(project.root, "status", "--porcelain") == ""
    # yes to the revert, no to the revert commit: the revert stays staged, not committed
    prompter = FakePrompter(tty=True, answers=["s", "n"])
    res = run_undo(project, prompter, yes=False)
    assert res.code == 0, res.issues
    assert len(prompter.prompts) == 2
    assert git(project.root, "rev-parse", "HEAD").strip() == head
    assert (project.root / "CLAUDE.md").read_bytes() == TEXT.encode()
    j = journal(project)
    assert j is not None and j.state == "committed"


def test_no_git_undo_from_backup_and_original(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    index = project.root / "docs" / "historial" / "INDICE.md"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"# Historial\n\n- antes\n")
    out = plan(project)
    assert {o.path: o.mode for o in out.manifest.outputs}["docs/historial/INDICE.md"] == "extended"
    before = tree_bytes(project.root)
    assert run_apply(project, yes=True, no_git=True).code == 0
    assert index.read_bytes() != b"# Historial\n\n- antes\n"
    res = run_undo(project)
    assert res.code == 0, res.issues
    assert tree_bytes(project.root) == before
    j = journal(project)
    assert j is not None and j.state == "undone"


def test_crash_while_writing_then_undo_no_git(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = no_git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    run = str(run_path(project))
    real_replace = os.replace
    count = {"n": 0}

    def crash(src: Any, dst: Any, *a: Any, **kw: Any) -> None:
        real_replace(src, dst, *a, **kw)
        if not str(dst).startswith(run):
            count["n"] += 1
            if count["n"] == 2:
                raise SystemExit("crash")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(SystemExit):
        run_apply(project, yes=True, no_git=True)
    monkeypatch.undo()
    assert undo.status(project.root, RID)[0].interrupted
    assert run_undo(project).code == 0
    assert tree_bytes(project.root) == before


def test_undo_missing_run_exit_1(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    res = run_undo(project)
    assert res.code == 1 and "run_missing" in keys(res)


# --------------------------------------------------------------------------- prune


def _copies(project: Project) -> dict[str, bool]:
    run = run_path(project)
    return {name: (run / name).exists() for name in ("original", "backup", "stage", "dry-stage")}


KEPT = ("manifest.json", "placement.json", "journal.json", "plan.md", "report.json")


def test_prune_removes_copies_keeps_manifest(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    run = run_path(project)
    (run / "dry-stage").mkdir()
    (run / "stage").mkdir()
    assert _copies(project)["original"] and _copies(project)["backup"]
    before = tree_bytes(project.root)
    res = undo.prune(project, RID, FakePrompter(tty=False), yes=True)
    assert res.code == 0, res.issues
    assert not any(_copies(project).values())
    for name in KEPT:
        assert (run / name).is_file(), name
    assert tree_bytes(project.root) == before
    j = journal(project)
    assert j is not None and j.state == "committed"


@pytest.mark.parametrize("state", ["staged", "tagged", "backed_up", "writing", "written"])
def test_prune_refuses_in_writing_state(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    project = git_project(make_project)
    plan(project)
    reach(state, project, monkeypatch)
    res = undo.prune(project, RID, FakePrompter(tty=False), yes=True)
    assert res.code == 1 and "prune_state" in keys(res)
    assert _copies(project)["original"]


@pytest.mark.parametrize("state", ["planned", "undone"])
def test_prune_allowed_states(make_project: MakeProject, state: str) -> None:
    project = git_project(make_project)
    plan(project)
    if state == "undone":
        assert run_apply(project, yes=True).code == 0
        assert run_undo(project).code == 0
    res = undo.prune(project, RID, FakePrompter(tty=False), yes=True)
    assert res.code == 0, res.issues
    assert not _copies(project)["original"]


def test_prune_needs_yes_without_tty(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    res = undo.prune(project, RID, FakePrompter(tty=False), yes=False)
    assert res.code == 1 and "needs_yes" in keys(res)
    assert _copies(project)["original"]
    prompter = FakePrompter(tty=True, answers=["n"])
    res = undo.prune(project, RID, prompter, yes=False)
    assert res.code == 1 and "declined" in keys(res)
    assert _copies(project)["original"]
    prompter = FakePrompter(tty=True, answers=["s"])
    res = undo.prune(project, RID, prompter, yes=False)
    assert res.code == 0
    assert not _copies(project)["original"]


def test_prune_without_git_warns_no_copy_left(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, no_git=True).code == 0
    assert run_undo(project).code == 0
    prompter = FakePrompter(tty=False)
    res = undo.prune(project, RID, prompter, yes=True)
    assert res.code == 0
    assert any("git" in s for s in prompter.said)


# --------------------------------------------------------------------------- verify of a run


def test_verify_after_prune_reads_original_from_tag(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    assert undo.prune(project, RID, FakePrompter(tty=False), yes=True).code == 0
    assert not (run_path(project) / "original").exists()
    calls: list[tuple[str, str]] = []
    real_show = gitops.show_bytes

    def spy(root: Path, rev: str, path: str) -> bytes | None:
        calls.append((rev, path))
        return real_show(root, rev, path)

    monkeypatch.setattr(gitops, "show_bytes", spy)
    res = undo.verify_run(project, Config(), RID, rev=None, strict=True)
    assert res.ok, res.failures
    assert res.mode == "exact"
    assert any(TAG in rev and path == "CLAUDE.md" for rev, path in calls)
    res = undo.verify_run(project, Config(), RID, rev="HEAD", strict=True)
    assert res.ok, res.failures


def test_verify_run_before_prune_and_posterior_edit(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True, commit=True).code == 0
    res = undo.verify_run(project, Config(), RID, rev=None, strict=False)
    assert res.ok and res.mode == "exact", res.failures
    agents = project.root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes().replace(b"Intro del proyecto.", b"Intro editada."))
    res = undo.verify_run(project, Config(), RID, rev=None, strict=False)
    assert res.mode == "posterior" and res.ok, res.failures
    res = undo.verify_run(project, Config(), RID, rev=None, strict=True)
    assert not res.ok
    # the commit itself is still intact
    res = undo.verify_run(project, Config(), RID, rev="HEAD", strict=True)
    assert res.ok and res.mode == "exact", res.failures


def _rm_run(project: Project) -> None:
    shutil.rmtree(run_path(project))


@pytest.mark.parametrize(
    "how", ["never_planned", "folder_removed", "placement_missing"], ids=lambda s: s
)
def test_verify_without_run_folder_exit_1(make_project: MakeProject, how: str) -> None:
    project = git_project(make_project)
    if how != "never_planned":
        plan(project)
        assert run_apply(project, yes=True, commit=True).code == 0
        if how == "folder_removed":
            _rm_run(project)
        else:
            (run_path(project) / "placement.json").unlink()
    with pytest.raises(ReorgError) as exc:
        undo.verify_run(project, Config(), RID, rev=None, strict=False)
    assert exc.value.code == 1
    assert {i.key for i in exc.value.issues} == {"run_missing"}


def test_undo_output_contains_no_content(make_project: MakeProject) -> None:
    seed = "ZQX-UNDO-5512"
    text = BASE.replace("Intro del proyecto.", f"Intro {seed}.") + NOTE_L4
    project = git_project(make_project, text)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    (project.root / "CLAUDE.md").write_bytes(text.encode() + f"{seed} ajeno\n".encode())
    prompter = FakePrompter(tty=False)
    res = run_undo(project, prompter)
    assert res.code == 4
    blob = "\n".join([repr(i) for i in res.issues] + prompter.prompts + prompter.said)
    assert seed not in blob
