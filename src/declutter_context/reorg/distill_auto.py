"""``declutter reorg distill-auto`` and ``distill-undo --auto`` (fase 3A §5-§8).

One batch: every ``ready`` card, in ``D-NNN`` order, applied by the 2b transaction on one
new branch ``declutter/distill-auto-<id>-<n>`` after the tag
``declutter/pre-distill-auto-<id>-<n>``,
one commit per card, no confirmation, a one-line notice per card, a brake (SIGINT and
SIGTERM) and a report built from the journals. Never merges, pushes or deletes refs; never
relaxes the ratchet; no AI and no network.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import FrameType

from .. import fsutil, ratchet
from ..i18n import t
from ..model import Config, Project
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
    distill,
    distill_apply,
    gitops,
    mdblocks,
    resolve_code,
)
from . import distill_batch as db
from .apply import ApplyOutcome, Journal, read_journal, restore, write_journal
from .distill_auto_report import Report, report_md, report_obj
from .lexicon import build_lexicon

__all__ = ["AutoOptions", "AutoOutcome", "Brake", "run", "undo_batch"]

# A ratchet failure (3) halts too: the ratchet is never relaxed by itself (A10).
_HALT_CODES = frozenset({EXIT_SECRET, EXIT_USAGE, EXIT_INTERNAL, EXIT_THRESHOLD})
_HALT_KEYS = frozenset({"concurrent_edit", "restore_untouched"})


class Brake:
    """The stop flag set by SIGINT/SIGTERM (§6.1). Injectable for tests."""

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


@dataclass(frozen=True)
class AutoOptions:
    skip: tuple[str, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class AutoOutcome:
    code: int
    issues: tuple[Issue, ...]
    batch: db.Batch | None = None
    report: Report | None = None


def _m(key: str, lang: str, **kw: object) -> str:
    return t(f"distill.auto.{key}", lang, **kw)


def _fail(issues: Sequence[Issue]) -> AutoOutcome:
    items = tuple(dict.fromkeys(issues))
    return AutoOutcome(code=resolve_code(i.code for i in items), issues=items)


def _iso(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _measure(root: Path) -> tuple[int, int]:
    """Bytes and lines of AGENTS.md now (0, 0 when absent)."""
    try:
        data = fsutil.safe_read_bytes(root / "AGENTS.md", root)
    except fsutil.UnsafeReadError:
        return (0, 0)
    return (len(data), len(mdblocks.split_lines(data).lines))


def _tighten_events(root: Path) -> list[Mapping[str, object]] | None:
    """The ``tighten`` events of ratchet.log; ``None`` when the log cannot be read."""
    try:
        raw = fsutil.safe_read_bytes(root / ratchet.LOG_REL, root)
    except fsutil.UnsafeReadError as exc:
        return [] if exc.reason == "missing" else None
    try:
        events = ratchet.log_events(raw)
    except ratchet.RatchetSchemaError:
        return None
    return [e for e in events if e.get("event") == "tighten"]


def _tighten_count(root: Path) -> int | None:
    found = _tighten_events(root)
    return None if found is None else len(found)


def _tightened_since(root: Path, before: int | None) -> tuple[str, ...]:
    """New ``tighten`` events; ``()`` when either read failed (never the old ones)."""
    events = _tighten_events(root)
    if before is None or events is None:
        return ()
    found = events[before:]
    # Events are metadata only (keys and numbers); never rule text. Only the maxima that
    # went down, as ``S2_max 2621→2411`` (first old value to last new value).
    first: dict[str, object] = {}
    last: dict[str, object] = {}
    for e in found:
        old, new = e.get("old"), e.get("new")
        if not isinstance(old, Mapping) or not isinstance(new, Mapping):
            continue
        for k, nv in new.items():
            ov = old.get(k)
            if isinstance(ov, int) and isinstance(nv, int) and nv < ov:
                first.setdefault(str(k), ov)
                last[str(k)] = nv
    return tuple(f"{k} {first[k]}→{last[k]}" for k in sorted(first))


def _card_no(card: str) -> int:
    return int(card.split("-", 1)[1])


def _classify(res: ApplyOutcome, n: int) -> str:
    """``applied`` | ``skipped`` | ``stopped`` | ``halt`` (A9, P2).

    Only a journal of THIS batch counts for P2: a failure before writing hands back the
    card's previous journal, which may be ``committed`` from an older application.
    An incomplete rollback is never «skipped».
    """
    keys = {i.key for i in res.issues}
    j = res.journal
    ours = j is not None and j.batch == n
    state = j.state if j is not None and ours else None
    if res.code == 0 and state == "committed":
        return "applied"
    if res.code == EXIT_INTERRUPTED:
        return "stopped"
    if (
        res.code in _HALT_CODES
        or keys & _HALT_KEYS
        or state == "committed"
        or (state is not None and state in distill_apply.INTERRUPTED)
    ):
        return "halt"
    return "skipped"


def _halt_issue(res: ApplyOutcome) -> Issue:
    """The issue that names the halt: P2's ``verify_rev_failed`` (it carries the undo
    command), then a halting key, then one with the outcome's code. The issue keeps the
    outcome's code, so the precedence of §5.4 holds (a secret beside it still exits 5)."""
    for key in ("distill.verify_rev_failed", *sorted(_HALT_KEYS)):
        found = next((x for x in res.issues if x.key == key), None)
        if found is not None:
            return replace(found, code=res.code or found.code)
    return next(
        (x for x in res.issues if x.code == res.code),
        Issue(code=res.code or EXIT_PROOF, key="distill.auto.halted"),
    )


