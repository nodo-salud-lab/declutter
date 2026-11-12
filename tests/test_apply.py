"""``declutter reorg apply``: journal and transaction (spec §8 G5, G9, §10.4, §11.1, §11.2, §11.5,
§12.3, §13).

Synthetic projects only (``make_project``); bytes written with ``write_bytes`` when exact.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.model import Config, Project, ProofFailure, ProofResult
from declutter_context.reorg import apply as ap
from declutter_context.reorg import gitops, proof
from declutter_context.reorg import manifest as mf
from declutter_context.reorg.apply import ApplyOptions, ApplyOutcome, Journal, JournalEntry

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
SEED = "ZQX-SEED-7731"

BASE = "# Demo\nIntro del proyecto.\n\n## Cómo correr\nEjecuta make test.\n"
NOTE_L4 = "\n## Nota 2026-10-03\nSolo registro de la sesión.\n"
K_L4 = "\n## Sesión 2026-10-05\n- Nunca uses la base de prueba.\n"
L1C = "\n## Hooks de Claude Code\nConfigura los hooks y skills en settings.json.\n"
G2 = "\n## Datos de pacientes\nTodo dato es confidencial.\n"
GUARDED = "\n## Sesión 2026-10-09\n- Nunca subas el password al repo.\n"
TEXT = BASE + NOTE_L4 + G2


# --------------------------------------------------------------------------- helpers


class FakePrompter:
    def __init__(self, tty: bool, answers: Sequence[str] = ()) -> None:
        self.tty = tty
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def is_tty(self) -> bool:
        return self.tty

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def say(self, text: str) -> None:
        self.said.append(text)


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file below ``root`` except ``.git/`` and ``.declutter/reorg/``."""
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        if rel == ".git" or rel.startswith(".git" + os.sep):
            dirnames[:] = []
            continue
        if rel.startswith(str(Path(".declutter", "reorg"))):
            dirnames[:] = []
            continue
        for d in dirnames:
            out[os.path.relpath(Path(dirpath) / d, root) + "/"] = b""
        for f in filenames:
            p = Path(dirpath) / f
            key = os.path.relpath(p, root)
            out[key] = (b"->" + str(p.readlink()).encode()) if p.is_symlink() else p.read_bytes()
    return out


