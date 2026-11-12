"""``declutter reorg undo``, ``status``, ``prune`` and ``verify`` of a run (spec §6.1, §7, §11.4,
§11.5, §13).

``undo`` follows the table of §11.5 by journal state. It never deletes the branch
``declutter/reorg-<id>`` nor the tag ``declutter/pre-reorg-<id>``: it prints the commands for the
human.
``status`` reports every run and marks the intermediate states as interrupted. ``prune``
deletes the literal local copies (``original/``, ``backup/``, ``stage/``, ``dry-stage/``) and
keeps what ``verify`` needs. ``verify_run`` runs the proof in posterior mode over the disk
or a revision; after ``prune`` it reads the original from the tag (§7).

Issues and messages carry metadata only (keys, paths, ids, refs); never file content (G6).
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .. import fsutil
from ..model import Config, Placement, Project, ProofResult
from . import (
    EXIT_INTERNAL,
    EXIT_PROOF,
    EXIT_USAGE,
    Issue,
    Prompter,
    ReorgError,
    gitops,
    proof,
    resolve_code,
)
from . import manifest as mf
from .apply import (
    ABSENT,
    PLACEMENT_NAME,
    ApplyOutcome,
    Journal,
    read_journal,
    reconcile_commit,
    restore,
    sha256_hex,
    write_journal,
)

__all__ = [
    "INTERRUPTED",
    "PRUNABLE",
    "PRUNED_DIRS",
    "RunStatus",
    "prune",
    "status",
    "undo",
    "verify_run",
]

INTERRUPTED = frozenset({"staged", "tagged", "backed_up", "writing"})
PRUNABLE = frozenset({"planned", "committed", "undone"})
PRUNED_DIRS = ("original", "backup", "stage", "dry-stage")

_YES = frozenset({"s", "si", "sí", "y", "yes"})
_TEXT: Mapping[str, Mapping[str, str]] = {
    "es": {
        "cmds": "La rama y la marca se conservan; para borrarlas a mano:",
        "untouched": "No se tocó (cambió después de aplicar): {path}",
        "recreated": "Se recreó {path} con «*» (prune borró su copia de respaldo).",
        "revert_q": "¿Revertir {sha} en {branch}? [s/N] ",
        "revert_commit_q": "¿Commit de la reversión en {branch}? [s/N] ",
        "revert_staged": "La reversión quedó preparada sin commit; revisa y haz commit a mano.",
        "prune_q": "¿Borrar las copias locales de {id} (original/, backup/)? [s/N] ",
        "prune_no_git": (
            "Sin git no hay marca: tras prune no queda copia del original fuera del árbol."
        ),
        "revert_subject": "Revertir declutter(reorg {id})",
    },
    "en": {
        "cmds": "The branch and the tag are kept; to delete them by hand:",
        "untouched": "Left untouched (changed after apply): {path}",
        "recreated": "Recreated {path} with '*' (prune deleted its backup copy).",
        "revert_q": "Revert {sha} on {branch}? [y/N] ",
        "revert_commit_q": "Commit the revert on {branch}? [y/N] ",
        "revert_staged": "The revert is staged without a commit; review and commit by hand.",
        "prune_q": "Delete the local copies of {id} (original/, backup/)? [y/N] ",
        "prune_no_git": (
            "Without git there is no tag: after prune no copy of the original is left "
            "outside the tree."
        ),
        "revert_subject": "Revert declutter(reorg {id})",
    },
}


def _tx(lang: str, key: str, **kw: object) -> str:
    table = _TEXT.get(lang, _TEXT["en"])
    return table[key].format(**kw) if kw else table[key]


def _outcome(issues: Iterable[Issue], j: Journal | None) -> ApplyOutcome:
    items = tuple(dict.fromkeys(issues))
    return ApplyOutcome(code=resolve_code(i.code for i in items), issues=items, journal=j)


def _branch(reorg_id: str) -> str:
    return f"declutter/reorg-{reorg_id}"


def _tag(reorg_id: str) -> str:
    return f"declutter/pre-reorg-{reorg_id}"


def _yes_no(prompter: Prompter, prompt: str) -> bool:
    return prompter.ask(prompt).strip().lower() in _YES


def _read(root: Path, rel: str) -> bytes | None:
    try:
        return fsutil.safe_read_bytes(root / rel, root)
    except fsutil.UnsafeReadError:
        return None


def _rmtree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


def _load(root: Path, reorg_id: str) -> tuple[mf.Manifest, Path, Journal | None]:
    m, run = mf.load_run(root, reorg_id)
    return m, run, read_journal(run)


# --------------------------------------------------------------------------- status


@dataclass(frozen=True)
class RunStatus:
    id: str
    state: str
    interrupted: bool
    commit: str | None


def _row(root: Path, reorg_id: str) -> RunStatus:
    _m, _run, j = _load(root, reorg_id)
    state = "planned" if j is None else j.state
    return RunStatus(
        id=reorg_id,
        state=state,
        interrupted=state in INTERRUPTED,
        commit=None if j is None else j.commit,
    )


def status(root: Path, reorg_id: str | None = None) -> tuple[RunStatus, ...]:
    """Every run (by id) with its journal state; ``interrupted`` for the intermediate
    states (spec §11.5). With ``reorg_id``, only that run (missing -> ``run_missing``)."""
    if reorg_id is not None:
        return (_row(root, reorg_id),)
    folder = root / mf.RUN_DIR
    if not folder.is_dir() or folder.is_symlink():
        return ()
    rows: list[RunStatus] = []
    for child in sorted(folder.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            mf.run_dir(root, child.name)
        except ReorgError:
            continue
        rows.append(_row(root, child.name))
    return tuple(rows)


# --------------------------------------------------------------------------- undo helpers


def _say_refs(prompter: Prompter, root: Path, reorg_id: str, lang: str) -> None:
    """Print the commands that delete the branch and the tag (never run them)."""
    refs = [
        (f"refs/heads/{_branch(reorg_id)}", f"git branch -D {_branch(reorg_id)}"),
        (f"refs/tags/{_tag(reorg_id)}", f"git tag -d {_tag(reorg_id)}"),
    ]
    cmds = [cmd for ref, cmd in refs if gitops.ref_exists(root, ref)]
    if cmds:
        prompter.say(_tx(lang, "cmds"))
        for cmd in cmds:
            prompter.say(f"  {cmd}")


def _sources_issues(root: Path, m: mf.Manifest) -> list[Issue]:
    issues: list[Issue] = []
    for s in m.sources:
        data = _read(root, s.path)
        if data is None or sha256_hex(data) != s.sha256:
            issues.append(Issue(code=EXIT_PROOF, key="source_not_restored", source=s.path))
    return issues


_PLAN_GITIGNORE = b"*\n"  # what ``plan`` writes when there is none (§6.1)


def _restore_ignored_prev(root: Path, run: Path, j: Journal) -> tuple[str, ...]:
    """After git removed files the reorg commit added: bring back the ones that existed
    before ``apply`` but were not tracked (e.g. ``.declutter/.gitignore`` written by
    ``plan``), from ``backup/`` and only at their previous sha.

    After ``prune`` there is no ``backup/``: ``.declutter/.gitignore`` is then written
    again as ``plan`` writes it (``*``), so that ``.declutter/`` stays ignored (§6.1).
    Returns the paths recreated that way when that is not their exact previous content.
    """
    approximated: list[str] = []
    for e in j.entries:
        if e.prev_sha256 == ABSENT or os.path.lexists(root / e.path):
            continue
        data = _read(root, (run / "backup" / e.path).relative_to(root).as_posix())
        if data is None or sha256_hex(data) != e.prev_sha256:
            if e.path != mf.GITIGNORE_REL:
                continue
            data = _PLAN_GITIGNORE
            if sha256_hex(data) != e.prev_sha256:
                approximated.append(e.path)
        try:
            fsutil.atomic_write_bytes(root / e.path, data, root)
        except fsutil.UnsafeWriteError:
            continue
    return tuple(approximated)


def _unstage_added(root: Path, j: Journal) -> None:
    """Take out of the index the journal paths that are not in HEAD but were staged (a
    ``git add`` of step 8 whose commit was rejected or interrupted), so the switch to the
    base branch does not carry them as staged additions (§11.2 step 8, §11.5)."""
    if not gitops.is_repo(root):
        return
    paths = [
        e.path
        for e in j.entries
        if gitops.show_bytes(root, "HEAD", e.path) is None
        and gitops.show_bytes(root, "", e.path) is not None
    ]
    gitops.unstage(root, paths)


def _switch_base(root: Path, j: Journal, m: mf.Manifest) -> list[Issue]:
    """``git switch <base>`` when on another branch; a refusal lists the dirty outputs."""
    base = j.base_branch
    if base is None or not gitops.is_repo(root):
        return []
    try:
        if gitops.head_branch(root) != base:
            gitops.switch(root, base)
    except gitops.GitError as exc:
        paths = sorted({o.path for o in m.outputs} | {e.path for e in j.entries})
        dirty: tuple[str, ...] = ()
        with contextlib.suppress(gitops.GitError):
            dirty = gitops.dirty(root, paths)
        issues = [Issue(code=EXIT_USAGE, key="switch_refused", dest=p) for p in dirty]
        return issues or [Issue(code=EXIT_USAGE, key="switch_refused", detail=str(exc))]
    return []


def _save_undone(run: Path, j: Journal) -> Journal:
    j = replace(j, state="undone")
    write_journal(run, j)
    return j


# --------------------------------------------------------------------------- undo by state


@dataclass
class _Undo:
    project: Project
    reorg_id: str
    prompter: Prompter
    yes: bool
    lang: str
    m: mf.Manifest
    run: Path

    @property
    def root(self) -> Path:
        return self.project.root

    def refs(self) -> None:
        if gitops.is_repo(self.root):
            _say_refs(self.prompter, self.root, self.reorg_id, self.lang)


def _undo_staged(u: _Undo, j: Journal) -> ApplyOutcome:
    """Delete ``stage/``. ``apply`` records the base branch before the tag and the switch,
    so a crash right after either still goes back and prints the commands (§11.2 step 4;
    acceptance criterion 11)."""
    _rmtree(u.run / "stage")
    issues = _switch_base(u.root, j, u.m)
    u.refs()
    if issues:
        return _outcome(issues, j)
    return _outcome([], _save_undone(u.run, j))


def _undo_tagged(u: _Undo, j: Journal) -> ApplyOutcome:
    _rmtree(u.run / "stage")
    issues = _switch_base(u.root, j, u.m)
    u.refs()
    if issues:
        return _outcome(issues, j)
    return _outcome([], _save_undone(u.run, j))


def _undo_writing(u: _Undo, j: Journal) -> ApplyOutcome:
    """``writing``/``written`` (and ``--no-git``): restore every journal entry, recreate a
    replaced link, switch back; 4 if a source does not return to its sha. A file whose
    sha changed after apply is left untouched and listed (spec §11.5)."""
    _unstage_added(u.root, j)
    untouched = restore(u.root, u.run, j)
    _rmtree(u.run / "stage")
    issues = _switch_base(u.root, j, u.m)
    for path in untouched:
        u.prompter.say(_tx(u.lang, "untouched", path=path))
    issues += _sources_issues(u.root, u.m)
    u.refs()
    if issues:
        return _outcome(issues, j)
    return _outcome([], _save_undone(u.run, j))


def _undo_committed(u: _Undo, j: Journal) -> ApplyOutcome:
    root, commit = u.root, j.commit
    if commit is None or not gitops.is_repo(root):
        return _outcome([Issue(code=EXIT_INTERNAL, key="journal_invalid", detail="commit")], j)
    head = gitops.head_branch(root)
    try:
        merged = head != _branch(u.reorg_id) and gitops.is_ancestor(root, commit, "HEAD")
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))], j)
    if merged:
        return _undo_merged(u, j, commit, head or "HEAD")
    issues = _switch_base(root, j, u.m)
    u.refs()
    if issues:
        return _outcome(issues, j)
    for path in _restore_ignored_prev(root, u.run, j):
        u.prompter.say(_tx(u.lang, "recreated", path=path))
    issues = _sources_issues(root, u.m)
    if issues:
        return _outcome(issues, j)
    return _outcome([], _save_undone(u.run, j))


def _conflicts(root: Path) -> tuple[str, ...]:
    try:
        raw = gitops.run(root, "diff", "--name-only", "-z", "--relative", "--diff-filter=U")
    except gitops.GitError:
        return ()
    return tuple(sorted(f.decode("utf-8", "surrogateescape") for f in raw.split(b"\0") if f))


def _index_dirty(root: Path) -> tuple[str, ...]:
    raw = gitops.run(root, "diff", "--cached", "--name-only", "-z", "--relative")
    return tuple(sorted(f.decode("utf-8", "surrogateescape") for f in raw.split(b"\0") if f))


def _undo_merged(u: _Undo, j: Journal, commit: str, branch: str) -> ApplyOutcome:
    """Already merged into the current branch: confirmed ``git revert --no-commit``, sources
    checked, then a confirmed revert commit. A conflict aborts the revert (exit 1)."""
    root = u.root
    try:
        staged = _index_dirty(root)
        dirty = gitops.dirty(root, sorted({o.path for o in u.m.outputs}))
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))], j)
    blocking = [Issue(code=EXIT_USAGE, key="index_not_clean", dest=p) for p in staged]
    blocking += [Issue(code=EXIT_USAGE, key="dirty", dest=p) for p in dirty]
    if blocking:
        return _outcome(blocking, j)
    if not u.yes:
        if not u.prompter.is_tty():
            return _outcome([Issue(code=EXIT_USAGE, key="needs_yes")], j)
        if not _yes_no(u.prompter, _tx(u.lang, "revert_q", sha=commit[:12], branch=branch)):
            return _outcome([Issue(code=EXIT_USAGE, key="declined")], j)
    try:
        gitops.revert_no_commit(root, commit)
    except gitops.GitError:
        conflicts = _conflicts(root)
        try:
            gitops.revert_abort(root)
        except gitops.GitError as exc:
            conflicts_issues = [
                Issue(code=EXIT_USAGE, key="revert_conflict", dest=p) for p in conflicts
            ]
            return _outcome(
                [*conflicts_issues, Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))],
                j,
            )
        issues = [Issue(code=EXIT_USAGE, key="revert_conflict", dest=p) for p in conflicts]
        return _outcome(issues or [Issue(code=EXIT_USAGE, key="revert_conflict")], j)
    for path in _restore_ignored_prev(root, u.run, j):
        u.prompter.say(_tx(u.lang, "recreated", path=path))
    issues = _sources_issues(root, u.m)
    if issues:
        # Conservative: nothing is forced back; the staged revert is left for the human.
        u.prompter.say(_tx(u.lang, "revert_staged"))
        return _outcome(issues, j)
    if not u.yes and not _yes_no(u.prompter, _tx(u.lang, "revert_commit_q", branch=branch)):
        u.prompter.say(_tx(u.lang, "revert_staged"))
        u.refs()
        return _outcome([], j)
    try:
        gitops.commit(
            root,
            _tx(u.lang, "revert_subject", id=u.reorg_id),
            f"This reverts commit {commit}.",
        )
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_USAGE, key="commit_rejected", detail=str(exc))], j)
    u.refs()
    return _outcome([], _save_undone(u.run, j))


def _active_distills(root: Path, reorg_id: str) -> tuple[str, ...]:
    """Cards of ``reorg_id`` whose distillation is not undone (fase 2b §9)."""
    base = root / ".declutter" / "distill" / reorg_id
    if not base.is_dir() or base.is_symlink():
        return ()
    out: list[str] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            dj = read_journal(child, root)
        except ReorgError:
            out.append(child.name)  # unreadable: fail closed
            continue
        if dj is not None and dj.state != "undone":
            out.append(child.name)
    return tuple(out)


def undo(
    project: Project, reorg_id: str, prompter: Prompter, *, yes: bool, lang: str
) -> ApplyOutcome:
    """``declutter reorg undo`` (spec §11.5). Never deletes the branch or the tag."""
    root = project.root
    try:
        m, run, j = _load(root, reorg_id)
    except ReorgError as exc:
        return _outcome(exc.issues, None)
    if j is None or j.state in ("planned", "undone"):
        return _outcome([], j)
    active = _active_distills(root, reorg_id)
    if active:
        # Fase 2b §9: the distillations sit on top of the run; undo them first (LIFO).
        return _outcome(
            [Issue(code=EXIT_USAGE, key="distill_active", detail=card) for card in active], j
        )
    u = _Undo(project, reorg_id, prompter, yes, lang, m, run)
    try:
        done = reconcile_commit(root, reorg_id, j)
        if done is not j:
            # ``written`` but the commit exists (interrupted between ``git commit`` and the
            # journal): restoring files on the reorg branch would leave it reverted and the
            # switch refused, so it is undone as ``committed`` (§11.5).
            write_journal(run, done)
            j = done
        if j.state == "staged":
            return _undo_staged(u, j)
        if j.state in ("tagged", "backed_up"):
            return _undo_tagged(u, j)
        if j.state in ("writing", "written"):
            return _undo_writing(u, j)
        return _undo_committed(u, j)
    except ReorgError as exc:
        return _outcome(exc.issues, j)
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))], j)


# --------------------------------------------------------------------------- prune


def _has_tag(root: Path, reorg_id: str) -> bool:
    return gitops.is_repo(root) and gitops.ref_exists(root, f"refs/tags/{_tag(reorg_id)}")


def prune(project: Project, reorg_id: str, prompter: Prompter, *, yes: bool) -> ApplyOutcome:
    """``declutter reorg prune`` (spec §6.1): only in ``planned``/``committed``/``undone``; asks
    for confirmation (without a TTY, ``--yes``); deletes ``original/``, ``backup/``,
    ``stage/`` and ``dry-stage/`` and keeps the rest. Without a tag it warns that no copy of
    the original is left."""
    root = project.root
    try:
        m, run, j = _load(root, reorg_id)
    except ReorgError as exc:
        return _outcome(exc.issues, None)
    state = "planned" if j is None else j.state
    if state not in PRUNABLE:
        return _outcome([Issue(code=EXIT_USAGE, key="prune_state", detail=state)], j)
    lang = m.lang
    if not _has_tag(root, reorg_id):
        prompter.say(_tx(lang, "prune_no_git"))
    if not yes:
        if not prompter.is_tty():
            return _outcome([Issue(code=EXIT_USAGE, key="needs_yes")], j)
        if not _yes_no(prompter, _tx(lang, "prune_q", id=reorg_id)):
            return _outcome([Issue(code=EXIT_USAGE, key="declined")], j)
    for name in PRUNED_DIRS:
        _rmtree(run / name)
    return _outcome([], j)


# --------------------------------------------------------------------------- verify


def _fallback(*readers: Callable[[str], bytes | None]) -> Callable[[str], bytes | None]:
    def read(rel: str) -> bytes | None:
        for r in readers:
            data = r(rel)
            if data is not None:
                return data
        return None

    return read


def _dir_reader(base: Path, root: Path) -> Callable[[str], bytes | None]:
    def read(rel: str) -> bytes | None:
        try:
            return fsutil.safe_read_bytes(base / rel, root)
        except fsutil.UnsafeReadError:
            return None

    return read


def _rev_reader(root: Path, rev: str) -> Callable[[str], bytes | None]:
    def read(rel: str) -> bytes | None:
        try:
            return gitops.show_bytes(root, rev, rel)
        except gitops.GitError:
            return None

    return read


def _placements(root: Path, run: Path) -> tuple[Placement, ...]:
    raw = _read(root, (run / PLACEMENT_NAME).relative_to(root).as_posix())
    if raw is None:
        raise ReorgError([Issue(code=EXIT_USAGE, key="run_missing", detail=PLACEMENT_NAME)])
    try:
        return mf.placements_loads(raw)
    except mf.ManifestError as exc:
        raise ReorgError([exc.issue]) from None


def verify_run(
    project: Project,
    cfg: Config,
    reorg_id: str,
    *,
    rev: str | None,
    strict: bool,
    read: Callable[[str], bytes | None] | None = None,
) -> ProofResult:
    """``declutter reorg verify`` (spec §11.4) in posterior mode, over the disk or ``rev``.

    Needs the local run folder with ``manifest.json`` and ``placement.json`` (else
    ``run_missing``, code 1). The original comes from ``original/`` or, after ``prune``,
    from the tag ``declutter/pre-reorg-<id>``; the C5 backups likewise from ``backup/`` or the
    tag. C8a (disk) uses the journal's files; C8b (``rev``) compares the tag with the
    recorded reorg commit and is skipped when there is none. ``read`` (only without
    ``rev``) replaces the disk reader: ``distill-apply`` proves its stage with it.
    """
    del cfg  # the proof needs no configuration; kept for the shared command signature
    root = project.root
    m, run, j = _load(root, reorg_id)
    placements = _placements(root, run)
    tagged = _has_tag(root, reorg_id)
    tag_ref = f"refs/tags/{_tag(reorg_id)}"
    from_tag = _rev_reader(root, tag_ref) if tagged else (lambda _rel: None)
    original_read = _fallback(_dir_reader(run / "original", root), from_tag)
    backup = _fallback(_dir_reader(run / "backup", root), from_tag)

    def original(rel: str) -> bytes:
        return original_read(rel) or b""

    outputs = frozenset(o.path for o in m.outputs)
    if rev is None:
        written = outputs if j is None else frozenset(e.path for e in j.entries)
        return proof.verify(
            m,
            placements,
            _dir_reader(root, root) if read is None else read,
            original,
            checks=proof.CHECKS_DISK,
            posterior=True,
            strict=strict,
            written=written,
            backup=backup,
        )
    checks = proof.CHECKS_REV
    diff: frozenset[str] | None = None
    commit = None if j is None else j.commit
    base_read: Callable[[str], bytes | None] | None = None
    if tagged and commit is not None:
        base_read = from_tag
        try:
            diff = gitops.diff_names(root, tag_ref, commit)
        except gitops.GitError as exc:
            raise ReorgError(
                [Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))]
            ) from None
    else:
        checks = checks - {"C8b"}
    return proof.verify(
        m,
        placements,
        _rev_reader(root, rev),
        original,
        checks=checks,
        posterior=True,
        strict=strict,
        diff_names=diff,
        backup=backup,
        base=base_read,
    )