def _short(key: str) -> str:
    return key.removeprefix("distill.")


def _stale_issues(root: Path, reorg_id: str) -> list[Issue]:
    """§5.2.3 and §6.3: a live lock, or a dead / interrupted batch, refuse a new one."""
    issues: list[Issue] = []
    lock = db.read_lock(root, reorg_id)
    if lock is not None and db.pid_alive(lock.pid):
        return [Issue(code=EXIT_USAGE, key="distill.auto.locked", detail=f"pid {lock.pid}")]
    if lock is not None:
        owner = db.read_batch(root, reorg_id, lock.batch) if lock.batch > 0 else None
        if owner is None or owner.state != "running":
            # An orphan lock (P5): reported, never deleted here.
            rel = db.lock_path(root, reorg_id).relative_to(root).as_posix()
            issues.append(
                Issue(
                    code=EXIT_USAGE,
                    key="distill.auto.locked",
                    detail=f"pid {lock.pid} dead; rm {rel}",
                )
            )
    for n in db.numbers(root, reorg_id):
        b = db.read_batch(root, reorg_id, n)
        if b is None:
            continue
        if b.state == "running":
            b = replace(b, state="interrupted")
            db.write_batch(root, b)
        if b.state == "interrupted":
            half = [
                c
                for c in b.planned
                if (j := read_journal(distill_apply.distill_dir(root, reorg_id, c), root))
                is not None
                and j.state not in ("committed", "undone")
            ]
            issues.append(
                Issue(
                    code=EXIT_USAGE,
                    key="distill.auto.interrupted",
                    detail=f"batch {n} {' '.join(half) or '-'}: "
                    f"declutter reorg distill-undo {reorg_id} --auto {n}",
                )
            )
    return issues


def run(
    project: Project,
    cfg: Config,
    reorg_id: str,
    opts: AutoOptions,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
    brake: Brake | None = None,
    signals: bool = True,
) -> AutoOutcome:
    """``declutter reorg distill-auto`` (fase 3A §5). Never reads stdin (§11.14)."""
    root = project.root
    brake = brake or Brake()
    try:
        stale = _stale_issues(root, reorg_id)
        if stale:
            return _fail(stale)
        rep = distill.check(
            root, reorg_id, card=None, lexicon=build_lexicon(cfg.reorg.extra_lexicon)
        )
    except ReorgError as exc:
        return _fail(exc.issues)
    # §5.2.6: a secret in any card stops everything before anything is created.
    secrets = [
        Issue(
            code=EXIT_SECRET,
            key="secret",
            dest=r.path,
            at=(r.line, r.line) if r.line else None,
            detail=r.detail,
        )
        for c in rep.cards
        for r in c.reasons
        if r.code == EXIT_SECRET
    ]
    if secrets:
        return _fail(secrets)
    known = {c.card_id for c in rep.cards}
    unknown = [c for c in opts.skip if c not in known]
    if unknown:
        return _fail(
            [Issue(code=EXIT_USAGE, key="distill.auto.skip_unknown", detail=" ".join(unknown))]
        )
    ready = sorted((c for c in rep.cards if c.state == "ready"), key=lambda c: _card_no(c.card_id))
    planned = tuple(c.card_id for c in ready if c.card_id not in opts.skip)
    if not planned:
        prompter.say(_m("nothing", lang))
        return AutoOutcome(code=0, issues=())
    issues = distill_apply.batch_preflight(
        project, cfg, reorg_id, planned, prompter, now=now, lang=lang
    )
    if any(i.code == EXIT_THRESHOLD for i in issues):
        issues.append(Issue(code=EXIT_THRESHOLD, key="distill.auto.ratchet_human"))
    if issues:
        return _fail(issues)
    if opts.dry_run:
        return _dry_run(
            project,
            cfg,
            reorg_id,
            opts,
            rep,
            planned,
            prompter,
            now=now,
            lang=lang,
            brake=brake,
            signals=signals,
        )
    return _run_batch(
        project,
        cfg,
        reorg_id,
        opts,
        rep,
        planned,
        prompter,
        now=now,
        lang=lang,
        brake=brake,
        signals=signals,
    )


_DRY_MAX_BYTES = 2 * 1024**3


class _DryRefused(Exception):
    """The dry-run cannot be isolated safely; ``issue`` says why."""

    def __init__(self, key: str, detail: str) -> None:
        super().__init__(key)
        self.issue = Issue(code=EXIT_USAGE, key=f"distill.auto.{key}", detail=detail)


def _git_path(root: Path, *args: str) -> Path:
    """An absolute, resolved path printed by ``git rev-parse`` (relative to ``root``)."""
    out = gitops.run(root, "rev-parse", *args).decode("utf-8", "replace").strip()
    p = Path(out)
    return (p if p.is_absolute() else root / p).resolve()


