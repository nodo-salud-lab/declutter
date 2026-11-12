"""``declutter reorg apply``: the transaction of spec §11.2 with its journal (§11.5).

Steps: check and C0 on disk (1); build every output in ``stage/`` and record each file's
current sha in the journal before writing anything (2); prove the stage (3); tag and
branch (4); back up every existing file that is extended or rewritten (5); write with
compare-and-swap and a write-ahead journal, sources last and ``CLAUDE.md`` very last (6);
prove the disk (7); commit (8); prove the commit (9); record the final state (10).

Any failure or interruption after step 4 restores every file whose sha is the expected
one, leaves foreign edits untouched and switches back to the base branch. Issues carry
metadata only (keys, paths, ids, ranges); never file content (G6).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import fsutil, ratchet
from ..model import Config, Placement, Project
from . import (
    EXIT_INTERNAL,
    EXIT_INTERRUPTED,
    EXIT_PROOF,
    EXIT_SECRET,
    EXIT_THRESHOLD,
    EXIT_USAGE,
    Issue,
    Prompter,
    ReorgError,
    gitops,
    proof,
    resolve_code,
)
from . import manifest as mf
from .lexicon import scan_secrets

__all__ = [
    "STATES",
    "ApplyOptions",
    "ApplyOutcome",
    "Journal",
    "JournalEntry",
    "apply",
    "read_journal",
    "reconcile_commit",
    "restore",
    "sha256_hex",
    "write_journal",
    "write_order",
]

STATES = ("planned", "staged", "tagged", "backed_up", "writing", "written", "committed", "undone")
ENTRY_STATUSES = frozenset({"pending", "writing", "done"})
JOURNAL_NAME = "journal.json"
PLACEMENT_NAME = "placement.json"
AGENTS = "AGENTS.md"
CLAUDE = "CLAUDE.md"
ABSENT = "absent"

_YES = frozenset({"s", "si", "sí", "y", "yes"})
_TEXT: Mapping[str, Mapping[str, str]] = {
    "es": {
        "apply_q": "¿Aplicar? [s/N] ",
        "commit_q": "¿Commit en {branch}? [s/N] ",
        "g9_head": "Excepciones de seguridad (G9), un bloque por línea:",
        "g9_q": "Escribe «{word}» para aplicar sin fijar estos bloques: ",
        "word": "confirmo",
        "subject": "declutter(reorg {id}): {sources} → 5 capas, {n} líneas sin pérdida",
    },
    "en": {
        "apply_q": "Apply? [y/N] ",
        "commit_q": "Commit on {branch}? [y/N] ",
        "g9_head": "Safety exceptions (G9), one block per line:",
        "g9_q": "Type '{word}' to apply without pinning these blocks: ",
        "word": "confirm",
        "subject": "declutter(reorg {id}): {sources} → 5 layers, {n} lines lossless",
    },
}


def _tx(lang: str, key: str, **kw: object) -> str:
    table = _TEXT.get(lang, _TEXT["en"])
    return table[key].format(**kw) if kw else table[key]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- journal


@dataclass(frozen=True)
class JournalEntry:
    path: str
    mode: str
    prev_sha256: str  # hex or "absent"
    expected_sha256: str
    status: str  # "pending" | "writing" | "done"


@dataclass(frozen=True)
class Journal:
    state: str
    base_branch: str | None
    entries: tuple[JournalEntry, ...]
    replaced_symlink: str | None = None  # link target recorded before writing CLAUDE.md
    commit: str | None = None
    # Added in T13 (default keeps the shared contract): folders created for absent
    # outputs, so a restore can remove them again when they are left empty.
    created_dirs: tuple[str, ...] = ()
    # Added in fase 3A (default keeps the shared contract): the distill-auto batch number.
    batch: int | None = None


def _journal_obj(j: Journal) -> dict[str, object]:
    obj: dict[str, object] = {
        "state": j.state,
        "base_branch": j.base_branch,
        "entries": [
            {
                "path": e.path,
                "mode": e.mode,
                "prev_sha256": e.prev_sha256,
                "expected_sha256": e.expected_sha256,
                "status": e.status,
            }
            for e in j.entries
        ],
        "replaced_symlink": j.replaced_symlink,
        "commit": j.commit,
        "created_dirs": list(j.created_dirs),
    }
    if j.batch is not None:
        obj["batch"] = j.batch
    return obj


def _journal_invalid(detail: str) -> ReorgError:
    return ReorgError([Issue(code=EXIT_INTERNAL, key="journal_invalid", detail=detail)])


def _opt_str(v: object, where: str) -> str | None:
    if v is None or isinstance(v, str):
        return v
    raise _journal_invalid(where)


def _req_str(v: object, where: str) -> str:
    if isinstance(v, str):
        return v
    raise _journal_invalid(where)


def _journal_of(v: object) -> Journal:
    if not isinstance(v, dict):
        raise _journal_invalid("$")
    state = _req_str(v.get("state"), "state")
    if state not in STATES:
        raise _journal_invalid("state")
    raw_entries = v.get("entries")
    if not isinstance(raw_entries, list):
        raise _journal_invalid("entries")
    entries: list[JournalEntry] = []
    for k, e in enumerate(raw_entries):
        if not isinstance(e, dict):
            raise _journal_invalid(f"entries[{k}]")
        entry = JournalEntry(
            path=_req_str(e.get("path"), f"entries[{k}].path"),
            mode=_req_str(e.get("mode"), f"entries[{k}].mode"),
            prev_sha256=_req_str(e.get("prev_sha256"), f"entries[{k}].prev_sha256"),
            expected_sha256=_req_str(e.get("expected_sha256"), f"entries[{k}].expected_sha256"),
            status=_req_str(e.get("status"), f"entries[{k}].status"),
        )
        if entry.status not in ENTRY_STATUSES or entry.mode not in mf.OUTPUT_MODES:
            raise _journal_invalid(f"entries[{k}]")
        entries.append(entry)
    dirs = v.get("created_dirs", [])
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise _journal_invalid("created_dirs")
    batch = v.get("batch")
    if batch is not None and (isinstance(batch, bool) or not isinstance(batch, int) or batch < 1):
        raise _journal_invalid("batch")
    return Journal(
        state=state,
        base_branch=_opt_str(v.get("base_branch"), "base_branch"),
        entries=tuple(entries),
        replaced_symlink=_opt_str(v.get("replaced_symlink"), "replaced_symlink"),
        commit=_opt_str(v.get("commit"), "commit"),
        created_dirs=tuple(dirs),
        batch=batch,
    )


def _root_of(run: Path) -> Path:
    """The project root of a run folder ``<root>/.declutter/reorg/<id>``."""
    return run.parent.parent.parent


def read_journal(run: Path, root: Path | None = None) -> Journal | None:
    """The journal of a run; ``None`` means ``planned`` (no journal yet). ``root`` is the
    project root when ``run`` is not ``<root>/.declutter/reorg/<id>`` (a distillation)."""
    root = _root_of(run) if root is None else root
    try:
        data = fsutil.safe_read_bytes(run / JOURNAL_NAME, root)
    except fsutil.UnsafeReadError as exc:
        if exc.reason == "missing":
            return None
        raise ReorgError(
            [Issue(code=EXIT_INTERNAL, key="unsafe_read", dest=JOURNAL_NAME, detail=exc.reason)]
        ) from None
    try:
        obj: object = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _journal_invalid("json") from None
    return _journal_of(obj)


def write_journal(run: Path, j: Journal, root: Path | None = None) -> None:
    """Atomic, fsynced, deterministic JSON (``atomic_write_bytes`` fsyncs file and folder)."""
    data = (
        json.dumps(_journal_obj(j), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    fsutil.atomic_write_bytes(run / JOURNAL_NAME, data, _root_of(run) if root is None else root)


def _set_entry(j: Journal, path: str, status: str) -> Journal:
    return replace(
        j, entries=tuple(replace(e, status=status) if e.path == path else e for e in j.entries)
    )


# --------------------------------------------------------------------------- options


@dataclass(frozen=True)
class ApplyOptions:
    yes: bool = False
    commit: bool = False
    no_git: bool = False
    dry_run: bool = False
    replace_symlink: bool = False


@dataclass(frozen=True)
class ApplyOutcome:
    code: int
    issues: tuple[Issue, ...]
    journal: Journal | None


def _outcome(issues: Iterable[Issue], j: Journal | None) -> ApplyOutcome:
    items = tuple(dict.fromkeys(issues))
    return ApplyOutcome(code=resolve_code(i.code for i in items), issues=items, journal=j)


def _fail(code: int, key: str, j: Journal | None = None, **kw: Any) -> ApplyOutcome:
    return _outcome([Issue(code=code, key=key, **kw)], j)


# --------------------------------------------------------------------------- order


def write_order(entries: Sequence[JournalEntry], sources: Collection[str]) -> list[JournalEntry]:
    """Created, then extended, then control files, then rewritten sources; among the
    sources, ``AGENTS.md`` goes before ``CLAUDE.md`` and ``CLAUDE.md`` is always last."""
    groups = {"created": 0, "extended": 1, "control": 2}

    def key(e: JournalEntry) -> tuple[int, int, str]:
        group = 3 if (e.path in sources or e.mode == "rewritten") else groups.get(e.mode, 3)
        last = {AGENTS: 1, CLAUDE: 2}.get(e.path, 0) if group == 3 else 0
        return (group, last, e.path)

    return sorted(entries, key=key)


# --------------------------------------------------------------------------- disk helpers


def _read(root: Path, rel: str) -> bytes | None:
    """Bytes of ``root/rel`` (confined), ``None`` when missing; other refusals raise."""
    try:
        return fsutil.safe_read_bytes(root / rel, root)
    except fsutil.UnsafeReadError as exc:
        if exc.reason == "missing":
            return None
        raise ReorgError(
            [Issue(code=EXIT_INTERNAL, key="unsafe_read", dest=rel, detail=exc.reason)]
        ) from None


def _reader(base: Path, root: Path) -> proof.Read:
    def read(rel: str) -> bytes | None:
        try:
            return fsutil.safe_read_bytes(base / rel, root)
        except fsutil.UnsafeReadError:
            return None

    return read


def _original(run: Path, root: Path) -> Callable[[str], bytes]:
    read = _reader(run / "original", root)

    def original(rel: str) -> bytes:
        return read(rel) or b""

    return original


def _write(root: Path, path: Path, data: bytes, *, replace_symlink: bool = False) -> None:
    fsutil.atomic_write_bytes(path, data, root, replace_symlink=replace_symlink)


def _rmtree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


def _missing_dirs(root: Path, rel: str) -> list[str]:
    out: list[str] = []
    parent = Path(rel).parent
    while str(parent) not in ("", ".") and not os.path.lexists(root / parent):
        out.append(parent.as_posix())
        parent = parent.parent
    return out


def _sha_now(root: Path, rel: str) -> str:
    data = _read(root, rel)
    return ABSENT if data is None else sha256_hex(data)


# --------------------------------------------------------------------------- restore


def _restore_link(root: Path, e: JournalEntry, target: str) -> bool:
    """Restore a replaced ``CLAUDE.md`` link; False when the file is left untouched."""
    path = root / e.path
    if path.is_symlink():
        return str(path.readlink()) == target
    data = _read(root, e.path)
    if data is None or sha256_hex(data) != e.expected_sha256:
        return False
    path.unlink()
    path.symlink_to(target)
    return True


def restore(root: Path, run: Path, j: Journal) -> tuple[str, ...]:
    """Undo the writes of a journal (spec §11.5); return the paths left untouched.

    For every entry (not only the ``done`` ones): at its expected sha it is restored from
    ``backup/`` (sources from ``original/``) or deleted when it was absent; at its
    previous sha (or absent and ``absent``) nothing is done; anything else is a foreign
    edit and is left as is. A replaced ``CLAUDE.md`` link is recreated.
    """
    untouched: list[str] = []
    backup = _reader(run / "backup", root)
    original = _reader(run / "original", root)
    for e in j.entries:
        try:
            if e.path == CLAUDE and j.replaced_symlink is not None:
                if not _restore_link(root, e, j.replaced_symlink):
                    untouched.append(e.path)
                continue
            cur = _sha_now(root, e.path)
            if cur == e.prev_sha256:
                continue
            if cur != e.expected_sha256:
                untouched.append(e.path)
                continue
            if e.prev_sha256 == ABSENT:
                (root / e.path).unlink()
                continue
            data = None
            for read in (original, backup) if e.mode == "rewritten" else (backup, original):
                cand = read(e.path)
                if cand is not None and sha256_hex(cand) == e.prev_sha256:
                    data = cand
                    break
            if data is None:
                untouched.append(e.path)
                continue
            _write(root, root / e.path, data)
        except (OSError, ReorgError, fsutil.UnsafeWriteError):
            untouched.append(e.path)
    for rel in sorted(j.created_dirs, key=lambda d: (-d.count("/"), d)):
        with contextlib.suppress(OSError):
            (root / rel).rmdir()
    return tuple(untouched)


# --------------------------------------------------------------------------- transaction


class _Abort(Exception):
    def __init__(self, issues: Sequence[Issue]) -> None:
        super().__init__(", ".join(i.key for i in issues))
        self.issues = tuple(issues)


@dataclass
class _Tx:
    project: Project
    cfg: Config
    reorg_id: str
    opts: ApplyOptions
    prompter: Prompter
    now: datetime
    lang: str
    root: Path
    run: Path
    use_git: bool = False
    symlink: bool = False
    status_before: frozenset[str] = frozenset()
    normalizing: tuple[gitops.Finding, ...] = ()
    journal: Journal | None = None

    @property
    def branch(self) -> str:
        return f"declutter/reorg-{self.reorg_id}"

    @property
    def tag(self) -> str:
        return f"declutter/pre-reorg-{self.reorg_id}"

    def save(self, j: Journal) -> None:
        self.journal = j
        if not self.opts.dry_run:
            write_journal(self.run, j)

    def yes_no(self, prompt: str) -> bool:
        return self.prompter.ask(prompt).strip().lower() in _YES


def _preconditions(tx: _Tx, m: mf.Manifest) -> list[Issue]:
    """§11.1 git preconditions (or ``--no-git``) and the replaced-link confirmation."""
    root = tx.root
    issues: list[Issue] = []
    sources = [s.path for s in m.sources]
    outputs = [o.path for o in m.outputs]
    tx.symlink = m.symlink_claude and (root / CLAUDE).is_symlink()
    if tx.symlink and not tx.opts.replace_symlink:
        issues.append(Issue(code=EXIT_USAGE, key="replace_symlink", dest=CLAUDE))
    if not gitops.is_repo(root):
        if not tx.opts.no_git:
            issues.append(Issue(code=EXIT_USAGE, key="not_repo"))
        return issues
    if tx.opts.no_git:
        # Conservative reading: --no-git only acknowledges a missing repository; inside
        # one it would skip the safety tag and branch.
        issues.append(Issue(code=EXIT_USAGE, key="no_git_in_repo"))
        return issues
    tx.use_git = True
    try:
        if gitops.head_branch(root) is None:
            issues.append(Issue(code=EXIT_USAGE, key="detached_head"))
        op = gitops.operation_in_progress(root)
        if op is not None:
            issues.append(Issue(code=EXIT_USAGE, key="operation_in_progress", detail=op))
        existing = [p for p in outputs if os.path.lexists(root / p)]
        for path in gitops.dirty(root, sorted({*sources, *existing})):
            issues.append(Issue(code=EXIT_USAGE, key="dirty", dest=path))
        for ref in (f"refs/heads/{tx.branch}", f"refs/tags/{tx.tag}"):
            if gitops.ref_exists(root, ref):
                issues.append(Issue(code=EXIT_USAGE, key="ref_exists", detail=ref))
        # Decided after step 1, which builds the bytes to write (Enmienda 05-11).
        tx.normalizing = gitops.normalizing(root, sorted({*sources, *outputs}))
        for path in sources:
            disk = _read(root, path)
            if disk is None or not gitops.blob_matches_disk(root, path, disk):
                issues.append(Issue(code=EXIT_USAGE, key="blob_differs", source=path))
        tx.status_before = gitops.status_paths(root)
    except gitops.GitError as exc:
        issues.append(Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc)))
    except ReorgError as exc:
        issues += exc.issues
    return issues


def _normalizing_issues(tx: _Tx, m: mf.Manifest, files: Mapping[str, bytes] | None) -> list[Issue]:
    """§11.1 normalizing attributes (Enmienda 05-11): tolerated only when every relevant
    byte is CR-free — a source's disk bytes; an output's new bytes and, if it exists, its
    current ones. Without the built bytes (``files`` is None) every finding refuses."""
    if not tx.normalizing:
        return []
    root = tx.root
    sources = {s.path for s in m.sources}
    outputs = {o.path for o in m.outputs}

    def cr_free(path: str) -> bool:
        chunks: list[bytes] = []
        if path in outputs:
            if files is None or path not in files:
                return False
            chunks.append(files[path])
        if path in sources or os.path.lexists(root / path):
            try:
                disk = _read(root, path)
            except ReorgError:
                return False  # unreadable here: refuse, the read itself is reported elsewhere
            if disk is None and path in sources:
                return False
            chunks.append(disk or b"")
        return all(b"\r" not in c for c in chunks)

    refused, _ok = gitops.split_lf_safe(tx.normalizing, sorted(sources | outputs), cr_free)
    return [
        Issue(code=EXIT_USAGE, key="git_normalizes", dest=path, detail=f"{attr}={value}")
        for path, attr, value in refused
    ]


def _step1(tx: _Tx, stored: mf.Manifest) -> tuple[mf.PlanOutcome | None, list[Issue]]:
    """``check`` (for apply), C0 on the disk and the ratchet baseline (§12.3)."""
    try:
        out = mf.check_run(tx.project, tx.cfg, tx.reorg_id, now=tx.now)
    except ReorgError as exc:
        return None, list(exc.issues)
    issues = list(out.issues)
    if out.code == 0 and stored.sources != out.manifest.sources:
        issues.append(Issue(code=EXIT_PROOF, key="check.1", detail=f"plan --from {stored.id}"))
    disk = _reader(tx.root, tx.root)
    c0 = proof.verify(out.manifest, (), disk, lambda p: disk(p) or b"", checks=frozenset({"C0"}))
    issues += proof.to_issues(c0)
    if any(w.split(":")[0] == ratchet.WOULD_FAIL for w in out.manifest.warnings):
        issues.append(Issue(code=EXIT_THRESHOLD, key=ratchet.WOULD_FAIL))
    return out, issues


def _confirm(tx: _Tx, m: mf.Manifest) -> list[Issue]:
    """«¿Aplicar?» (answered by ``--yes``) and then G9, which ``--yes`` never skips."""
    if not tx.opts.yes and not tx.yes_no(_tx(tx.lang, "apply_q")):
        return [Issue(code=EXIT_USAGE, key="declined")]
    acked = [(u, b) for u in m.units for b in u.blocks if b.guard_ack]
    if not acked:
        return []
    if not tx.prompter.is_tty():
        return [Issue(code=EXIT_USAGE, key="g9_tty", detail=f"{len(acked)} guard_ack")]
    tx.prompter.say(_tx(tx.lang, "g9_head"))
    for u, b in acked:
        tx.prompter.say(f"  {b.id} {b.key} [{', '.join(b.guard)}] {b.guard_ack} ({u.source})")
    word = _tx(tx.lang, "word")
    if tx.prompter.ask(_tx(tx.lang, "g9_q", word=word)).strip() != word:
        return [Issue(code=EXIT_USAGE, key="g9_not_confirmed")]
    return []


def _stage(tx: _Tx, out: mf.PlanOutcome, stage: Path) -> Journal:
    """Step 2: every output in ``stage/``; current shas recorded before writing anything."""
    root, m, res = tx.root, out.manifest, out.build
    _rmtree(stage)
    for path in sorted(res.files):
        _write(root, stage / path, res.files[path])
    sources = {s.path: s.sha256 for s in m.sources}
    entries: list[JournalEntry] = []
    created: set[str] = set()
    fatal: list[Issue] = []
    for o in m.outputs:
        data = _read(root, o.path)
        prev = ABSENT if data is None else sha256_hex(data)
        planned = sources.get(o.path, res.prev_sha.get(o.path, ABSENT))
        if prev != planned:
            fatal.append(Issue(code=EXIT_PROOF, key="concurrent_edit", dest=o.path))
        if data is not None and o.path not in sources:
            for hit in scan_secrets(o.path, data):
                fatal.append(
                    Issue(
                        code=EXIT_SECRET,
                        key="secret",
                        dest=o.path,
                        at=(hit.line, hit.line),
                        detail=hit.category,
                    )
                )
        if data is None:
            created.update(_missing_dirs(root, o.path))
        entries.append(JournalEntry(o.path, o.mode, prev, sha256_hex(res.files[o.path]), "pending"))
    if fatal:
        raise _Abort(fatal)
    return Journal(
        state="staged",
        base_branch=None,
        entries=tuple(entries),
        created_dirs=tuple(sorted(created)),
    )


def _backup(tx: _Tx, j: Journal) -> None:
    """Step 5: copy each existing extended or rewritten file; its sha must be the one of
    step 2, otherwise someone edited it in between (``concurrent_edit``)."""
    root = tx.root
    for e in j.entries:
        if e.prev_sha256 == ABSENT or (e.path == CLAUDE and tx.symlink):
            continue
        data = _read(root, e.path)
        if data is None or sha256_hex(data) != e.prev_sha256:
            raise _Abort([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)])
        dest = tx.run / "backup" / e.path
        _write(root, dest, data)
        copy = _read(root, dest.relative_to(root).as_posix())
        if copy is None or sha256_hex(copy) != e.prev_sha256:
            raise _Abort([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)])


def _cas_ok(tx: _Tx, e: JournalEntry, link: str | None) -> bool:
    path = tx.root / e.path
    if e.path == CLAUDE and link is not None:
        return path.is_symlink() and str(path.readlink()) == link
    return _sha_now(tx.root, e.path) == e.prev_sha256


def _write_all(tx: _Tx, out: mf.PlanOutcome, j: Journal) -> Journal:
    """Step 6: compare-and-swap, write-ahead journal, atomic write, ``done``."""
    files = out.build.files
    sources = {s.path for s in out.manifest.sources}
    j = replace(j, state="writing")
    tx.save(j)
    for e in write_order(j.entries, sources):
        if not _cas_ok(tx, e, j.replaced_symlink):
            raise _Abort([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)])
        j = _set_entry(j, e.path, "writing")
        tx.save(j)
        _write(
            tx.root,
            tx.root / e.path,
            files[e.path],
            replace_symlink=(e.path == CLAUDE and tx.symlink),
        )
        j = _set_entry(j, e.path, "done")
        tx.save(j)
    return j


def _verify_disk(
    tx: _Tx, m: mf.Manifest, placements: Sequence[Placement], j: Journal, *, with_status: bool
) -> list[Issue]:
    new_status: frozenset[str] | None = None
    if tx.use_git and with_status:
        new_status = gitops.status_paths(tx.root) - tx.status_before
    res = proof.verify(
        m,
        placements,
        _reader(tx.root, tx.root),
        _original(tx.run, tx.root),
        checks=proof.CHECKS_DISK,
        written=frozenset(e.path for e in j.entries),
        new_status=new_status,
        backup=_reader(tx.run / "backup", tx.root),
    )
    return list(proof.to_issues(res))


def _rollback(tx: _Tx, issues: list[Issue]) -> list[Issue]:
    """Restore every file, switch back to the base branch; ``undone`` when clean."""
    j = tx.journal
    if j is None:
        return issues
    untouched = restore(tx.root, tx.run, j)
    issues += [Issue(code=EXIT_PROOF, key="restore_untouched", dest=p) for p in untouched]
    switched = True
    if tx.use_git and j.base_branch is not None:
        try:
            if gitops.head_branch(tx.root) != j.base_branch:
                gitops.switch(tx.root, j.base_branch)
        except gitops.GitError as exc:
            switched = False
            issues.append(Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc)))
    _rmtree(tx.run / "stage")
    if not untouched and switched:
        with contextlib.suppress(Exception):
            tx.save(replace(j, state="undone"))
    return issues


def _commit(tx: _Tx, m: mf.Manifest, placements: Sequence[Placement], j: Journal) -> ApplyOutcome:
    """Steps 8-10: commit only the outputs, then prove the commit (``verify --rev HEAD``)."""
    root = tx.root
    outputs = [o.path for o in m.outputs]
    subject = _tx(
        tx.lang,
        "subject",
        id=m.id,
        sources=" + ".join(s.path for s in m.sources),
        n=sum(s.lines for s in m.sources),
    )
    manifest_raw = _read(root, (tx.run / mf.MANIFEST_NAME).relative_to(root).as_posix()) or b""
    body = "\n".join(
        [
            f"Declutter-Manifest: {m.id} sha256={sha256_hex(manifest_raw)}",
            *[f"Declutter-Source: {s.path} sha256={s.sha256}" for s in m.sources],
        ]
    )
    new: list[str] = []
    try:
        new = [p for p in outputs if gitops.show_bytes(root, "HEAD", p) is None]
        gitops.add(root, new)
    except gitops.GitError as exc:
        # No hook runs on ``git add``: an ignore rule or a git failure, not a hook.
        _unstage_new(tx, new)
        return _fail(EXIT_USAGE, "git_add_failed", j, detail=f"{exc}: declutter reorg undo {m.id}")
    try:
        sha = gitops.commit_only(root, subject, body, outputs)
    except gitops.GitError as exc:
        _unstage_new(tx, new)
        return _fail(
            EXIT_USAGE,
            "commit_rejected",
            j,
            detail=f"{exc}; hook may have changed files: declutter reorg verify {m.id}",
        )
    j = replace(j, state="committed", commit=sha)
    tx.save(j)
    return _prove_commit(tx, m, placements, j)


def _unstage_new(tx: _Tx, new: Sequence[str]) -> None:
    """After a failed ``git add`` or commit: take the new outputs out of the index again,
    so an ``undo`` (or the human's next commit on the base branch) does not carry them.
    Only while HEAD is still the tagged commit (no commit was made)."""
    with contextlib.suppress(gitops.GitError, ValueError):
        if new and gitops.rev_commit(tx.root, "HEAD") == gitops.rev_commit(tx.root, tx.tag):
            gitops.unstage(tx.root, new)


def _prove_commit(
    tx: _Tx, m: mf.Manifest, placements: Sequence[Placement], j: Journal
) -> ApplyOutcome:
    """Step 9: ``verify --rev HEAD --strict`` over the reorg commit."""
    root = tx.root
    try:
        res = proof.verify(
            m,
            placements,
            lambda p: gitops.show_bytes(root, "HEAD", p),
            _original(tx.run, root),
            checks=proof.CHECKS_REV,
            strict=True,
            diff_names=gitops.diff_names(root, tx.tag, "HEAD"),
            backup=_reader(tx.run / "backup", root),
            base=lambda p: gitops.show_bytes(root, tx.tag, p),
        )
    except gitops.GitError as exc:
        return _fail(EXIT_INTERNAL, "git_error", j, detail=str(exc))
    if not res.ok:
        hint = Issue(
            code=EXIT_PROOF, key="verify_rev_failed", detail=f"declutter reorg undo {m.id}"
        )
        return _outcome([*proof.to_issues(res), hint], j)
    return _outcome([], j)


def reconcile_commit(root: Path, reorg_id: str, j: Journal) -> Journal:
    """A ``written`` journal whose commit already exists (a crash or Ctrl-C between
    ``git commit`` and the journal write): HEAD is on ``declutter/reorg-<id>`` and has moved
    past the tag. The journal then becomes ``committed`` with that commit; otherwise it
    is returned unchanged. Nothing is written here."""
    if j.state != "written" or j.commit is not None:
        return j
    try:
        if not gitops.is_repo(root) or gitops.head_branch(root) != f"declutter/reorg-{reorg_id}":
            return j
        tagged = gitops.rev_commit(root, f"refs/tags/declutter/pre-reorg-{reorg_id}")
        head = gitops.rev_commit(root, "HEAD")
        if tagged is None or head is None or head == tagged:
            return j
        if not gitops.is_ancestor(root, tagged, head):
            return j
    except (gitops.GitError, ValueError):
        return j
    return replace(j, state="committed", commit=head)


def _wants_commit(tx: _Tx) -> bool:
    if not tx.use_git:
        return False
    if tx.opts.commit:
        return True
    return tx.prompter.is_tty() and tx.yes_no(_tx(tx.lang, "commit_q", branch=tx.branch))


def _resume(tx: _Tx, m: mf.Manifest, j: Journal) -> ApplyOutcome:
    """``apply <id> --commit`` over a ``written`` journal: exact disk proof, then 8-10."""
    tty = tx.prompter.is_tty()
    if not tty and not tx.opts.yes:
        return _fail(EXIT_USAGE, "needs_yes", j)
    if not gitops.is_repo(tx.root) or j.base_branch is None:
        return _fail(EXIT_USAGE, "not_repo", j)
    tx.use_git = True
    if gitops.head_branch(tx.root) != tx.branch:
        return _fail(EXIT_USAGE, "wrong_branch", j, detail=tx.branch)
    raw = _read(tx.root, (tx.run / PLACEMENT_NAME).relative_to(tx.root).as_posix())
    if raw is None:
        return _fail(EXIT_USAGE, "run_missing", j, detail=PLACEMENT_NAME)
    try:
        placements = mf.placements_loads(raw)
    except mf.ManifestError as exc:
        return _outcome([exc.issue], j)
    issues = _verify_disk(tx, m, placements, j, with_status=False)
    if issues:
        return _outcome(issues, j)
    done = reconcile_commit(tx.root, tx.reorg_id, j)
    if done.state == "committed":
        # The commit was made before an interruption: record it and prove it (step 9).
        tx.save(done)
        return _prove_commit(tx, m, placements, done)
    if not tx.opts.yes and not tx.yes_no(_tx(tx.lang, "commit_q", branch=tx.branch)):
        return _outcome([], j)
    return _commit(tx, m, placements, j)


def apply(
    project: Project,
    cfg: Config,
    reorg_id: str,
    opts: ApplyOptions,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
) -> ApplyOutcome:
    """``declutter reorg apply`` (spec §11.2). Never pushes or merges."""
    root = project.root
    try:
        stored, run = mf.load_run(root, reorg_id)
        j0 = read_journal(run)
    except ReorgError as exc:
        return _outcome(exc.issues, None)
    tx = _Tx(project, cfg, reorg_id, opts, prompter, now, lang, root, run, journal=j0)
    try:
        if j0 is not None and j0.state == "written" and opts.commit and not opts.dry_run:
            return _resume(tx, stored, j0)
        if j0 is not None and j0.state != "planned":
            return _fail(EXIT_USAGE, "already_applied", j0, detail=j0.state)
        return _transaction(tx, stored)
    except KeyboardInterrupt:
        interrupted = Issue(code=EXIT_INTERRUPTED, key="interrupted")
        j = tx.journal
        if j is not None and j.state in ("written", "committed"):
            # Writing had finished (§10.4: restore only a write in progress). A commit
            # made just before the interruption is recorded; the remedy is undo (§11.5).
            done = reconcile_commit(tx.root, tx.reorg_id, j)
            if done is not j:
                with contextlib.suppress(Exception):
                    tx.save(done)
            hint = Issue(
                code=EXIT_INTERRUPTED, key="interrupted", detail=f"declutter reorg undo {reorg_id}"
            )
            return _outcome([hint], tx.journal)
        issues = _rollback(tx, [interrupted])
        return _outcome(issues, tx.journal)


def _transaction(tx: _Tx, stored: mf.Manifest) -> ApplyOutcome:
    opts = tx.opts
    issues: list[Issue] = []
    if not opts.dry_run and not tx.prompter.is_tty() and not opts.yes:
        issues.append(Issue(code=EXIT_USAGE, key="needs_yes"))
    issues += _preconditions(tx, stored)
    out, found = _step1(tx, stored)
    issues += _normalizing_issues(tx, stored, None if out is None else out.build.files)
    issues += found
    if issues or out is None:
        return _outcome(issues or [Issue(code=EXIT_INTERNAL, key="check_failed")], None)
    m, placements = out.manifest, out.build.placements
    if not opts.dry_run:
        found = _confirm(tx, m)
        if found:
            return _outcome(found, None)

    # 2-3: stage and prove it; nothing outside the run folder is touched.
    stage = tx.run / ("dry-stage" if opts.dry_run else "stage")
    try:
        j = _stage(tx, out, stage)
        if not opts.dry_run:
            # The applied plan: ``set`` only records overrides, so the units and outputs
            # of manifest.json are refreshed with the rebuilt ones that match placement.json.
            _write(tx.root, tx.run / mf.MANIFEST_NAME, mf.dumps(m))
            _write(tx.root, tx.run / PLACEMENT_NAME, mf.placements_dumps(placements))
            tx.save(j)
        res = proof.verify(
            m,
            placements,
            _reader(stage, tx.root),
            _original(tx.run, tx.root),
            checks=proof.CHECKS_STAGE,
            written=frozenset(out.build.files),
            backup=_reader(tx.root, tx.root),
        )
    except _Abort as exc:
        _rmtree(stage)
        return _outcome(exc.issues, tx.journal)
    except (Exception, KeyboardInterrupt) as exc:
        _rmtree(stage)
        if isinstance(exc, KeyboardInterrupt):
            return _fail(EXIT_INTERRUPTED, "interrupted", tx.journal)
        return _fail(EXIT_INTERNAL, "apply_aborted", tx.journal, detail=type(exc).__name__)
    _rmtree(stage)
    if not res.ok or opts.dry_run:
        return _outcome(proof.to_issues(res), tx.journal)
    return _write_phase(tx, out, j)


def _write_phase(tx: _Tx, out: mf.PlanOutcome, j: Journal) -> ApplyOutcome:
    """Steps 4-7 under the abort rule, then the commit decision (8-10)."""
    m, placements = out.manifest, out.build.placements
    try:
        if tx.use_git:
            # The base branch is recorded before git is touched, so that a crash between
            # the tag or the switch and the journal still lets undo go back (§11.2 step 4).
            j = replace(j, base_branch=gitops.head_branch(tx.root))
            tx.save(j)
            gitops.tag(tx.root, tx.tag)
            gitops.switch_create(tx.root, tx.branch)
            j = replace(j, state="tagged")
        else:
            j = replace(j, state="tagged")
        if tx.symlink:
            j = replace(j, replaced_symlink=str((tx.root / CLAUDE).readlink()))
        tx.save(j)
        _backup(tx, j)
        j = replace(j, state="backed_up")
        tx.save(j)
        j = _write_all(tx, out, j)
        found = _verify_disk(tx, m, placements, j, with_status=True)
        if found:
            raise _Abort(found)
        j = replace(j, state="written")
        tx.save(j)
    except KeyboardInterrupt:
        raise
    except _Abort as exc:
        return _outcome(_rollback(tx, list(exc.issues)), tx.journal)
    except Exception as exc:
        issue = Issue(code=EXIT_PROOF, key="apply_aborted", detail=type(exc).__name__)
        if isinstance(exc, gitops.GitError):
            issue = replace(issue, detail=str(exc))
        return _outcome(_rollback(tx, [issue]), tx.journal)
    if _wants_commit(tx):
        return _commit(tx, m, placements, j)
    return _outcome([], j)