def git(root: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return res.stdout.decode()


def commit_all(root: Path, msg: str = "c") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def plan(project: Project, cfg: Config | None = None) -> mf.PlanOutcome:
    out = mf.plan_project(mf.PlanRequest(project=project, cfg=cfg or Config(), lang="es", now=NOW))
    assert out.manifest.id == RID
    return out


def run_apply(
    project: Project,
    prompter: FakePrompter | None = None,
    cfg: Config | None = None,
    **opts: Any,
) -> ApplyOutcome:
    return ap.apply(
        project,
        cfg or Config(),
        RID,
        ApplyOptions(**opts),
        prompter or FakePrompter(tty=False),
        now=NOW,
        lang="es",
    )


def run_path(project: Project) -> Path:
    return project.root / ".declutter" / "reorg" / RID


def journal(project: Project) -> Journal | None:
    return ap.read_journal(run_path(project))


def keys(out: ApplyOutcome) -> set[str]:
    return {i.key for i in out.issues}


def git_project(make_project: MakeProject, text: str = TEXT) -> Project:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(text.encode())
    commit_all(project.root)
    return project


def no_git_project(make_project: MakeProject, text: str = TEXT) -> Project:
    project = make_project()
    (project.root / "CLAUDE.md").write_bytes(text.encode())
    return project


def guarded_project(make_project: MakeProject) -> Project:
    """A plan with one ``guard_ack`` (G9) that passes ``check``."""
    project = git_project(make_project, BASE + GUARDED)
    out = plan(project)
    u = next(u for u in out.manifest.units if u.heading == "Sesión 2026-10-09")
    res = mf.set_override(
        project,
        Config(),
        RID,
        u.heading_key,
        accept=True,
        reason="revisada a mano",
        not_safety=u.blocks[0].id,
        ack_reason="falso positivo del campo",
        now=NOW,
    )
    assert res.code == 0, res.issues
    return project


def all_text(out: ApplyOutcome, prompter: FakePrompter) -> str:
    parts = [repr(i) for i in out.issues] + prompter.prompts + prompter.said
    return "\n".join(parts)


# --------------------------------------------------------------------------- happy path


def test_apply_happy_path_commit_and_verify_rev(make_project: MakeProject) -> None:
    project = git_project(make_project)
    base = git(project.root, "rev-parse", "main").strip()
    out = plan(project)
    assert out.code == 0, out.issues
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 0, res.issues
    j = journal(project)
    assert j is not None and j.state == "committed" and j.commit
    assert j.base_branch == "main"
    assert all(e.status == "done" for e in j.entries)
    assert gitops.head_branch(project.root) == f"declutter/reorg-{RID}"
    assert gitops.ref_exists(project.root, f"refs/tags/declutter/pre-reorg-{RID}")
    assert git(project.root, "rev-parse", "main").strip() == base
    msg = git(project.root, "log", "-1", "--format=%B")
    manifest_sha = ap.sha256_hex((run_path(project) / "manifest.json").read_bytes())
    assert msg.startswith(f"declutter(reorg {RID}): CLAUDE.md → 5 capas, ")
    assert f"Declutter-Manifest: {RID} sha256={manifest_sha}" in msg
    assert f"Declutter-Source: CLAUDE.md sha256={out.manifest.sources[0].sha256}" in msg
    changed = set(git(project.root, "diff", "--name-only", "main", "HEAD").split())
    assert changed == {o.path for o in out.manifest.outputs}
    assert (run_path(project) / "placement.json").is_file()


def test_stage_removed_after_disk_verify(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    res = run_apply(project, yes=True)
    assert res.code == 0, res.issues
    assert not (run_path(project) / "stage").exists()
    j = journal(project)
    assert j is not None and j.state == "written"
    assert (run_path(project) / "backup" / "CLAUDE.md").read_bytes() == TEXT.encode()


def test_apply_twice_same_id_exit_1(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    again = run_apply(project, yes=True)
    assert again.code == 1 and "already_applied" in keys(again)


def test_apply_dry_run_leaves_no_dry_stage_and_touches_nothing(
    make_project: MakeProject,
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    res = run_apply(project, dry_run=True)
    assert res.code == 0, res.issues
    assert tree_bytes(project.root) == before
    run = run_path(project)
    assert not (run / "dry-stage").exists()
    assert not (run / "stage").exists()
    assert not (run / "journal.json").exists()
    assert not (run / "backup").exists()
    assert gitops.head_branch(project.root) == "main"
    assert not gitops.ref_exists(project.root, f"refs/tags/declutter/pre-reorg-{RID}")


# --------------------------------------------------------------------------- write order


def _entry(path: str, mode: str) -> JournalEntry:
    return JournalEntry(path, mode, "absent", "0" * 64, "pending")


def test_write_order_claude_md_is_last_replace(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        _entry("CLAUDE.md", "rewritten"),
        _entry("AGENTS.md", "rewritten"),
        _entry(".declutter/pins.json", "control"),
        _entry("docs/historial/INDICE.md", "extended"),
        _entry("ESTADO.md", "created"),
        _entry("sub/CLAUDE.md", "rewritten"),
    ]
    order = [e.path for e in ap.write_order(entries, {"CLAUDE.md", "AGENTS.md", "sub/CLAUDE.md"})]
    assert order == [
        "ESTADO.md",
        "docs/historial/INDICE.md",
        ".declutter/pins.json",
        "sub/CLAUDE.md",
        "AGENTS.md",
        "CLAUDE.md",
    ]

    project = git_project(make_project)
    plan(project)
    seen: list[str] = []
    real = os.replace
    reorg = str(run_path(project))

    def spy(src: Any, dst: Any, *a: Any, **k: Any) -> None:
        if not str(dst).startswith(reorg):
            seen.append(os.path.relpath(str(dst), project.root))
        real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", spy)
    assert run_apply(project, yes=True).code == 0
    assert seen[-1] == "CLAUDE.md"
    assert len(seen) == len(set(seen))


# --------------------------------------------------------------------------- fault injection

N_OUTPUTS = 10
FAULTS = [
    "stage_write",
    "stage_verify",
    "tag",
    "switch_create",
    "backup",
    "fsync",
    "disk_verify",
    *[f"replace_{k}" for k in range(N_OUTPUTS)],
]


def _inject(fault: str, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    run = str(run_path(project))
    real_replace = os.replace
    state = {"armed": False, "fired": False, "n": 0}

    def once() -> bool:
        if state["fired"]:
            return False
        state["fired"] = True
        return True

    if fault in ("stage_write", "backup") or fault.startswith("replace_"):
        target = {"stage_write": "stage", "backup": "backup"}.get(fault)
        k = int(fault.split("_")[1]) if fault.startswith("replace_") else -1

        def fake_replace(src: Any, dst: Any, *a: Any, **kw: Any) -> None:
            d = str(dst)
            if target is not None and d.startswith(str(Path(run, target))) and once():
                raise OSError("injected")
            if target is None and not d.startswith(run):
                if state["n"] == k and once():
                    raise OSError("injected")
                state["n"] += 1
            real_replace(src, dst, *a, **kw)

        monkeypatch.setattr(os, "replace", fake_replace)
    elif fault in ("stage_verify", "disk_verify"):
        real_verify = proof.verify
        which = proof.CHECKS_STAGE if fault == "stage_verify" else proof.CHECKS_DISK

        def fake_verify(*a: Any, **kw: Any) -> ProofResult:
            if kw.get("checks") == which and once():
                if fault == "stage_verify":
                    raise RuntimeError("injected")
                fail = ProofFailure("C2", None, None, None, "AGENTS.md", (1, 1), "injected")
                return ProofResult(ok=False, mode="exact", failures=(fail,))
            return real_verify(*a, **kw)

        monkeypatch.setattr(proof, "verify", fake_verify)
    elif fault in ("tag", "switch_create"):

        def fake_git(root: Path, name: str) -> None:
            raise gitops.GitError(fault, 128)

        monkeypatch.setattr(gitops, fault, fake_git)
    elif fault == "fsync":
        real_switch = gitops.switch_create
        real_fsync = os.fsync

        def arm(root: Path, name: str) -> None:
            real_switch(root, name)
            state["armed"] = True

        def fake_fsync(fd: int) -> None:
            if state["armed"]:
                state["n"] += 1
                if state["n"] == 3 and once():
                    raise OSError("injected")
            real_fsync(fd)

        monkeypatch.setattr(gitops, "switch_create", arm)
        monkeypatch.setattr(os, "fsync", fake_fsync)
    else:  # pragma: no cover
        raise AssertionError(fault)


@pytest.mark.parametrize("fault", FAULTS)
def test_fault_injection_leaves_tree_identical_on_base_branch(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    project = git_project(make_project)
    out = plan(project)
    assert len(out.manifest.outputs) == N_OUTPUTS
    before = tree_bytes(project.root)
    _inject(fault, project, monkeypatch)
    res = run_apply(project, yes=True, commit=True)
    monkeypatch.undo()
    assert res.code in (2, 4), (fault, res.issues)
    assert tree_bytes(project.root) == before, fault
    assert gitops.head_branch(project.root) == "main"
    assert not (run_path(project) / "stage").exists()


def test_crash_after_replace_before_done_then_undo_restores(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    from declutter_context.reorg import undo

    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
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
    with pytest.raises(SystemExit):
        run_apply(project, yes=True)
    monkeypatch.undo()
    j = journal(project)
    assert j is not None and j.state == "writing"
    assert any(e.status == "writing" for e in j.entries)
    res = undo.undo(project, RID, FakePrompter(tty=False), yes=True, lang="es")
    assert res.code == 0
    assert tree_bytes(project.root) == before


def test_keyboard_interrupt_during_write_restores_and_returns_130(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    run = str(run_path(project))
    real_replace = os.replace
    count = {"n": 0}

    def interrupt(src: Any, dst: Any, *a: Any, **kw: Any) -> None:
        if not str(dst).startswith(run):
            count["n"] += 1
            if count["n"] == 3:
                raise KeyboardInterrupt
        real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", interrupt)
    res = run_apply(project, yes=True)
    monkeypatch.undo()
    assert res.code == 130
    assert tree_bytes(project.root) == before
    assert gitops.head_branch(project.root) == "main"


# --------------------------------------------------------------------------- CAS and backups


def _seed_in_write_order(monkeypatch: pytest.MonkeyPatch, path: Path, data: bytes) -> None:
    real = ap.write_order

    def seeded(entries: Sequence[JournalEntry], sources: Any) -> list[JournalEntry]:
        path.write_bytes(data)
        return real(entries, sources)

    monkeypatch.setattr(ap, "write_order", seeded)


def test_cas_concurrent_edit_of_claude_md_aborts_4_and_keeps_foreign_edit(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    foreign = TEXT.encode() + b"edit ajena\n"
    _seed_in_write_order(monkeypatch, project.root / "CLAUDE.md", foreign)
    res = run_apply(project, yes=True)
    assert res.code == 4 and "concurrent_edit" in keys(res)
    after = tree_bytes(project.root)
    assert after.pop("CLAUDE.md") == foreign
    before.pop("CLAUDE.md")
    assert after == before
    assert gitops.head_branch(project.root) == "main"


def test_cas_concurrent_edit_of_extended_index_aborts_4(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    index = project.root / "docs" / "historial" / "INDICE.md"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"# Historial\n\n- antes\n")
    commit_all(project.root)
    out = plan(project)
    modes = {o.path: o.mode for o in out.manifest.outputs}
    assert modes["docs/historial/INDICE.md"] == "extended"
    before = tree_bytes(project.root)
    foreign = b"# Historial\n\n- editado por otra sesion\n"
    _seed_in_write_order(monkeypatch, index, foreign)
    res = run_apply(project, yes=True)
    assert res.code == 4 and "concurrent_edit" in keys(res)
    after = tree_bytes(project.root)
    assert after.pop("docs/historial/INDICE.md") == foreign
    before.pop("docs/historial/INDICE.md")
    assert after == before
    assert gitops.head_branch(project.root) == "main"


def test_backup_sha_mismatch_aborts_4(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = git_project(make_project)
    plan(project)
    real = gitops.switch_create
    foreign = TEXT.encode() + b"otra\n"

    def edit_then_switch(root: Path, name: str) -> None:
        (root / "CLAUDE.md").write_bytes(foreign)
        real(root, name)

    monkeypatch.setattr(gitops, "switch_create", edit_then_switch)
    res = run_apply(project, yes=True)
    assert res.code == 4 and "concurrent_edit" in keys(res)
    assert (project.root / "CLAUDE.md").read_bytes() == foreign
    assert not (project.root / "AGENTS.md").exists()
    assert gitops.head_branch(project.root) == "main"


# --------------------------------------------------------------------------- commit and hooks


def test_commit_only_does_not_include_seeded_foreign_change(make_project: MakeProject) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(TEXT.encode())
    (project.root / "otro.txt").write_bytes(b"uno\n")
    commit_all(project.root)
    plan(project)
    (project.root / "otro.txt").write_bytes(b"dos\n")
    git(project.root, "add", "otro.txt")
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 0, res.issues
    changed = set(git(project.root, "diff", "--name-only", "main", "HEAD").split())
    assert "otro.txt" not in changed
    assert (project.root / "otro.txt").read_bytes() == b"dos\n"
    assert "otro.txt" in git(project.root, "status", "--porcelain")


def _hook(project: Project, body: str) -> None:
    hook = project.root / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)


def test_precommit_hook_trimming_spaces_fails_verify_rev_4(make_project: MakeProject) -> None:
    text = "# Demo\nIntro con salto forzado.  \n\n## Cómo correr\nEjecuta make test.\n" + NOTE_L4
    project = git_project(make_project, text)
    plan(project)
    _hook(
        project,
        "sed 's/[ ]*$//' AGENTS.md > AGENTS.md.tmp && mv AGENTS.md.tmp AGENTS.md\n"
        "git add AGENTS.md\n",
    )
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 4, res.issues
    j = journal(project)
    assert j is not None and j.state == "committed" and j.commit
    assert gitops.head_branch(project.root) == f"declutter/reorg-{RID}"
    assert any("undo" in i.detail for i in res.issues)


def test_precommit_hook_rejecting_commit_leaves_written_exit_1(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    _hook(project, "exit 1\n")
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 1 and "commit_rejected" in keys(res)
    j = journal(project)
    assert j is not None and j.state == "written" and j.commit is None


def test_resume_commit_from_written(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    _hook(project, "exit 1\n")
    assert run_apply(project, yes=True, commit=True).code == 1
    (project.root / ".git" / "hooks" / "pre-commit").unlink()
    # resume without TTY and without --yes -> 1
    assert run_apply(project, commit=True).code == 1
    prompter = FakePrompter(tty=True)
    res = run_apply(project, prompter, yes=True, commit=True)
    assert res.code == 0, res.issues
    assert not any("confirm" in p for p in prompter.prompts)
    j = journal(project)
    assert j is not None and j.state == "committed" and j.commit


def test_resume_commit_fails_4_when_bytes_changed(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    agents = project.root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes().replace(b"Intro del proyecto.", b"Intro cambiada."))
    res = run_apply(project, yes=True, commit=True)
    assert res.code == 4
    j = journal(project)
    assert j is not None and j.state == "written"


@pytest.mark.parametrize("how", ["autocrlf", "gitattributes", "text_no_eol"])
def test_autocrlf_or_gitattributes_exit_1_before_writing(
    make_project: MakeProject, how: str
) -> None:
    project = git_project(make_project)
    plan(project)
    if how == "autocrlf":
        git(project.root, "config", "core.autocrlf", "true")
    elif how == "gitattributes":
        # eol=lf would be the identity on these LF bytes (Enmienda 05-11): crlf refuses.
        (project.root / ".gitattributes").write_bytes(b"CLAUDE.md text eol=crlf\n")
    else:
        (project.root / ".gitattributes").write_bytes(b"CLAUDE.md text\n")
    before = tree_bytes(project.root)
    res = run_apply(project, yes=True)
    assert res.code == 1 and "git_normalizes" in keys(res)
    assert tree_bytes(project.root) == before
    assert journal(project) is None


# --------------------------------------------------------------------------- confirmations


def test_apply_commit_without_tty_and_without_yes_exit_1(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    res = run_apply(project, commit=True)
    assert res.code == 1 and "needs_yes" in keys(res)
    assert tree_bytes(project.root) == before


def test_tty_apply_question_declined_writes_nothing(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    before = tree_bytes(project.root)
    prompter = FakePrompter(tty=True, answers=["n"])
    res = run_apply(project, prompter)
    assert res.code == 1
    assert prompter.prompts and "[s/N]" in prompter.prompts[0]
    assert tree_bytes(project.root) == before


def test_tty_commit_question(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    prompter = FakePrompter(tty=True, answers=["s", "s"])
    res = run_apply(project, prompter)
    assert res.code == 0, res.issues
    assert any(f"declutter/reorg-{RID}" in p for p in prompter.prompts)
    j = journal(project)
    assert j is not None and j.state == "committed"


def test_g9_yes_without_tty_with_guard_ack_exit_1(make_project: MakeProject) -> None:
    project = guarded_project(make_project)
    before = tree_bytes(project.root)
    res = run_apply(project, yes=True)
    assert res.code == 1 and "g9_tty" in keys(res)
    assert tree_bytes(project.root) == before


def test_g9_tty_yes_still_requires_confirmo(make_project: MakeProject) -> None:
    project = guarded_project(make_project)
    prompter = FakePrompter(tty=True, answers=["confirmo"])
    res = run_apply(project, prompter, yes=True)
    assert res.code == 0, res.issues
    # --yes answers only "¿Aplicar?": G9 is still asked, and so is the commit question.
    assert "confirmo" in prompter.prompts[0]
    assert not any("Aplicar" in p for p in prompter.prompts)
    m = mf.loads((run_path(project) / "manifest.json").read_bytes())
    block = next(b for u in m.units for b in u.blocks if b.guard_ack)
    listed = "\n".join(prompter.said)
    assert block.id in listed and block.key in listed and "secrets" in listed


def test_g9_wrong_word_aborts(make_project: MakeProject) -> None:
    project = guarded_project(make_project)
    before = tree_bytes(project.root)
    res = run_apply(project, FakePrompter(tty=True, answers=["s", "si"]))
    assert res.code == 1 and "g9_not_confirmed" in keys(res)
    assert tree_bytes(project.root) == before
    assert journal(project) is None


@pytest.mark.parametrize("extra", [L1C, K_L4])
def test_apply_yes_with_unconfirmed_l1c_or_demoted_exit_4(
    make_project: MakeProject, extra: str
) -> None:
    project = git_project(make_project, BASE + extra)
    plan(project)
    before = tree_bytes(project.root)
    res = run_apply(project, yes=True)
    assert res.code == 4
    assert keys(res) & {"check.15", "check.16"}
    assert tree_bytes(project.root) == before


# --------------------------------------------------------------------------- guards and modes


def test_secret_in_existing_file_to_extend_exit_5_no_backup(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    index = project.root / "docs" / "historial" / "INDICE.md"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"# Historial\n\n- antes\n")
    out = plan(project)
    assert {o.path: o.mode for o in out.manifest.outputs}["docs/historial/INDICE.md"] == "extended"
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    index.write_bytes(f"# Historial\n\n- antes {secret}\n".encode())
    prompter = FakePrompter(tty=False)
    res = run_apply(project, prompter, yes=True, no_git=True)
    assert res.code == 5, res.issues
    assert not (run_path(project) / "backup").exists()
    assert not (project.root / "AGENTS.md").exists()
    assert secret not in all_text(res, prompter)


def test_not_repo_requires_no_git_and_no_git_works(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    plan(project)
    res = run_apply(project, yes=True)
    assert res.code == 1 and "not_repo" in keys(res)
    res = run_apply(project, yes=True, no_git=True)
    assert res.code == 0, res.issues
    j = journal(project)
    assert j is not None and j.state == "written" and j.base_branch is None
    assert (project.root / "AGENTS.md").is_file()


def test_no_git_flag_in_repo_exit_1(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    res = run_apply(project, yes=True, no_git=True)
    assert res.code == 1 and "no_git_in_repo" in keys(res)


def test_symlink_claude_requires_replace_symlink_and_journal_records_target(
    make_project: MakeProject,
) -> None:
    project = make_project({"AGENTS.md": BASE + NOTE_L4})
    (project.root / "CLAUDE.md").symlink_to("AGENTS.md")
    plan(project)
    res = run_apply(project, yes=True, no_git=True)
    assert res.code == 1 and "replace_symlink" in keys(res)
    assert (project.root / "CLAUDE.md").is_symlink()
    res = run_apply(project, yes=True, no_git=True, replace_symlink=True)
    assert res.code == 0, res.issues
    j = journal(project)
    assert j is not None and j.replaced_symlink == "AGENTS.md"
    claude = project.root / "CLAUDE.md"
    assert not claude.is_symlink() and claude.is_file()


def test_ratchet_would_fail_requires_relax_exit_3(make_project: MakeProject) -> None:
    project = no_git_project(make_project)
    ctl = project.root / ".declutter"
    ctl.mkdir()
    (ctl / "ratchet.json").write_bytes(
        ratchet.ratchet_dumps(ratchet.RatchetState(s1_max=1, s3_max_pct=1))
    )
    out = plan(project)
    assert ratchet.WOULD_FAIL in out.manifest.warnings
    res = run_apply(project, yes=True, no_git=True)
    assert res.code == 3 and "ratchet_would_fail" in keys(res)
    assert not (project.root / "AGENTS.md").exists()


def test_output_contains_no_content(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = BASE.replace("Intro del proyecto.", f"Intro {SEED}.") + NOTE_L4
    project = git_project(make_project, text)
    plan(project)
    foreign = text.encode() + f"{SEED} ajeno\n".encode()
    _seed_in_write_order(monkeypatch, project.root / "CLAUDE.md", foreign)
    prompter = FakePrompter(tty=False)
    res = run_apply(project, prompter, yes=True)
    assert res.code == 4
    assert SEED not in all_text(res, prompter)


# --------------------------------------------------------------------------- journal and restore


def test_journal_roundtrip_is_deterministic(tmp_path: Path) -> None:
    j = Journal(
        state="writing",
        base_branch="main",
        entries=(JournalEntry("AGENTS.md", "created", "absent", "a" * 64, "done"),),
        replaced_symlink=None,
        commit=None,
        created_dirs=("docs", "docs/historial"),
    )
    (tmp_path / ".declutter" / "reorg" / RID).mkdir(parents=True)
    run = tmp_path / ".declutter" / "reorg" / RID
    assert ap.read_journal(run) is None
    ap.write_journal(run, j)
    first = (run / "journal.json").read_bytes()
    assert ap.read_journal(run) == j
    ap.write_journal(run, j)
    assert (run / "journal.json").read_bytes() == first


def test_restore_leaves_foreign_edit_and_lists_it(make_project: MakeProject) -> None:
    project = git_project(make_project)
    plan(project)
    assert run_apply(project, yes=True).code == 0
    j = journal(project)
    assert j is not None
    (project.root / "ESTADO.md").write_bytes(b"editado despues\n")
    untouched = ap.restore(project.root, run_path(project), j)
    assert untouched == ("ESTADO.md",)
    assert (project.root / "CLAUDE.md").read_bytes() == TEXT.encode()
    assert not (project.root / "AGENTS.md").exists()
    assert (project.root / "ESTADO.md").read_bytes() == b"editado despues\n"
    assert not (project.root / "docs").exists()