def _tree_bytes(top: Path, *, skip_git: bool) -> int:
    """Bytes below ``top`` by ``lstat`` (links never followed)."""
    total = 0
    stack = [top]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if skip_git and e.name == ".git" and Path(e.path).parent == top:
                    continue
                st = e.stat(follow_symlinks=False)
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                else:
                    total += st.st_size
    return total


def _inside(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def _build_dry_copy(project: Project, tmp: Path) -> Project:
    """An independent repository in ``tmp`` holding the project (§5.5, P3).

    The working tree of the toplevel goes to ``tmp/copy`` without ``.git``; the common git
    dir becomes ``tmp/copy/.git``. In a linked worktree the copy takes that worktree's HEAD
    and index. Hooks are neutralised and ``core.worktree`` dropped; ``core.fsmonitor`` and
    ``commit.gpgSign`` are turned off. The copied local config may still carry
    ``filter.*``, ``gpg.program``, ``credential.helper`` or ``include.path``; the batch
    runs no filters on the copy beyond git's own and never reaches a remote. Every
    symlink of the copy, ``.git`` included, must resolve inside ``tmp``. Raises
    ``_DryRefused``.
    """
    root = project.root
    tmp = tmp.resolve()
    top = _git_path(root, "--show-toplevel")
    prefix = gitops.run(root, "rev-parse", "--show-prefix").decode().strip()
    common = _git_path(root, "--git-common-dir")
    gitdir = _git_path(root, "--git-dir")
    if (top / ".gitmodules").exists():
        raise _DryRefused("dry_run_unsafe", "submodules")
    size = _tree_bytes(top, skip_git=True) + _tree_bytes(common, skip_git=False)
    if size > _DRY_MAX_BYTES:
        mib = size // 1024**2
        raise _DryRefused(
            "dry_run_too_big",
            f"{mib} MiB > {_DRY_MAX_BYTES // 1024**2} MiB: use the real run "
            "and distill-undo --auto",
        )
    copy = tmp / "copy"
    shutil.copytree(
        top,
        copy,
        symlinks=True,
        ignore=lambda d, names: [".git"] if Path(d) == top else [],
    )
    shutil.copytree(common, copy / ".git", symlinks=True)
    if gitdir != common:
        for name in ("HEAD", "index"):
            src = gitdir / name
            if src.is_file():
                shutil.copy2(src, copy / ".git" / name)
            else:
                (copy / ".git" / name).unlink(missing_ok=True)
    shutil.rmtree(copy / ".git" / "worktrees", ignore_errors=True)
    hooks = tmp / "no-hooks"
    hooks.mkdir()
    gitops.run(copy, "config", "--unset", "core.worktree", check=False)
    gitops.run(copy, "config", "core.hooksPath", str(hooks))
    gitops.run(copy, "config", "core.fsmonitor", "false")
    gitops.run(copy, "config", "commit.gpgSign", "false")
    # Every link of the copy, .git included: a link in .git/objects or .git/refs that
    # points out would make the copy's commit or tag write into the real repository.
    for dirpath, dirnames, filenames in os.walk(copy):
        for name in (*dirnames, *filenames):
            link = Path(dirpath) / name
            if link.is_symlink() and not _inside(link.resolve(), tmp):
                raise _DryRefused("dry_run_unsafe", f"symlink {link.relative_to(copy).as_posix()}")
    root_copy = copy / prefix
    if not _inside(_git_path(root_copy, "--git-common-dir"), tmp) or (
        _git_path(root_copy, "--show-toplevel") != copy.resolve()
    ):
        raise _DryRefused("dry_run_unsafe", "git dir outside the temporary copy")
    return replace(project, root=root_copy, cwd=root_copy)


def _dry_run(
    project: Project,
    cfg: Config,
    reorg_id: str,
    opts: AutoOptions,
    rep: distill.CheckReport,
    planned: tuple[str, ...],
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
    brake: Brake,
    signals: bool,
) -> AutoOutcome:
    """§5.5 (P3): the real batch on an independent temporary copy of the repository, then
    discarded. The project gets no branch, tag, batch.json or report. The only write
    possible on the real project is that ``_stale_issues`` (run before this) may turn a
    ``running`` batch.json into ``interrupted`` (§6.3). The §5.2 conditions were already
    checked on the project; the copy is byte-identical, so ``rep`` and ``planned`` are
    reused. ``dry=True`` keeps the merge/undo commands out of the report. Worktrees,
    subfolders, submodules, outward symlinks and huge trees are handled or refused in
    ``_build_dry_copy`` so a git command in the copy can never touch the real repo."""
    with tempfile.TemporaryDirectory(prefix="declutter-dry-", ignore_cleanup_errors=True) as tmp:
        try:
            sub = _build_dry_copy(project, Path(tmp))
        except _DryRefused as exc:
            return _fail([exc.issue])
        except (gitops.GitError, OSError) as exc:
            return _fail(
                [
                    Issue(
                        code=EXIT_USAGE,
                        key="distill.auto.dry_run_unsafe",
                        detail=type(exc).__name__,
                    )
                ]
            )
        return _run_batch(
            sub,
            cfg,
            reorg_id,
            opts,
            rep,
            planned,
            prompter,
            now=now,
            lang=lang,
            brake=brake,
            signals=signals,
            dry=True,
        )


@contextlib.contextmanager
def _signals(brake: Brake, enabled: bool) -> Iterator[None]:
    """§6.1: SIGINT and SIGTERM only set the flag; a second one is ignored; restored after."""
    if not enabled:
        yield
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        brake.set()

    old = {s: signal.signal(s, handler) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def _run_batch(
    project: Project,
    cfg: Config,
    reorg_id: str,
    opts: AutoOptions,
    rep: distill.CheckReport,
    planned: tuple[str, ...],
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
    brake: Brake,
    signals: bool,
    dry: bool = False,
) -> AutoOutcome:
    root = project.root
    n = db.next_number(root, reorg_id)
    branch, tag = db.batch_branch(reorg_id, n), db.batch_tag(reorg_id, n)
    try:
        refs = [
            (f"refs/heads/{branch}", f"git branch -D {branch}"),
            (f"refs/tags/{tag}", f"git tag -d {tag}"),
        ]
        exists = [
            Issue(code=EXIT_USAGE, key="ref_exists", detail=f"{ref}: {cmd}")
            for ref, cmd in refs
            if gitops.ref_exists(root, ref)
        ]
        if exists:
            return _fail(exists)
        base_branch = gitops.head_branch(root) or ""
        base_commit = gitops.head_commit(root) or ""
    except gitops.GitError as exc:
        return _fail([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))])
    by_id = {c.card_id: c for c in rep.cards}
    b = db.Batch(
        reorg=reorg_id,
        batch=n,
        state="running",
        base_branch=base_branch,
        base_commit=base_commit,
        branch=branch,
        tag=tag,
        planned=planned,
        skipped_by_user=tuple(opts.skip),
        cards=(),
        started=_iso(now),
        ended=None,
    )
    # The handlers go in before the first write, so a signal never lands mid-write (§6.1).
    with _signals(brake, signals):
        try:
            db.acquire(root, reorg_id, n, _iso(now))
        except FileExistsError:
            return _fail([Issue(code=EXIT_USAGE, key="distill.auto.locked")])
        released = False
        try:
            db.write_batch(root, b)
            try:
                gitops.tag(root, tag)
                gitops.switch_create(root, branch)
            except gitops.GitError as exc:
                b = replace(b, state="stopped", stop_key="git_error", ended=_iso(now))
                db.release(root, reorg_id)
                released = True
                db.write_batch(root, b)
                return _fail([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))])
            agents_before = _measure(root)
            tighten_before = _tighten_count(root)
            k = len(planned)
            m = sum(1 for c in rep.cards if c.state == "not_ready")
            prompter.say(_m("start", lang, n=n, k=k, m=m, branch=branch))
            if dry:
                prompter.say(_m("dry_run_notice", lang))
            results: list[db.CardResult] = []
            halt: Issue | None = None
            for i, card in enumerate(planned, start=1):
                if brake.is_set():
                    break
                spec = distill_apply.BatchSpec(
                    batch=n,
                    branch=branch,
                    tag=tag,
                    base_branch=base_branch,
                    reason=f"auto {reorg_id}/{n}",
                    stop=brake.is_set,
                )
                res = distill_apply.apply_batch_card(
                    project, cfg, reorg_id, card, spec, prompter, now=now, lang=lang
                )
                kind = _classify(res, n)
                cc = by_id[card]
                keys = tuple(dict.fromkeys(_short(x.key) for x in res.issues))
                if kind == "applied":
                    assert res.journal is not None
                    results.append(
                        db.CardResult(
                            card,
                            "applied",
                            commit=res.journal.commit,
                            bytes_removed=cc.bytes_removed,
                            bytes_added=cc.bytes_added,
                            copies=len(cc.copies),
                        )
                    )
                    prompter.say(
                        _m(
                            "applied",
                            lang,
                            i=i,
                            k=k,
                            card=card,
                            copies=len(cc.copies),
                            bytes=cc.bytes_removed or 0,
                        )
                    )
                elif kind == "skipped":
                    results.append(db.CardResult(card, "skipped", reasons=keys))
                    prompter.say(_m("skipped", lang, i=i, k=k, card=card, keys=", ".join(keys)))
                elif kind == "stopped":
                    results.append(db.CardResult(card, "stopped", reasons=keys))
                else:
                    results.append(db.CardResult(card, "failed", reasons=keys))
                    halt = _halt_issue(res)
                    prompter.say(_m("halted", lang, i=i, k=k, card=card, reason=_short(halt.key)))
                b = replace(b, cards=tuple(results))
                db.write_batch(root, b)
                if kind in ("stopped", "halt"):
                    break
            braked = brake.is_set()
            if braked:
                prompter.say(_m("braked", lang))
            b = replace(
                b,
                state="stopped" if braked else "done",
                stop_key=None if halt is None else _short(halt.key),
                ended=_iso(now),
            )
            # The lock goes first: a failed final write never leaves an orphan lock.
            db.release(root, reorg_id)
            released = True
            db.write_batch(root, b)
        except Exception as exc:
            # An ordinary exception closes the batch as interrupted and frees the lock.
            # A BaseException (a dry death, KeyboardInterrupt) is left alone on purpose:
            # «running» and the lock stay for the next run to detect (§6.3).
            if not released:
                b = replace(
                    b,
                    state="interrupted",
                    stop_key=f"exception:{type(exc).__name__}",
                    ended=_iso(now),
                )
                db.release(root, reorg_id)
                with contextlib.suppress(Exception):
                    db.write_batch(root, b)
            raise
        report = _report(root, reorg_id, rep, b, agents_before, tighten_before, dry=dry)
        _save_report(root, b, report, lang)
    if braked:
        code = EXIT_INTERRUPTED
    elif halt is not None:
        code = halt.code  # 5, 3, 1, 2, or 4 (concurrent_edit, restore, P2)
    elif any(c.result != "applied" for c in results):
        code = EXIT_PROOF
    else:
        code = 0
    issues = () if halt is None else (halt,)
    for line in report_md(report, lang).splitlines():
        prompter.say(line)
    return AutoOutcome(code=code, issues=issues, batch=b, report=report)


def _report(
    root: Path,
    reorg_id: str,
    rep: distill.CheckReport,
    b: db.Batch,
    agents_before: tuple[int, int],
    tighten_before: int | None,
    *,
    dry: bool,
) -> Report:
    card_paths = [c.path for c in rep.cards]
    try:
        pending = tuple(gitops.dirty(root, card_paths))
    except gitops.GitError:
        pending = ()
    return Report(
        reorg=reorg_id,
        batch=b.batch,
        branch=b.branch,
        tag=b.tag,
        base_branch=b.base_branch,
        state=b.state,
        stop_key=b.stop_key,
        dry_run=dry,
        applied=tuple(c for c in b.cards if c.result == "applied"),
        skipped=tuple(c for c in b.cards if c.result != "applied"),
        not_ready=tuple(
            (c.card_id, tuple((r.key, r.line, r.col) for r in c.reasons))
            for c in rep.cards
            if c.state == "not_ready"
        ),
        user_skipped=b.skipped_by_user,
        distilled_before=tuple(c.card_id for c in rep.cards if c.state == "distilled"),
        no_card_bytes=rep.totals()["no_card_bytes"],
        agents_before=agents_before,
        agents_after=_measure(root),
        tightened=_tightened_since(root, tighten_before),
        pending_paths=pending,
    )


def _save_report(root: Path, b: db.Batch, report: Report, lang: str) -> None:
    folder = db.batch_dir(root, b.reorg, b.batch)
    fsutil.atomic_write_bytes(folder / "informe.md", report_md(report, lang).encode(), root)
    data = json.dumps(report_obj(report), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fsutil.atomic_write_bytes(folder / "informe.json", data.encode(), root)


# --------------------------------------------------------------------------- undo (§8)

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _ours(root: Path, reorg_id: str, b: db.Batch) -> dict[str, tuple[Path, Journal]]:
    """The journals of THIS batch, by card, in planned order (older ones never count)."""
    out: dict[str, tuple[Path, Journal]] = {}
    for card in b.planned:
        folder = distill_apply.distill_dir(root, reorg_id, card)
        j = read_journal(folder, root)
        if j is not None and j.batch == b.batch:
            out[card] = (folder, j)
    return out


def _first_entries(ours: Mapping[str, tuple[Path, Journal]]) -> dict[str, tuple[Path, str]]:
    """P4: for each output, (card folder, prev sha) of the FIRST card of the batch that
    touched it; that prev sha is the state before the batch."""
    out: dict[str, tuple[Path, str]] = {}
    for folder, j in ours.values():
        for e in j.entries:
            out.setdefault(e.path, (folder, e.prev_sha256))
    return out


def _last_expected(ours: Mapping[str, tuple[Path, Journal]]) -> dict[str, str]:
    """For each output, the sha the batch left it at: the ``expected_sha256`` of the LAST
    committed card of the batch that touched it (rolled-back or restored cards left
    nothing behind)."""
    out: dict[str, str] = {}
    for _folder, j in ours.values():
        if j.state != "committed":
            continue
        for e in j.entries:
            out[e.path] = e.expected_sha256
    return out


def _sha_now(root: Path, rel: str) -> str:
    """sha256 of ``rel`` now; ``absent`` when missing; ``unreadable`` (never a sha) else."""
    try:
        return hashlib.sha256(fsutil.safe_read_bytes(root / rel, root)).hexdigest()
    except fsutil.UnsafeReadError as exc:
        return "absent" if exc.reason == "missing" else "unreadable"


def _restore_untracked(
    root: Path,
    firsts: Mapping[str, tuple[Path, str]],
    lasts: Mapping[str, str],
    skip: set[str],
) -> list[Issue]:
    """P4: tracked outputs came back with the switch or the revert; local data files that
    git does not hold are put back from the backup of the first card that touched them.
    Only a file still at the sha the batch left (``lasts``) is touched: a foreign edit,
    or a file that cannot be read safely, is left as is and reported."""
    issues: list[Issue] = []
    for rel, (folder, prev) in sorted(firsts.items()):
        if rel in skip or gitops.show_bytes(root, "HEAD", rel) is not None:
            continue
        cur = _sha_now(root, rel)
        if cur == prev:
            continue
        if cur != lasts.get(rel):
            issues.append(Issue(code=EXIT_PROOF, key="restore_untouched", dest=rel))
            continue
        try:
            if prev == "absent":
                (root / rel).unlink()
                continue
            data = fsutil.safe_read_bytes(folder / "backup" / rel, root)
            if hashlib.sha256(data).hexdigest() != prev:
                issues.append(Issue(code=EXIT_PROOF, key="distill.not_restored", dest=rel))
                continue
            fsutil.atomic_write_bytes(root / rel, data, root)
        except (OSError, fsutil.UnsafeReadError, fsutil.UnsafeWriteError):
            issues.append(Issue(code=EXIT_PROOF, key="distill.not_restored", dest=rel))
    return issues


def _not_back(root: Path, firsts: Mapping[str, tuple[Path, str]], skip: set[str]) -> list[Issue]:
    """§8.2.3: every output (the cards aside) is back at its state before the batch."""
    return [
        Issue(code=EXIT_PROOF, key="distill.not_restored", dest=rel)
        for rel, (_f, prev) in sorted(firsts.items())
        if rel not in skip and _sha_now(root, rel) != prev
    ]


def _later(root: Path, reorg_id: str, ours: Mapping[str, tuple[Path, Journal]]) -> list[Issue]:
    """LIFO as in 2b §9: a distillation applied after this batch (a later batch or a single
    card) must be undone first. A missing order counts as the latest (fail closed)."""
    rows = distill_apply.status(root, reorg_id)

    def order(seq: int) -> float:
        return seq if seq > 0 else float("inf")

    mine = [order(r.seq) for r in rows if r.card in ours]
    if not mine:
        return []
    first = min(mine)
    return [
        Issue(code=EXIT_USAGE, key="distill.lifo", detail=r.card)
        for r in rows
        if r.card not in ours
        and r.state in distill_apply.ACTIVE
        and (order(r.seq) > first or order(r.seq) == float("inf"))
    ]


def _mark_undone(root: Path, reorg_id: str, b: db.Batch) -> db.Batch:
    """Journals, then ``batch.json``, then the lock (§8.1.2-3, P5)."""
    for folder, j in _ours(root, reorg_id, b).values():
        if j.state != "undone":
            write_journal(folder, replace(j, state="undone"), root)
    b = replace(b, state="undone")
    db.write_batch(root, b)
    lock = db.read_lock(root, reorg_id)
    if lock is not None and (lock.batch == b.batch or not db.pid_alive(lock.pid)):
        db.release(root, reorg_id)
    return b


class _ReconcileRefused(Exception):
    def __init__(self, sha: str) -> None:
        super().__init__(sha)
        self.issue = Issue(code=EXIT_USAGE, key="distill.auto.reconcile_refused", detail=sha)


def _reconcile(
    root: Path, reorg_id: str, b: db.Batch, ours: dict[str, tuple[Path, Journal]]
) -> db.Batch:
    """A card that died right after its ``git commit``: the journal says ``written`` with
    no commit, and the tip of the batch branch (whatever branch is checked out, so a
    merged batch is reconciled too) is a commit not yet in ``batch.json`` that carries
    this card's and this batch's trailers. It is taken as the card's commit only if its
    parent is the last commit of the batch (or the batch tag when there is none) and it
    changes nothing but the card's outputs; otherwise ``_ReconcileRefused`` (a manual
    commit that copied the trailers is never reverted). The journal goes to
    ``committed`` and the card counts as applied. 2b's ``_reconcile`` is not used: it
    compares with the per-card tag, and in a batch the tag is the batch's."""
    cand = gitops.rev_commit(root, f"refs/heads/{b.branch}")
    known = [c.commit for c in b.cards if c.commit]
    if cand is None or cand in known:
        return b
    body = gitops.run(root, "log", "-1", "--format=%B", cand).decode("utf-8", "replace")
    if f"Declutter-Distill-Batch: {reorg_id}/{b.batch}" not in body.splitlines():
        return b
    for card, (folder, j) in ours.items():
        if j.state != "written" or j.commit is not None:
            continue
        if f"Declutter-Distill: {reorg_id}/{card} " not in body:
            continue
        parent = gitops.rev_commit(root, f"{cand}^")
        expected = known[-1] if known else gitops.rev_commit(root, f"refs/tags/{b.tag}")
        allowed = {e.path for e in j.entries} | {distill_apply.card_file(root, reorg_id, card)}
        if parent is None or parent != expected:
            raise _ReconcileRefused(cand[:12])
        if not gitops.diff_names(root, f"{cand}^", cand) <= allowed:
            raise _ReconcileRefused(cand[:12])
        j = replace(j, state="committed", commit=cand)
        write_journal(folder, j, root)
        ours[card] = (folder, j)
        cards = [c for c in b.cards if c.card != card]
        cards.append(db.CardResult(card, "applied", commit=cand))
        order = {c: k for k, c in enumerate(b.planned)}
        b = replace(b, cards=tuple(sorted(cards, key=lambda c: order.get(c.card, len(order)))))
        db.write_batch(root, b)
        break
    return b


def _half_done(root: Path, n: int, ours: Mapping[str, tuple[Path, Journal]]) -> list[Issue]:
    """§8.4: the card left half-done (any non-final journal of this batch) gets the 2b §9
    remedy first, on the batch branch: its writes go back and its stage is removed."""
    for folder, j in ours.values():
        if j.batch != n or j.state in ("committed", "undone"):
            continue
        untouched = restore(root, folder, j)
        stage = folder / "stage"
        if stage.is_dir() and not stage.is_symlink():
            shutil.rmtree(stage, ignore_errors=True)
        if untouched:
            return [Issue(code=EXIT_PROOF, key="restore_untouched", dest=p) for p in untouched]
    return []


class _KeepCard(Exception):
    """The card is not restored (edited, or its backup does not match)."""


def _restore_cards(
    root: Path,
    reorg_id: str,
    ours: Mapping[str, tuple[Path, Journal]],
    prompter: Prompter,
    lang: str,
) -> None:
    """After a successful undo (merged or not), each card of the batch goes back to the
    filled state it had before the batch (the AI fill is not thrown away; spec §8.1/§8.2,
    «esta spec, 12-11»). Only when nobody touched the card since: the disk equals the
    base's version (``git show HEAD:<path>``) and the journal's backup of THAT card has
    the entry's ``prev_sha256``. Otherwise the card is left alone and named in a
    ``distill.auto.card_kept`` line (path and card id only). It stays an uncommitted
    modification, as before the batch."""
    for card, (folder, j) in sorted(ours.items()):
        rel = distill_apply.card_file(root, reorg_id, card)
        entry = next((e for e in j.entries if e.path == rel), None)
        if entry is None:
            continue
        cur = _sha_now(root, rel)
        if cur == entry.prev_sha256:
            continue  # already as before the batch
        try:
            disk = fsutil.safe_read_bytes(root / rel, root)
            head = gitops.show_bytes(root, "HEAD", rel)
            backup = fsutil.safe_read_bytes(folder / "backup" / rel, root)
            if (
                head is None
                or disk != head
                or hashlib.sha256(backup).hexdigest() != entry.prev_sha256
            ):
                raise _KeepCard
            fsutil.atomic_write_bytes(root / rel, backup, root)
        except (
            _KeepCard,
            OSError,
            gitops.GitError,
            fsutil.UnsafeReadError,
            fsutil.UnsafeWriteError,
        ):
            prompter.say(_m("card_kept", lang, card=card, path=rel))


def undo_batch(
    project: Project,
    cfg: Config,
    reorg_id: str,
    n: int,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
) -> AutoOutcome:
    """``declutter reorg distill-undo <id> --auto <n>`` (fase 3A §8). Never deletes the branch
    or the tag; deletes the lock when it is this batch's or its pid is dead (P5). A merged
    batch needs a TTY and «confirmo».

    If the outputs are not back at their state before the batch AFTER the reverts (exit
    4, ``distill.not_restored`` names the files), the reverts are committed but the batch
    still reads as merged: do not run the undo again blindly, check those files first.
    """
    root = project.root
    try:
        b = db.read_batch(root, reorg_id, n)
        if b is None:
            return _fail([Issue(code=EXIT_USAGE, key="distill.auto.no_batch", detail=str(n))])
        if b.state == "undone":
            return AutoOutcome(code=0, issues=(), batch=b)
        lock = db.read_lock(root, reorg_id)
        if lock is not None and db.pid_alive(lock.pid):
            return _fail(
                [Issue(code=EXIT_USAGE, key="distill.auto.locked", detail=f"pid {lock.pid}")]
            )
        ours = _ours(root, reorg_id, b)
        later = _later(root, reorg_id, ours)
        if later:
            return _fail(later)
        try:
            b = _reconcile(root, reorg_id, b, ours)
        except _ReconcileRefused as exc:
            return _fail([exc.issue])
        card_paths = {distill_apply.card_file(root, reorg_id, c) for c in b.planned}
        firsts = _first_entries(ours)
        lasts = _last_expected(ours)
        applied = [c for c in b.cards if c.result == "applied" and c.commit]
        tip = gitops.rev_commit(root, f"refs/heads/{b.branch}")
        merged = bool(applied) and tip is not None and gitops.is_ancestor(root, tip, b.base_branch)
        head = gitops.head_branch(root)
        if not merged and head not in (b.branch, b.base_branch):
            # Only the batch branch or the base: another branch is left untouched.
            return _fail([Issue(code=EXIT_USAGE, key="wrong_branch", detail=head or "HEAD")])
        issues = _half_done(root, n, ours)
        if issues:
            return _fail(issues)
        if merged:
            issues = _undo_merged(root, b, applied, ours, prompter, lang)
        elif head != b.base_branch:
            try:
                gitops.switch(root, b.base_branch)
            except gitops.GitError as exc:
                issues = [Issue(code=EXIT_USAGE, key="switch_refused", detail=str(exc))]
        if issues:
            return _fail(issues)
        issues = _restore_untracked(root, firsts, lasts, card_paths)
        issues += _not_back(root, firsts, card_paths)
        if issues:
            return _fail(issues)
        _restore_cards(root, reorg_id, ours, prompter, lang)
        b = _mark_undone(root, reorg_id, b)
    except ReorgError as exc:
        return _fail(exc.issues)
    except gitops.GitError as exc:
        return _fail([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))])
    prompter.say(_m("undo.done", lang, n=n, branch=b.branch, tag=b.tag))
    return AutoOutcome(code=0, issues=(), batch=b)


def _undo_merged(
    root: Path,
    b: db.Batch,
    applied: Sequence[db.CardResult],
    ours: Mapping[str, tuple[Path, Journal]],
    prompter: Prompter,
    lang: str,
) -> list[Issue]:
    """§8.2: on the base branch, with no operation in progress, a clean index and clean
    outputs (as 2b), list the commits (subject and files only), ask «confirmo» in a TTY
    (``--yes`` never skips it), check again that nothing moved, then revert every commit
    of the batch in ONE ``git revert`` (newest first). On a conflict ``revert --abort``
    puts the base back at its previous HEAD, which is checked."""
    if gitops.head_branch(root) != b.base_branch:
        return [Issue(code=EXIT_USAGE, key="wrong_branch", detail=b.base_branch)]
    op = gitops.operation_in_progress(root)
    if op is not None:
        return [Issue(code=EXIT_USAGE, key="operation_in_progress", detail=op)]
    # A foreign staged change would be swept into a revert commit, and a dirty output
    # would mix with it; refuse both (fail closed, as 2b).
    outputs = sorted({e.path for c in applied if c.card in ours for e in ours[c.card][1].entries})
    staged = gitops.run(root, "diff", "--cached", "--name-only", "-z", "--no-relative")
    names = sorted(f.decode("utf-8", "surrogateescape") for f in staged.split(b"\0") if f)
    blocking = [Issue(code=EXIT_USAGE, key="index_not_clean", dest=x) for x in names]
    blocking += [Issue(code=EXIT_USAGE, key="dirty", dest=x) for x in gitops.dirty(root, outputs)]
    if blocking:
        return blocking
    if not prompter.is_tty():
        return [Issue(code=EXIT_USAGE, key="distill.auto.no_tty")]
    before = gitops.head_commit(root)
    shas: list[str] = []
    for c in reversed(applied):
        sha = c.commit or ""
        if not _SHA.fullmatch(sha) or gitops.rev_commit(root, sha) != sha:
            return [Issue(code=EXIT_INTERNAL, key="batch_invalid", detail=f"{c.card} commit")]
        shas.append(sha)
        subject = gitops.run(root, "show", "--no-patch", "--format=%s", sha).decode(
            "utf-8", "replace"
        )
        files = ", ".join(sorted(gitops.diff_names(root, f"{sha}^", sha)))
        prompter.say(
            _m("undo.commit_row", lang, sha=sha[:12], subject=subject.strip(), files=files)
        )
    word = prompter.ask(_m("undo.ask", lang, n=b.batch, branch=b.base_branch, k=len(applied)))
    if word.strip().casefold() not in ("confirmo", "confirm"):
        return [Issue(code=EXIT_USAGE, key="distill.auto.not_confirmed")]
    # Nothing may have moved while the question was open.
    if gitops.head_branch(root) != b.base_branch or gitops.head_commit(root) != before:
        return [Issue(code=EXIT_USAGE, key="distill.auto.head_moved", detail=b.base_branch)]
    lock = db.read_lock(root, b.reorg)
    if lock is not None and db.pid_alive(lock.pid):
        return [Issue(code=EXIT_USAGE, key="distill.auto.locked", detail=f"pid {lock.pid}")]
    op = gitops.operation_in_progress(root)
    if op is not None:
        return [Issue(code=EXIT_USAGE, key="operation_in_progress", detail=op)]
    assert before is not None
    try:
        # One sequence: --no-edit and core.editor=true, never an editor; hooks run.
        gitops.run(root, "-c", "core.editor=true", "revert", "--no-edit", *shas)
    except gitops.GitError:
        with contextlib.suppress(gitops.GitError):
            gitops.revert_abort(root)
        now_head = gitops.head_commit(root)
        if now_head != before:
            left = gitops.run(root, "rev-list", "--reverse", f"{before}..HEAD", check=False)
            done = " ".join(x[:12] for x in left.decode("utf-8", "replace").split())
            return [
                Issue(
                    code=EXIT_USAGE,
                    key="distill.auto.revert_partial",
                    detail=done or (now_head or "HEAD")[:12],
                )
            ]
        return [
            Issue(code=EXIT_USAGE, key="revert_conflict", detail=" ".join(x[:12] for x in shas))
        ]
    return []
