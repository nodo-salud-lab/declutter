"""``declutter reorg distill-apply`` and ``distill-undo``: one card per call (fase 2b §6-§9).

``distill-apply`` replaces, in ``AGENTS.md``, every G1 copy of one card's unit (header,
literal lines and blank line, §4.2) by the card's distilled entry (§6.2) inside the
region ``## Invariantes de seguridad (destiladas)`` (§6.1), turns the card's state line
and its index row and pointers into «destilada» (§6.3), adds the ``invariant`` and
``card`` pins (§7.1), logs a ``distill`` event (and a ``tighten`` one when a maximum of
the ratchet goes down, §7.5) and leaves everything audited and reversible.

The transaction follows 2a §11.2 with its own journal in
``.declutter/distill/<id>/<D-NNN>/``: nothing is written before the TTY confirmation
(motive and the word ``confirmo``/``confirm``; ``--yes`` never skips it); then stage,
proof of the stage, tag ``declutter/pre-distill-<id>-<D-NNN>`` and branch
``declutter/distill-<id>-<D-NNN>``, backups, compare-and-swap writes with ``AGENTS.md``
last (the only file literal text leaves), proof of the disk, scope check and the
optional commit. ``distill-undo`` reverts the last distillation of a run (LIFO).

Conservative choices (noted for the owner): the confirmation comes before the stage is
written (so a refusal writes nothing at all, not even the local journal); every
``placements[]`` entry of the event carries the run coordinates ``at`` of the placement
it changes, so that equal blank lines are never confused; ``files[].lines_delta`` of the
card is counted from its expected length (the skill filled it before distilling), and
from the current length for every other file, so that a foreign edit is still reported
by ``verify --strict``.

Messages and issues carry ids, paths, line numbers, counts, bytes and shas; never a byte
of a card, a literal or an invariant (G6).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import fsutil, ratchet
from ..i18n import t
from ..model import Config, Pin, Placement, Project
from . import (
    EXIT_INTERNAL,
    EXIT_INTERRUPTED,
    EXIT_PROOF,
    EXIT_SECRET,
    EXIT_USAGE,
    Issue,
    Prompter,
    ReorgError,
    distill,
    gitops,
    mdblocks,
    proof,
    resolve_code,
)
from . import manifest as mf
from . import overlay as ov
from .apply import (
    PLACEMENT_NAME,
    ApplyOutcome,
    Journal,
    JournalEntry,
    read_journal,
    restore,
    write_journal,
)
from .build import anchor, section_titles
from .lexicon import Lexicon, build_lexicon, scan_secrets
from .undo import verify_run

__all__ = [
    "DISTILL_DIR",
    "INTERRUPTED",
    "BatchSpec",
    "DistillOptions",
    "DistillStatus",
    "apply",
    "apply_batch_card",
    "batch_preflight",
    "branch_name",
    "card_file",
    "distill_dir",
    "status",
    "tag_name",
    "undo",
]

DISTILL_DIR = ".declutter/distill"
RECORD_NAME = "record.json"
INTERRUPTED = frozenset({"staged", "tagged", "backed_up", "writing"})
ACTIVE = INTERRUPTED | {"written", "committed"}
AGENTS = "AGENTS.md"
GEN_END_INVARIANTS = b"<!-- declutter:gen-end invariants -->"
GEN_END_DISTILLED = b"<!-- declutter:gen-end invariants-distilled -->"
_CONFIRM_WORDS = frozenset({"confirmo", "confirm"})
_YES = frozenset({"s", "si", "sí", "y", "yes"})
_HEADER_PREFIX = "### ⟨".encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _enc(text: str) -> bytes:
    return text.encode("utf-8")


def _join(lines: Iterable[bytes]) -> bytes:
    return b"".join(x + b"\n" for x in lines)


def branch_name(reorg_id: str, card: str) -> str:
    return f"declutter/distill-{reorg_id}-{card}"


def tag_name(reorg_id: str, card: str) -> str:
    return f"declutter/pre-distill-{reorg_id}-{card}"


def distill_dir(root: Path, reorg_id: str, card: str) -> Path:
    return root / DISTILL_DIR / reorg_id / card


def _m(key: str, lang: str, **kw: object) -> str:
    return t(f"distill.apply.{key}", lang, **kw)


# --------------------------------------------------------------------------- options


@dataclass(frozen=True)
class DistillOptions:
    commit: bool = False
    yes: bool = False
    no_git: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class BatchSpec:
    """A card applied inside a distill-auto batch (fase 3A §5.3 step 4): no confirmation,
    on the batch branch (already created), with the brake checked at §6.1 points 2-3."""

    batch: int
    branch: str
    tag: str
    base_branch: str
    reason: str
    stop: Callable[[], bool]


class _Stopped(Exception):
    """The brake was found set before anything was written (§6.1)."""


def _outcome(issues: Iterable[Issue], j: Journal | None) -> ApplyOutcome:
    items = tuple(dict.fromkeys(issues))
    return ApplyOutcome(code=resolve_code(i.code for i in items), issues=items, journal=j)


def _issue(code: int, key: str, **kw: Any) -> Issue:
    return Issue(code=code, key=f"distill.{key}", **kw)


def _fail(code: int, key: str, j: Journal | None = None, **kw: Any) -> ApplyOutcome:
    return _outcome([_issue(code, key, **kw)], j)


class _Abort(Exception):
    def __init__(self, issues: Sequence[Issue]) -> None:
        super().__init__(", ".join(i.key for i in issues))
        self.issues = tuple(issues)


# --------------------------------------------------------------------------- disk helpers


def _read(root: Path, rel: str) -> bytes | None:
    try:
        return fsutil.safe_read_bytes(root / rel, root)
    except fsutil.UnsafeReadError as exc:
        if exc.reason == "missing":
            return None
        raise ReorgError(
            [Issue(code=EXIT_INTERNAL, key="unsafe_read", dest=rel, detail=exc.reason)]
        ) from None


def _sha_now(root: Path, rel: str) -> str:
    data = _read(root, rel)
    return "absent" if data is None else _sha(data)


def _rmtree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


def _overlay_reader(root: Path, files: Mapping[str, bytes]) -> Callable[[str], bytes | None]:
    disk = distill.disk_reader(root)

    def read(rel: str) -> bytes | None:
        if rel in files:
            return files[rel]
        try:
            return disk(rel)
        except ReorgError:
            return None

    return read


def _record_path(folder: Path) -> Path:
    return folder / RECORD_NAME


def _read_record(root: Path, folder: Path) -> dict[str, Any]:
    try:
        raw = fsutil.safe_read_bytes(_record_path(folder), root)
        obj = json.loads(raw.decode("utf-8"))
    except (fsutil.UnsafeReadError, UnicodeDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_record(root: Path, folder: Path, obj: Mapping[str, object]) -> None:
    data = (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fsutil.atomic_write_bytes(_record_path(folder), data, root)


# --------------------------------------------------------------------------- status


@dataclass(frozen=True)
class DistillStatus:
    card: str
    state: str
    interrupted: bool
    date: str | None
    commit: str | None
    seq: int


def _distills(root: Path, reorg_id: str) -> list[tuple[DistillStatus, Path, Journal | None]]:
    base = root / DISTILL_DIR / reorg_id
    if not base.is_dir() or base.is_symlink():
        return []
    out: list[tuple[DistillStatus, Path, Journal | None]] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.is_symlink() or not child.name.startswith("D-"):
            continue
        try:
            j = read_journal(child, root)
        except ReorgError:
            j = None
        rec = _read_record(root, child)
        state = "planned" if j is None else j.state
        seq = rec.get("seq")
        date = rec.get("date")
        out.append(
            (
                DistillStatus(
                    card=child.name,
                    state=state,
                    interrupted=state in INTERRUPTED,
                    date=date if isinstance(date, str) else None,
                    commit=None if j is None else j.commit,
                    seq=seq if isinstance(seq, int) and not isinstance(seq, bool) else 0,
                ),
                child,
                j,
            )
        )
    return out


def status(root: Path, reorg_id: str) -> tuple[DistillStatus, ...]:
    """The distillations of a run, in application order (fase 2b §9)."""
    rows = [r for r, _f, _j in _distills(root, reorg_id)]
    return tuple(sorted(rows, key=lambda r: (r.seq, r.card)))


# --------------------------------------------------------------------------- context


@dataclass
class _Ctx:
    project: Project
    cfg: Config
    reorg_id: str
    card: str
    opts: DistillOptions
    prompter: Prompter
    now: datetime
    lang: str
    root: Path
    run: Path
    folder: Path
    m: mf.Manifest
    placements: tuple[Placement, ...]
    lexicon: Lexicon
    use_git: bool = False
    status_before: frozenset[str] = frozenset()
    journal: Journal | None = None
    batch: BatchSpec | None = None

    @property
    def branch(self) -> str:
        return (
            self.batch.branch if self.batch is not None else branch_name(self.reorg_id, self.card)
        )

    @property
    def tag(self) -> str:
        return self.batch.tag if self.batch is not None else tag_name(self.reorg_id, self.card)

    def save(self, j: Journal) -> None:
        self.journal = j
        write_journal(self.folder, j, self.root)

    def yes_no(self, prompt: str) -> bool:
        return self.prompter.ask(prompt).strip().lower() in _YES

    def undo_cmd(self) -> str:
        if self.batch is not None:
            return f"declutter reorg distill-undo {self.reorg_id} --auto {self.batch.batch}"
        return f"declutter reorg distill-undo {self.reorg_id} --card {self.card}"


def _brake(ctx: _Ctx) -> None:
    if ctx.batch is not None and ctx.batch.stop():
        raise _Stopped


def _unit_of(m: mf.Manifest, card: str) -> mf.UnitEntry:
    for u in m.units:
        if u.card_id == card and u.dest == "L4":
            return u
    raise ReorgError([_issue(EXIT_USAGE, "card_unknown", detail=card)])


def _outputs(m: mf.Manifest, u: mf.UnitEntry, card: str) -> list[str]:
    """Declared outputs of the distillation (§6.4), in write order (§8.2 step 7)."""
    ups = distill.generated_updates(m, u, card)
    zone = [x.dest for x in ups if x.kind == "zone"]
    index = [x.dest for x in ups if x.kind == "index"]
    return [
        distill.card_path(m, u),
        *index,
        *zone,
        ratchet.PINS_REL,
        ratchet.RATCHET_REL,
        ratchet.LOG_REL,
        AGENTS,
    ]


# --------------------------------------------------------------------------- build (§6, §7)


@dataclass(frozen=True)
class _Built:
    files: Mapping[str, bytes]  # every output that changes -> its new bytes
    prev: Mapping[str, bytes]  # its current bytes
    check: distill.CardCheck
    pins_new: tuple[Pin, Pin]
    tightened: tuple[tuple[str, int, int], ...]  # (key, old, new)
    agents_before: tuple[int, int]  # (bytes, lines)
    agents_after: tuple[int, int]
    card_sha: str


def _controls(root: Path) -> tuple[tuple[Pin, ...], bytes, tuple[Mapping[str, object], ...]]:
    raw_pins = _read(root, ratchet.PINS_REL)
    raw_log = _read(root, ratchet.LOG_REL)
    if raw_pins is None or raw_log is None:
        missing = ratchet.PINS_REL if raw_pins is None else ratchet.LOG_REL
        raise ReorgError([Issue(code=EXIT_INTERNAL, key="control_invalid", dest=missing)])
    try:
        pins = ratchet.pins_loads(raw_pins)
        events = ratchet.log_events(raw_log)
    except ratchet.RatchetSchemaError as exc:
        raise ReorgError(
            [Issue(code=EXIT_INTERNAL, key="control_invalid", detail=f"{exc.where}: {exc.reason}")]
        ) from None
    return pins, raw_log, events


def _entry_key(m: mf.Manifest, placements: Sequence[Placement], card: str) -> tuple[int, int]:
    """(source index, src0 of the card's first copy): the order of the entries (§6.1)."""
    unit = next((u for u in m.units if u.card_id == card), None)
    if unit is None:
        raise _Abort([_issue(EXIT_PROOF, "region_changed", detail=card)])
    idx = {s.path: i for i, s in enumerate(m.sources)}
    keys = [
        (idx.get(p.source or "", len(idx)), (p.src or (0, 0))[0])
        for p in placements
        if p.role == "copy" and p.dest == AGENTS and p.unit == unit.id
    ]
    if not keys:
        raise _Abort([_issue(EXIT_PROOF, "region_changed", detail=card)])
    return min(keys)


def _entry_card(line: bytes) -> str | None:
    if not line.startswith(_HEADER_PREFIX):
        return None
    try:
        text = mdblocks.decode_line(line)
    except UnicodeDecodeError:
        return None
    rest = text.split("⟩", 1)
    if len(rest) != 2:
        return None
    word = rest[1].strip().split(" ", 1)[0]
    return word if word.startswith("D-") else None


def _new_agents(
    ctx: _Ctx, lines: Sequence[bytes], cc: distill.CardCheck, entry: distill.Entry
) -> tuple[list[bytes], list[dict[str, object]]]:
    """``AGENTS.md`` without the copies of the card and with its entry (§6.1-§6.2), and
    the ``generated[]`` spans of the event (§7.3)."""
    titles = section_titles(ctx.m.lang)
    title = _enc(f"## {titles['invariants_distilled']}")
    marker = _enc(f"<!-- declutter:gen invariants-distilled {ctx.m.id} -->")
    drop: set[int] = set()
    for c in cc.copies:
        if c.at is None:
            raise _Abort([_issue(EXIT_PROOF, "copy_not_located", block=c.block)])
        drop.update(range(c.at[0] - 1, c.at[1] + 2))  # header, copy lines, blank (1-based)
    ends = [i for i, x in enumerate(lines, 1) if x == GEN_END_INVARIANTS]
    titles_at = [i for i, x in enumerate(lines, 1) if x == title]
    if len(ends) != 1 or ends[0] in drop:
        raise _Abort([_issue(EXIT_PROOF, "region_changed", dest=AGENTS)])
    g = ends[0]
    blank_sha = mdblocks.lines_sha([b""])
    generated: list[dict[str, object]] = []
    insert_before: int | None = None  # 1-based line before which the entry goes
    create = not titles_at
    if not create:
        ok = (
            titles_at == [g + 2]
            and len(lines) >= g + 3
            and lines[g] == b""
            and lines[g + 2] == marker
            and lines.count(GEN_END_DISTILLED) == 1
        )
        if not ok:
            raise _Abort([_issue(EXIT_PROOF, "region_changed", dest=AGENTS)])
        close = lines.index(GEN_END_DISTILLED) + 1
        if close <= g + 3:
            raise _Abort([_issue(EXIT_PROOF, "region_changed", dest=AGENTS)])
        mine = _entry_key(ctx.m, ctx.placements, ctx.card)
        insert_before = close
        try:
            texts = [mdblocks.decode_line(x) for x in lines]
        except UnicodeDecodeError:
            raise _Abort([_issue(EXIT_PROOF, "region_changed", dest=AGENTS)]) from None
        kinds = mdblocks.scan(texts, strict=False)
        fenced = {"fence_open", "fence_body", "fence_close"}
        for i in range(g + 4, close):
            if kinds[i - 1] in fenced:  # a header-like line inside an invariant's fence
                continue
            other = _entry_card(lines[i - 1])
            if other is None:
                continue
            if other == ctx.card:
                raise _Abort([_issue(EXIT_PROOF, "region_changed", dest=AGENTS, at=(i, i))])
            if _entry_key(ctx.m, ctx.placements, other) > mine:
                insert_before = i
                break
    out: list[bytes] = []
    for i, line in enumerate(lines, 1):
        if insert_before == i:
            out.extend(entry.lines)
        if i in drop:
            continue
        out.append(line)
        if create and i == g:
            out += [b"", title, marker, *entry.lines, GEN_END_DISTILLED]
    if create:
        generated += [
            {"dest": AGENTS, "kind": "blank", "sha256": blank_sha, "lines": 1},
            {
                "dest": AGENTS,
                "kind": "invariants-distilled",
                "sha256": mdblocks.lines_sha([title, marker]),
                "lines": 2,
            },
            {
                "dest": AGENTS,
                "kind": "invariants-distilled",
                "sha256": mdblocks.lines_sha([GEN_END_DISTILLED]),
                "lines": 1,
            },
        ]
    generated.append({"dest": AGENTS, "kind": "blank", "sha256": blank_sha, "lines": 1})
    return out, generated


def _change(p: Placement, old: tuple[str, int], new: tuple[str, int] | None) -> dict[str, object]:
    return {
        "dest": p.dest,
        "kind": p.kind or p.role,
        "at": [p.at[0], p.at[1]],
        "old_sha256": old[0],
        "old_lines": old[1],
        "new_sha256": None if new is None else new[0],
        "new_lines": 0 if new is None else new[1],
    }


def _build(ctx: _Ctx, cc: distill.CardCheck, reason: str) -> _Built:
    """Every output of §6.4 in memory, with the ``distill`` (and ``tighten``) events."""
    m, root = ctx.m, ctx.root
    u = _unit_of(m, ctx.card)
    entry = cc.entry
    if entry is None:
        raise _Abort([_issue(EXIT_PROOF, "not_ready", detail="entry")])
    pins, raw_log, events = _controls(root)
    over = ov.build(m, ctx.placements, events, pins)
    expected = over.expected
    index_of = {id(p): i for i, p in enumerate(ctx.placements)}
    files: dict[str, bytes] = {}
    prev: dict[str, bytes] = {}
    changes: list[dict[str, object]] = []
    file_rows: list[dict[str, object]] = []

    def exp(p: Placement) -> tuple[str, int]:
        e = expected[index_of[id(p)]]
        if e is None:
            raise _Abort([_issue(EXIT_PROOF, "region_changed", dest=p.dest, at=p.at)])
        return e

    def place_at(dest: str, line: int) -> Placement:
        for p in ctx.placements:
            if p.dest == dest and p.at == (line, line) and p.role == "generated":
                return p
        raise _Abort([_issue(EXIT_PROOF, "header_changed", dest=dest, at=(line, line))])

    # AGENTS.md (§4.2, §6.1, §6.2).
    agents_raw = _read(root, AGENTS) or b""
    agents = mdblocks.split_lines(agents_raw).lines
    copy_places = {
        p.block: p
        for p in ctx.placements
        if p.role == "copy" and p.dest == AGENTS and p.unit == u.id
    }
    old_pins: list[dict[str, object]] = []
    for c in cc.copies:
        p = copy_places.get(c.block)
        if p is None or c.pin is None:
            raise _Abort([_issue(EXIT_PROOF, "pin_missing", block=c.block)])
        pin = next(x for x in pins if x.id == c.pin)
        old_pins.append(
            {
                "id": pin.id,
                "block": c.block,
                "sha256": pin.sha256,
                "lines": pin.lines,
                "src": f"{c.source}:{c.src[0]}-{c.src[1]}",
            }
        )
        hp = place_at(AGENTS, p.at[0] - 1)
        bp = place_at(AGENTS, p.at[1] + 1)
        changes += [
            _change(hp, exp(hp), None),
            _change(p, exp(p), None),
            _change(bp, exp(bp), None),
        ]
    new_agents, generated = _new_agents(ctx, agents, cc, entry)
    files[AGENTS] = _join(new_agents)
    prev[AGENTS] = agents_raw

    # The card (§6.3 line 2) and its placement (§7.3: expected old sha, frozen new sha).
    path = distill.card_path(m, u)
    card_raw = _read(root, path)
    if card_raw is None:
        raise _Abort([_issue(EXIT_PROOF, "card_unreadable", dest=path)])
    card_lines = list(mdblocks.split_lines(card_raw).lines)
    titles = section_titles(m.lang)
    card_lines[1] = _enc(titles["card_state_line_distilled"])
    if card_lines.count(distill.GEN_END_CARD) != 1:
        raise _Abort([_issue(EXIT_PROOF, "card_markers", dest=path)])
    end = card_lines.index(distill.GEN_END_CARD)
    cp = next(
        (
            p
            for p in ctx.placements
            if p.dest == path and p.role == "generated" and p.kind == "card"
        ),
        None,
    )
    if cp is None:
        raise _Abort([_issue(EXIT_PROOF, "card_markers", dest=path)])
    cp_old = exp(cp)
    trailing = cp_old[1] - (distill.card_end_index(m, u, ctx.card) + 1)
    window = card_lines[: end + 1 + max(trailing, 0)]
    changes.append(_change(cp, cp_old, (mdblocks.lines_sha(window), len(window))))
    files[path] = _join(card_lines)
    prev[path] = card_raw
    card_pin_lines = card_lines[: end + 1]
    card_sha = mdblocks.lines_sha(card_pin_lines)

    # Index row and pointers (§6.3), inside the expected windows of their placements.
    for up in distill.generated_updates(m, u, ctx.card):
        raw = files.get(up.dest)
        if raw is None:
            raw = _read(root, up.dest)
            if raw is None:
                raise _Abort([_issue(EXIT_PROOF, up.key, dest=up.dest)])
            prev[up.dest] = raw
        cur = list(mdblocks.split_lines(raw).lines)
        hits: list[tuple[Placement, int, int]] = []  # (placement, start, line index)
        for p in ctx.placements:
            if p.role != "generated" or p.dest != up.dest or p.kind != up.kind:
                continue
            e = expected[index_of[id(p)]]
            if e is None:
                continue
            start = ratchet.find_window(cur, e[0], e[1])
            if start is None:
                continue
            for k in range(start - 1, start - 1 + e[1]):
                if cur[k] == up.old:
                    hits.append((p, start, k))
        if len(hits) != 1:
            raise _Abort([_issue(EXIT_PROOF, up.key, dest=up.dest)])
        p, start, k = hits[0]
        e = exp(p)
        cur[k] = up.new
        changes.append(_change(p, e, (mdblocks.lines_sha(cur[start - 1 : start - 1 + e[1]]), e[1])))
        files[up.dest] = _join(cur)

    # files[] (§7.3): the card's delta from its expected length, the rest from the disk.
    for rel in sorted(files):
        after = len(mdblocks.split_lines(files[rel]).lines)
        if rel == path:  # the card placement's own change (§7.3, verified by overlay)
            delta = len(window) - cp_old[1]
        else:
            delta = after - len(mdblocks.split_lines(prev[rel]).lines)
        file_rows.append(
            {
                "path": rel,
                "lines_delta": delta,
                "sha256_before": _sha(prev[rel]),
                "sha256_after": _sha(files[rel]),
            }
        )

    # Pins (§7.1).
    cats = tuple(sorted({c for x in cc.copies for c in x.categories}))
    nxt = max((int(p.id[2:]) for p in pins if p.id[2:].isdigit()), default=0) + 1
    inv_pin = Pin(
        id=f"P-{nxt:03d}",
        reorg=m.id,
        unit=u.id,
        block=None,
        file=AGENTS,
        role="invariant",
        categories=cats,
        lines=len(entry.window),
        sha256=entry.sha256,
    )
    card_pin = Pin(
        id=f"P-{nxt + 1:03d}",
        reorg=m.id,
        unit=u.id,
        block=None,
        file=path,
        role="card",
        categories=cats,
        lines=len(card_pin_lines),
        sha256=card_sha,
    )
    raw_pins = _read(root, ratchet.PINS_REL) or b""
    files[ratchet.PINS_REL] = ratchet.pins_dumps([*pins, inv_pin, card_pin])
    prev[ratchet.PINS_REL] = raw_pins

    # The distill event (§7.3).
    today = ctx.now.date().isoformat()
    log = raw_log if not raw_log or raw_log.endswith(b"\n") else raw_log + b"\n"
    log += ratchet.log_line(
        "distill",
        date=today,
        reorg=m.id,
        old={"pins": old_pins, "card_sha256": cp_old[0]},
        new={
            "pins": [inv_pin.id, card_pin.id],
            "entry_sha256": entry.sha256,
            "entry_lines": len(entry.window),
            "card_sha256": card_sha,
            "card_lines": len(card_pin_lines),
        },
        reason=reason,
        extra={
            "distill": f"{m.id}/{ctx.card}",
            "card": ctx.card,
            "unit": u.id,
            "placements": changes,
            "generated": generated,
            "files": file_rows,
        },
    )

    # Tighten (§7.5): only lowers a maximum; never a baseline (§7.4).
    tightened: list[tuple[str, int, int]] = []
    raw_state = _read(root, ratchet.RATCHET_REL)
    if raw_state is None:
        raise ReorgError([Issue(code=EXIT_USAGE, key="no_baseline", dest=ratchet.RATCHET_REL)])
    try:
        state = ratchet.ratchet_loads(raw_state)
    except ratchet.RatchetSchemaError as exc:
        raise ReorgError(
            [Issue(code=EXIT_INTERNAL, key="control_invalid", detail=f"{exc.where}: {exc.reason}")]
        ) from None
    text_files = {k: v for k, v in files.items() if not k.startswith(".declutter/")}
    try:
        s1, pct = ratchet.measure(ctx.project, ctx.cfg, text_files)
    except Exception as exc:  # an unsafe read while measuring
        raise ReorgError(
            [Issue(code=EXIT_INTERNAL, key="unsafe_read", detail=type(exc).__name__)]
        ) from None
    s3 = math.ceil(pct)
    new_state = replace(state, s1_max=min(state.s1_max, s1), s3_max_pct=min(state.s3_max_pct, s3))
    if new_state != state:
        if new_state.s1_max != state.s1_max:
            tightened.append(("S1_max", state.s1_max, new_state.s1_max))
        if new_state.s3_max_pct != state.s3_max_pct:
            tightened.append(("S3_max_pct", state.s3_max_pct, new_state.s3_max_pct))
        dumped = ratchet.ratchet_dumps(new_state)
        files[ratchet.RATCHET_REL] = dumped
        prev[ratchet.RATCHET_REL] = raw_state
        log += ratchet.log_line(
            "tighten",
            date=today,
            reorg=m.id,
            old={"S1_max": state.s1_max, "S3_max_pct": state.s3_max_pct},
            new={"S1_max": new_state.s1_max, "S3_max_pct": new_state.s3_max_pct},
            reason=f"declutter reorg distill-apply {m.id} --card {ctx.card}",
            extra={"distill": f"{m.id}/{ctx.card}", "file_sha256": _sha(dumped)},
        )
    files[ratchet.LOG_REL] = log
    prev[ratchet.LOG_REL] = raw_log
    return _Built(
        files=files,
        prev=prev,
        check=cc,
        pins_new=(inv_pin, card_pin),
        tightened=tuple(tightened),
        agents_before=(len(agents_raw), len(agents)),
        agents_after=(len(files[AGENTS]), len(new_agents)),
        card_sha=card_sha,
    )


# --------------------------------------------------------------------------- proofs


def _reason_issues(cc: distill.CardCheck, lang: str) -> list[Issue]:
    return [
        Issue(
            code=r.code,
            key="distill.not_ready",
            unit=cc.unit,
            block=r.block,
            dest=r.path,
            detail=f"{cc.card_id} {distill._reason_text(r, lang)}",
        )
        for r in cc.reasons
    ]


def _same_entry(a: distill.CardCheck, b: distill.CardCheck) -> bool:
    """The card is still ready with the very entry that was shown and confirmed."""
    return (
        b.state == "ready"
        and a.entry is not None
        and b.entry is not None
        and a.entry.lines == b.entry.lines
    )


def _card_check(ctx: _Ctx, read: Callable[[str], bytes | None]) -> distill.CardCheck:
    rep = distill.check_run(ctx.m, ctx.placements, read, card=ctx.card, lexicon=ctx.lexicon)
    return rep.cards[0]


def _prove(ctx: _Ctx, files: Mapping[str, bytes]) -> list[Issue]:
    """§8.2 step 3 (and 8): ``verify`` posterior, ``ratchet check``, ``distill-check``
    (``distilled``), ``AGENTS.md`` balanced and G5 over the files read through ``files``
    (the stage or nothing: the disk)."""
    issues: list[Issue] = []
    read = _overlay_reader(ctx.root, files)
    res = verify_run(ctx.project, ctx.cfg, ctx.reorg_id, rev=None, strict=False, read=read)
    issues += proof.to_issues(res)
    u = _unit_of(ctx.m, ctx.card)
    path = distill.card_path(ctx.m, u)
    if res.ok and (path, "card_distilled") not in res.states:
        issues.append(_issue(EXIT_PROOF, "not_distilled", dest=path))
    rep = ratchet.check_files(ctx.project, ctx.cfg, files)
    for f in rep.failures:
        issues.append(
            Issue(code=f.code, key="distill.ratchet", dest=f.path, detail=f"{f.rule} {f.detail}")
        )
    try:
        cc = _card_check(ctx, read)
    except ReorgError as exc:
        issues += exc.issues
    else:
        if cc.state != "distilled":
            issues += _reason_issues(cc, ctx.lang) or [
                _issue(EXIT_PROOF, "not_distilled", dest=path)
            ]
    agents = read(AGENTS) or b""
    try:
        texts = [mdblocks.decode_line(x) for x in mdblocks.split_lines(agents).lines]
        mdblocks.scan(texts, strict=True)
    except (UnicodeDecodeError, mdblocks.SourceError):
        issues.append(_issue(EXIT_PROOF, "agents_unbalanced", dest=AGENTS))
    for rel, data in sorted(files.items()):
        for hit in scan_secrets(rel, data):
            issues.append(
                Issue(
                    code=EXIT_SECRET,
                    key="secret",
                    dest=rel,
                    at=(hit.line, hit.line),
                    detail=hit.category,
                )
            )
    return issues


# --------------------------------------------------------------------------- preconditions


def _lf_issues(root: Path, outputs: Sequence[str]) -> list[Issue]:
    findings = gitops.normalizing(root, list(outputs))

    def cr_free(path: str) -> bool:
        try:
            data = _read(root, path)
        except ReorgError:
            return False
        return data is None or b"\r" not in data

    refused, _ok = gitops.split_lf_safe(findings, list(outputs), cr_free)
    return [
        Issue(code=EXIT_USAGE, key="git_normalizes", dest=path, detail=f"{attr}={value}")
        for path, attr, value in refused
    ]


def _git_preconditions(ctx: _Ctx, outputs: Sequence[str], card_path: str) -> list[Issue]:
    root = ctx.root
    issues: list[Issue] = []
    if not gitops.is_repo(root):
        if not ctx.opts.no_git:
            issues.append(Issue(code=EXIT_USAGE, key="not_repo"))
        return issues
    if ctx.opts.no_git:
        issues.append(Issue(code=EXIT_USAGE, key="no_git_in_repo"))
        return issues
    ctx.use_git = True
    try:
        if gitops.head_branch(root) is None:
            issues.append(Issue(code=EXIT_USAGE, key="detached_head"))
        op = gitops.operation_in_progress(root)
        if op is not None:
            issues.append(Issue(code=EXIT_USAGE, key="operation_in_progress", detail=op))
        others = sorted(p for p in outputs if p != card_path)
        for path in gitops.dirty(root, others):
            issues.append(Issue(code=EXIT_USAGE, key="dirty", dest=path))
        refs = [
            (f"refs/heads/{ctx.branch}", f"git branch -D {ctx.branch}"),
            (f"refs/tags/{ctx.tag}", f"git tag -d {ctx.tag}"),
        ]
        for ref, cmd in refs:
            if gitops.ref_exists(root, ref):
                issues.append(Issue(code=EXIT_USAGE, key="ref_exists", detail=f"{ref}: {cmd}"))
        issues += _lf_issues(root, outputs)
        ctx.status_before = gitops.status_paths(root)
    except gitops.GitError as exc:
        issues.append(Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc)))
    return issues


def _run_ready(ctx: _Ctx) -> list[Issue]:
    """§8.1.1: the run journal is ``committed`` with its commit an ancestor of HEAD (git)
    or ``written`` (``--no-git``)."""
    j = read_journal(ctx.run)
    state = "planned" if j is None else j.state
    if ctx.use_git:
        if j is None or j.state != "committed" or j.commit is None:
            return [_issue(EXIT_USAGE, "run_not_committed", detail=state)]
        try:
            if not gitops.is_ancestor(ctx.root, j.commit, "HEAD"):
                return [_issue(EXIT_USAGE, "run_not_ancestor", detail=j.commit[:12])]
        except gitops.GitError as exc:
            return [Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))]
        return []
    if j is None or j.state not in ("written", "committed"):
        return [_issue(EXIT_USAGE, "run_not_committed", detail=state)]
    return []


def _others_interrupted(ctx: _Ctx) -> list[Issue]:
    return [
        _issue(EXIT_USAGE, "interrupted_other", detail=f"{r.card} {r.state}")
        for r, _f, _j in _distills(ctx.root, ctx.reorg_id)
        if r.card != ctx.card and r.state in INTERRUPTED
    ]


def _verify_now(ctx: _Ctx) -> list[Issue]:
    """§8.1.5: ``verify <id>`` (posterior, not strict) and ``ratchet check`` give 0."""
    issues: list[Issue] = []
    res = verify_run(ctx.project, ctx.cfg, ctx.reorg_id, rev=None, strict=False)
    issues += proof.to_issues(res)
    rep = ratchet.check(ctx.project, ctx.cfg, staged=False)
    for f in rep.failures:
        issues.append(
            Issue(code=f.code, key="distill.ratchet", dest=f.path, detail=f"{f.rule} {f.detail}")
        )
    return issues


# --------------------------------------------------------------------------- confirmation


def _summary(ctx: _Ctx, b: _Built) -> list[str]:
    lang = ctx.lang
    cc = b.check
    u = _unit_of(ctx.m, ctx.card)
    out = [_m("head", lang, card=ctx.card, unit=cc.unit, n=len(cc.copies))]
    for c in cc.copies:
        out.append(
            _m(
                "copy",
                lang,
                block=c.block,
                pin=c.pin or "-",
                src=f"{c.source}:{c.src[0]}-{c.src[1]}",
                lines=c.lines,
                bytes=c.bytes if c.bytes is not None else "?",
                cats=", ".join(c.categories),
            )
        )
    inv, card = b.pins_new
    out.append(
        _m("pins", lang, inv=inv.id, inv_lines=inv.lines, card=card.id, card_lines=card.lines)
    )
    out.append(_m("files", lang, files=", ".join(sorted(b.files))))
    out.append(
        _m(
            "agents",
            lang,
            b0=b.agents_before[0],
            l0=b.agents_before[1],
            b1=b.agents_after[0],
            l1=b.agents_after[1],
        )
    )
    if b.tightened:
        out.append(
            _m(
                "tighten",
                lang,
                items=", ".join(f"{k} {old} → {new}" for k, old, new in b.tightened),
            )
        )
    else:
        out.append(_m("tighten_none", lang))
    out.append(_m("literal", lang, history=f"{u.file}#{anchor(ctx.m.id, u.id)}", card=cc.path))
    return out


def _confirm(ctx: _Ctx, b: _Built) -> tuple[str | None, list[Issue]]:
    """§8.3: in a TTY, the motive (not empty) and the word; ``--yes`` never skips it."""
    if not ctx.prompter.is_tty():
        return None, [_issue(EXIT_USAGE, "no_tty")]
    for line in _summary(ctx, b):
        ctx.prompter.say(line)
    reason = ctx.prompter.ask(_m("ask_reason", ctx.lang)).strip()
    if not reason:
        return None, [_issue(EXIT_USAGE, "no_reason")]
    word = ctx.prompter.ask(_m("ask_word", ctx.lang)).strip().casefold()
    if word not in _CONFIRM_WORDS:
        return None, [_issue(EXIT_USAGE, "not_confirmed")]
    return reason, []


# --------------------------------------------------------------------------- transaction


def _stage(ctx: _Ctx, b: _Built, stage: Path) -> Journal:
    _rmtree(stage)
    for rel, data in sorted(b.files.items()):
        fsutil.atomic_write_bytes(stage / rel, data, ctx.root)
    entries = tuple(
        JournalEntry(rel, "rewritten", _sha(b.prev[rel]), _sha(b.files[rel]), "pending")
        for rel in _order(ctx, b.files)
    )
    return Journal(state="staged", base_branch=None, entries=entries)


def _order(ctx: _Ctx, files: Mapping[str, bytes]) -> list[str]:
    u = _unit_of(ctx.m, ctx.card)
    return [p for p in _outputs(ctx.m, u, ctx.card) if p in files]


def _stage_reader_files(ctx: _Ctx, stage: Path, files: Iterable[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for rel in files:
        data = fsutil.safe_read_bytes(stage / rel, ctx.root)
        out[rel] = data
    return out


def _backup(ctx: _Ctx, j: Journal) -> None:
    for e in j.entries:
        data = _read(ctx.root, e.path)
        if data is None or _sha(data) != e.prev_sha256:
            raise _Abort([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)])
        dest = ctx.folder / "backup" / e.path
        fsutil.atomic_write_bytes(dest, data, ctx.root)
        copy = _read(ctx.root, dest.relative_to(ctx.root).as_posix())
        if copy is None or _sha(copy) != e.prev_sha256:
            raise _Abort([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)])


def _set_entry(j: Journal, path: str, status: str) -> Journal:
    return replace(
        j, entries=tuple(replace(e, status=status) if e.path == path else e for e in j.entries)
    )


def _write_all(ctx: _Ctx, b: _Built, j: Journal) -> Journal:
    """§8.2 step 7: compare-and-swap, write-ahead journal, atomic write; AGENTS.md last."""
    j = replace(j, state="writing")
    ctx.save(j)
    for e in j.entries:
        if _sha_now(ctx.root, e.path) != e.prev_sha256:
            raise _Abort([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)])
        j = _set_entry(j, e.path, "writing")
        ctx.save(j)
        fsutil.atomic_write_bytes(ctx.root / e.path, b.files[e.path], ctx.root)
        j = _set_entry(j, e.path, "done")
        ctx.save(j)
    return j


def _scope(ctx: _Ctx, outputs: Sequence[str]) -> list[Issue]:
    """§8.2 step 9: no new working-tree change outside the declared outputs."""
    if not ctx.use_git:
        return []
    new = gitops.status_paths(ctx.root) - ctx.status_before
    return [_issue(EXIT_PROOF, "outside_outputs", dest=p) for p in sorted(new - set(outputs))]


def _rollback(ctx: _Ctx, issues: list[Issue]) -> list[Issue]:
    j = ctx.journal
    if j is None:
        return issues
    untouched = restore(ctx.root, ctx.folder, j)
    issues += [Issue(code=EXIT_PROOF, key="restore_untouched", dest=p) for p in untouched]
    switched = True
    # In a batch the branch belongs to the batch: stay on it (fase 3A §5.3).
    if ctx.use_git and j.base_branch is not None and ctx.batch is None:
        try:
            if gitops.head_branch(ctx.root) != j.base_branch:
                gitops.switch(ctx.root, j.base_branch)
        except gitops.GitError as exc:
            switched = False
            issues.append(Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc)))
    _rmtree(ctx.folder / "stage")
    if not untouched and switched:
        with contextlib.suppress(Exception):
            ctx.save(replace(j, state="undone"))
    # The tag and the branch are kept (never deleted by declutter): say how to remove them,
    # since a new distill-apply of this card refuses while they exist.
    if ctx.batch is None:
        with contextlib.suppress(gitops.GitError):
            _say_refs(ctx)
    return issues


def _next_seq(ctx: _Ctx) -> int:
    return max((r.seq for r, _f, _j in _distills(ctx.root, ctx.reorg_id)), default=0) + 1


def _delta(removed: int) -> str:
    """Signed byte change of AGENTS.md: ``\u2212N`` when it shrinks (or stays), ``+N`` when it
    grows, so a negative count never renders as a double minus."""
    return f"\u2212{removed}" if removed >= 0 else f"+{-removed}"


def _subject(ctx: _Ctx, b: _Built) -> str:
    n = len(b.check.copies)
    gone = b.agents_before[0] - b.agents_after[0]
    return _m("subject", ctx.lang, id=ctx.reorg_id, card=ctx.card, n=n, delta=_delta(gone))


def _commit(ctx: _Ctx, j: Journal, subject: str, card_sha: str) -> ApplyOutcome:
    """§8.2 step 10: ``git commit --only`` of the outputs, then ``verify --rev HEAD``."""
    paths = [e.path for e in j.entries]
    body = (
        f"Declutter-Distill: {ctx.reorg_id}/{ctx.card} card-sha256={card_sha}\n"
        f"Declutter-Reorg: {ctx.reorg_id}"
    )
    if ctx.batch is not None:
        body += f"\nDeclutter-Distill-Batch: {ctx.reorg_id}/{ctx.batch.batch}"
    try:
        sha = gitops.commit_only(ctx.root, subject, body, paths)
    except gitops.GitError as exc:
        return _fail(
            EXIT_USAGE,
            "commit_rejected",
            j,
            detail=f"{exc}; {ctx.undo_cmd()}",
        )
    j = replace(j, state="committed", commit=sha)
    ctx.save(j)
    try:
        res = verify_run(ctx.project, ctx.cfg, ctx.reorg_id, rev="HEAD", strict=False)
    except ReorgError as exc:
        return _outcome(exc.issues, j)
    if not res.ok:
        hint = _issue(
            EXIT_PROOF,
            "verify_rev_failed",
            detail=ctx.undo_cmd(),
        )
        return _outcome([*proof.to_issues(res), hint], j)
    return _outcome([], j)


def _wants_commit(ctx: _Ctx) -> bool:
    if not ctx.use_git:
        return False
    if ctx.opts.commit:
        return True
    return ctx.prompter.is_tty() and ctx.yes_no(_m("commit_q", ctx.lang, branch=ctx.branch))


def _card_sha_of(ctx: _Ctx) -> str:
    rec = _read_record(ctx.root, ctx.folder)
    sha = rec.get("card_sha256")
    return sha if isinstance(sha, str) else ""


def _resume(ctx: _Ctx, j: Journal) -> ApplyOutcome:
    """``--commit`` over a ``written`` journal: prove the disk again, then commit."""
    if not ctx.prompter.is_tty() and not ctx.opts.yes:
        return _fail(EXIT_USAGE, "needs_yes", j)
    if not gitops.is_repo(ctx.root) or j.base_branch is None:
        return _outcome([Issue(code=EXIT_USAGE, key="not_repo")], j)
    ctx.use_git = True
    if gitops.head_branch(ctx.root) != ctx.branch:
        return _outcome([Issue(code=EXIT_USAGE, key="wrong_branch", detail=ctx.branch)], j)
    for e in j.entries:
        if _sha_now(ctx.root, e.path) != e.expected_sha256:
            return _outcome([Issue(code=EXIT_PROOF, key="concurrent_edit", dest=e.path)], j)
    issues = _prove(ctx, {})
    if issues:
        return _outcome(issues, j)
    rec = _read_record(ctx.root, ctx.folder)
    subject = rec.get("subject")
    if not isinstance(subject, str) or not subject:
        subject = f"declutter(distill {ctx.reorg_id} {ctx.card})"
    if not ctx.opts.yes and not ctx.yes_no(_m("commit_q", ctx.lang, branch=ctx.branch)):
        return _outcome([], j)
    return _commit(ctx, j, subject, _card_sha_of(ctx))


def _reconcile(ctx: _Ctx, j: Journal) -> Journal:
    """A ``written`` journal whose commit exists (interrupted after ``git commit``)."""
    if j.state != "written" or j.commit is not None:
        return j
    try:
        if not gitops.is_repo(ctx.root) or gitops.head_branch(ctx.root) != ctx.branch:
            return j
        tagged = gitops.rev_commit(ctx.root, f"refs/tags/{ctx.tag}")
        head = gitops.rev_commit(ctx.root, "HEAD")
        if tagged is None or head is None or head == tagged:
            return j
        if not gitops.is_ancestor(ctx.root, tagged, head):
            return j
    except (gitops.GitError, ValueError):
        return j
    return replace(j, state="committed", commit=head)


def _make_ctx(
    project: Project,
    cfg: Config,
    reorg_id: str,
    card: str,
    opts: DistillOptions,
    prompter: Prompter,
    now: datetime,
    lang: str,
) -> _Ctx:
    root = project.root
    m, run = mf.load_run(root, reorg_id)
    raw = _read(root, (run / PLACEMENT_NAME).relative_to(root).as_posix())
    if raw is None:
        raise ReorgError([Issue(code=EXIT_USAGE, key="run_missing", detail=PLACEMENT_NAME)])
    try:
        placements = mf.placements_loads(raw)
    except mf.ManifestError as exc:
        raise ReorgError([exc.issue]) from None
    _unit_of(m, card)
    folder = distill_dir(root, reorg_id, card)
    return _Ctx(
        project=project,
        cfg=cfg,
        reorg_id=reorg_id,
        card=card,
        opts=opts,
        prompter=prompter,
        now=now,
        lang=lang,
        root=root,
        run=run,
        folder=folder,
        m=m,
        placements=placements,
        lexicon=build_lexicon(cfg.reorg.extra_lexicon),
    )


def apply(
    project: Project,
    cfg: Config,
    reorg_id: str,
    card: str,
    opts: DistillOptions,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
) -> ApplyOutcome:
    """``declutter reorg distill-apply`` (fase 2b §8). Never merges or pushes."""
    try:
        ctx = _make_ctx(project, cfg, reorg_id, card, opts, prompter, now, lang)
        j0 = read_journal(ctx.folder, ctx.root)
    except ReorgError as exc:
        return _outcome(exc.issues, None)
    ctx.journal = j0
    try:
        if j0 is not None and j0.state in INTERRUPTED:
            return _fail(EXIT_USAGE, "interrupted", j0, detail=j0.state)
        if j0 is not None and j0.state == "written":
            j1 = _reconcile(ctx, j0)
            if j1 is not j0:
                ctx.save(j1)
                j0 = j1
        if j0 is not None and j0.state == "written":
            if opts.commit and not opts.dry_run:
                return _resume(ctx, j0)
            return _fail(EXIT_USAGE, "already_applied", j0, detail=j0.state)
        return _transaction(ctx, j0)
    except ReorgError as exc:
        return _outcome(exc.issues, ctx.journal)
    except KeyboardInterrupt:
        j = ctx.journal
        if j is not None and j.state in ("written", "committed"):
            hint = Issue(
                code=EXIT_INTERRUPTED,
                key="interrupted",
                detail=f"declutter reorg distill-undo {reorg_id} --card {card}",
            )
            return _outcome([hint], j)
        if j is not None and j.state in INTERRUPTED and j is not j0:
            return _outcome(_rollback(ctx, [Issue(code=EXIT_INTERRUPTED, key="interrupted")]), j)
        return _outcome([Issue(code=EXIT_INTERRUPTED, key="interrupted")], j)


def _transaction(ctx: _Ctx, j0: Journal | None) -> ApplyOutcome:
    opts, lang = ctx.opts, ctx.lang
    u = _unit_of(ctx.m, ctx.card)
    path = distill.card_path(ctx.m, u)
    outputs = _outputs(ctx.m, u, ctx.card)

    # §8.5: an already distilled card is idempotent (nothing is written).
    cc0 = _card_check(ctx, distill.disk_reader(ctx.root))
    if cc0.state == "distilled":
        ctx.prompter.say(_m("already", lang, card=ctx.card))
        return _outcome([], j0)

    issues: list[Issue] = []
    if not opts.dry_run and not ctx.prompter.is_tty():
        issues.append(_issue(EXIT_USAGE, "no_tty"))
    issues += _git_preconditions(ctx, outputs, path)
    issues += _run_ready(ctx)
    issues += _others_interrupted(ctx)
    if cc0.state != "ready":
        issues += _reason_issues(cc0, lang) or [_issue(EXIT_PROOF, "not_ready", dest=path)]
    issues += _verify_now(ctx)
    if issues:
        return _outcome(issues, j0)

    # 2-3 in memory: build and prove; nothing is written yet.
    try:
        built = _build(ctx, cc0, reason="")
        found = _prove(ctx, built.files)
    except _Abort as exc:
        return _outcome(exc.issues, j0)
    if found:
        return _outcome(found, j0)
    if opts.dry_run:
        dry = ctx.folder / "dry-stage"
        try:
            for rel, data in sorted(built.files.items()):
                fsutil.atomic_write_bytes(dry / rel, data, ctx.root)
            for line in _summary(ctx, built):
                ctx.prompter.say(line)
        finally:
            _rmtree(dry)
        return _outcome([], j0)

    # 4: confirmation, then the real build with the motive.
    reason, refused = _confirm(ctx, built)
    if refused or reason is None:
        return _outcome(refused, j0)
    # The card may have changed while the human typed: check it again (fail closed).
    cc1 = _card_check(ctx, distill.disk_reader(ctx.root))
    if not _same_entry(cc0, cc1):
        return _outcome([_issue(EXIT_PROOF, "card_changed", dest=path)], j0)
    try:
        built = _build(ctx, cc0, reason=reason)
        found = _prove(ctx, built.files)
    except _Abort as exc:
        return _outcome(exc.issues, j0)
    if found:
        return _outcome(found, j0)

    # 2 (on disk): stage, journal, record; 3: prove the stage.
    # The record (with the order ``seq``) is written before the journal, so an
    # interruption never leaves a journal without its place in the LIFO order.
    seq = _next_seq(ctx)
    _rmtree(ctx.folder)
    _write_record(
        ctx.root,
        ctx.folder,
        {
            "seq": seq,
            "reorg": ctx.reorg_id,
            "card": ctx.card,
            "date": ctx.now.date().isoformat(),
            "card_sha256": built.card_sha,
            "subject": _subject(ctx, built),
        },
    )
    stage = ctx.folder / "stage"
    j = _stage(ctx, built, stage)
    ctx.save(j)
    found = _prove(ctx, _stage_reader_files(ctx, stage, built.files))
    if found:
        _rmtree(stage)
        j = replace(j, state="undone")
        ctx.save(j)
        return _outcome(found, j)
    return _write_phase(ctx, built, j, outputs)


def _write_phase(ctx: _Ctx, b: _Built, j: Journal, outputs: Sequence[str]) -> ApplyOutcome:
    try:
        # In a batch the tag and the branch already exist (fase 3A §5.3 step 3).
        if ctx.use_git and ctx.batch is None:
            j = replace(j, base_branch=gitops.head_branch(ctx.root))
            ctx.save(j)
            gitops.tag(ctx.root, ctx.tag)
            gitops.switch_create(ctx.root, ctx.branch)
        _brake(ctx)  # fase 3A §6.1 point 2: between the proof of stage/ and the backup
        j = replace(j, state="tagged")
        ctx.save(j)
        _backup(ctx, j)
        j = replace(j, state="backed_up")
        ctx.save(j)
        _brake(ctx)  # fase 3A §6.1 point 3: before the write (never checked after it)
        j = _write_all(ctx, b, j)
        found = _prove(ctx, {}) + _scope(ctx, outputs)
        if found:
            raise _Abort(found)
        _rmtree(ctx.folder / "stage")
        j = replace(j, state="written")
        ctx.save(j)
        rec = _read_record(ctx.root, ctx.folder)
        events = ratchet.log_events(b.files[ratchet.LOG_REL])
        rec["event"] = next(
            (
                dict(e)
                for e in reversed(events)
                if e.get("event") == "distill" and e.get("card") == ctx.card
            ),
            None,
        )
        _write_record(ctx.root, ctx.folder, rec)
    except KeyboardInterrupt:
        raise
    except _Stopped:
        stopped = Issue(code=EXIT_INTERRUPTED, key="distill.auto.stopped_card")
        return _outcome(_rollback(ctx, [stopped]), ctx.journal)
    except _Abort as exc:
        return _outcome(_rollback(ctx, list(exc.issues)), ctx.journal)
    except Exception as exc:
        issue = Issue(code=EXIT_PROOF, key="apply_aborted", detail=type(exc).__name__)
        if isinstance(exc, gitops.GitError):
            issue = replace(issue, detail=str(exc))
        return _outcome(_rollback(ctx, [issue]), ctx.journal)
    if ctx.batch is None:
        ctx.prompter.say(
            _m(
                "written",
                ctx.lang,
                card=ctx.card,
                delta=_delta(b.agents_before[0] - b.agents_after[0]),
            )
        )
    if _wants_commit(ctx):
        return _commit(ctx, j, _subject(ctx, b), b.card_sha)
    return _outcome([], j)


# --------------------------------------------------------------------------- batch (fase 3A)


def card_file(root: Path, reorg_id: str, card: str) -> str:
    """The card path of ``card`` in run ``reorg_id`` (relative to the root)."""
    m, _run = mf.load_run(root, reorg_id)
    return distill.card_path(m, _unit_of(m, card))


def batch_preflight(
    project: Project,
    cfg: Config,
    reorg_id: str,
    cards: Sequence[str],
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
) -> list[Issue]:
    """fase 3A §5.2 points 1-5 for every card of the batch. Nothing is written."""
    if not cards:
        return []
    root = project.root
    opts = DistillOptions(commit=True)
    try:
        contexts = [_make_ctx(project, cfg, reorg_id, c, opts, prompter, now, lang) for c in cards]
    except ReorgError as exc:
        return list(exc.issues)
    if not gitops.is_repo(root):
        return [Issue(code=EXIT_USAGE, key="not_repo")]
    ctx = contexts[0]
    ctx.use_git = True
    issues: list[Issue] = []
    try:
        if gitops.head_branch(root) is None:
            issues.append(Issue(code=EXIT_USAGE, key="detached_head"))
        op = gitops.operation_in_progress(root)
        if op is not None:
            issues.append(Issue(code=EXIT_USAGE, key="operation_in_progress", detail=op))
        card_paths = {distill.card_path(c.m, _unit_of(c.m, c.card)) for c in contexts}
        outputs = sorted(
            {p for c in contexts for p in _outputs(c.m, _unit_of(c.m, c.card), c.card)}
        )
        for path in gitops.dirty(root, [p for p in outputs if p not in card_paths]):
            issues.append(Issue(code=EXIT_USAGE, key="dirty", dest=path))
        issues += _lf_issues(root, outputs)
    except gitops.GitError as exc:
        issues.append(Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc)))
    issues += _run_ready(ctx)
    issues += [
        _issue(EXIT_USAGE, "interrupted_other", detail=f"{r.card} {r.state}")
        for r, _f, _j in _distills(root, reorg_id)
        if r.state in INTERRUPTED
    ]
    issues += _verify_now(ctx)
    return issues


def _batch_git(ctx: _Ctx, outputs: Sequence[str], card_path: str) -> list[Issue]:
    """2b §8.1 git preconditions for one card of a batch: the same as ``distill-apply``
    except that the branch and the tag already exist (they are the batch's)."""
    root = ctx.root
    try:
        if not gitops.is_repo(root):
            return [Issue(code=EXIT_USAGE, key="not_repo")]
        ctx.use_git = True
        if gitops.head_branch(root) != ctx.branch:
            return [Issue(code=EXIT_USAGE, key="wrong_branch", detail=ctx.branch)]
        issues: list[Issue] = []
        op = gitops.operation_in_progress(root)
        if op is not None:
            issues.append(Issue(code=EXIT_USAGE, key="operation_in_progress", detail=op))
        others = sorted(p for p in outputs if p != card_path)
        for path in gitops.dirty(root, others):
            issues.append(Issue(code=EXIT_USAGE, key="dirty", dest=path))
        issues += _lf_issues(root, outputs)
        ctx.status_before = gitops.status_paths(root)
    except gitops.GitError as exc:
        return [Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))]
    return issues


def apply_batch_card(
    project: Project,
    cfg: Config,
    reorg_id: str,
    card: str,
    spec: BatchSpec,
    prompter: Prompter,
    *,
    now: datetime,
    lang: str,
) -> ApplyOutcome:
    """fase 3A §5.3 step 4: 2b §8.2 without confirmation, on the batch branch."""
    try:
        ctx = _make_ctx(
            project, cfg, reorg_id, card, DistillOptions(commit=True), prompter, now, lang
        )
        j0 = read_journal(ctx.folder, ctx.root)
    except ReorgError as exc:
        return _outcome(exc.issues, None)
    ctx.batch = spec
    ctx.journal = j0
    if j0 is not None and (j0.state in INTERRUPTED or j0.state == "written"):
        return _fail(EXIT_USAGE, "interrupted", j0, detail=j0.state)
    try:
        return _batch_transaction(ctx, j0)
    except ReorgError as exc:
        return _outcome(exc.issues, ctx.journal)


def _batch_transaction(ctx: _Ctx, j0: Journal | None) -> ApplyOutcome:
    assert ctx.batch is not None
    u = _unit_of(ctx.m, ctx.card)
    path = distill.card_path(ctx.m, u)
    outputs = _outputs(ctx.m, u, ctx.card)
    # §10: «ready» is computed again here, never taken from the skill's first pass.
    cc0 = _card_check(ctx, distill.disk_reader(ctx.root))
    issues: list[Issue] = []
    if cc0.state != "ready":
        issues += _reason_issues(cc0, ctx.lang) or [_issue(EXIT_PROOF, "not_ready", dest=path)]
    issues += _batch_git(ctx, outputs, path)
    issues += _run_ready(ctx)
    issues += _others_interrupted(ctx)
    issues += _verify_now(ctx)
    if issues:
        return _outcome(issues, j0)
    try:
        built = _build(ctx, cc0, reason=ctx.batch.reason)
        found = _prove(ctx, built.files)
    except _Abort as exc:
        return _outcome(exc.issues, j0)
    if found:
        return _outcome(found, j0)
    seq = _next_seq(ctx)
    _rmtree(ctx.folder)
    _write_record(
        ctx.root,
        ctx.folder,
        {
            "seq": seq,
            "reorg": ctx.reorg_id,
            "card": ctx.card,
            "date": ctx.now.date().isoformat(),
            "card_sha256": built.card_sha,
            "subject": _subject(ctx, built),
            "batch": ctx.batch.batch,
            "branch": ctx.batch.branch,
            "tag": ctx.batch.tag,
        },
    )
    stage = ctx.folder / "stage"
    j = replace(_stage(ctx, built, stage), batch=ctx.batch.batch, base_branch=ctx.batch.base_branch)
    ctx.save(j)
    found = _prove(ctx, _stage_reader_files(ctx, stage, built.files))
    if found:
        _rmtree(stage)
        j = replace(j, state="undone")
        ctx.save(j)
        return _outcome(found, j)
    return _write_phase(ctx, built, j, outputs)


# --------------------------------------------------------------------------- undo (§9)


def _say_refs(ctx: _Ctx) -> None:
    if not gitops.is_repo(ctx.root):
        return
    refs = [
        (f"refs/heads/{ctx.branch}", f"git branch -D {ctx.branch}"),
        (f"refs/tags/{ctx.tag}", f"git tag -d {ctx.tag}"),
    ]
    cmds = [cmd for ref, cmd in refs if gitops.ref_exists(ctx.root, ref)]
    if cmds:
        ctx.prompter.say(_m("cmds", ctx.lang))
        for cmd in cmds:
            ctx.prompter.say(f"  {cmd}")


def _switch_base(ctx: _Ctx, j: Journal) -> list[Issue]:
    base = j.base_branch
    if base is None or not gitops.is_repo(ctx.root):
        return []
    try:
        if gitops.head_branch(ctx.root) != base:
            gitops.switch(ctx.root, base)
    except gitops.GitError as exc:
        return [Issue(code=EXIT_USAGE, key="switch_refused", detail=str(exc))]
    return []


def _not_back(ctx: _Ctx, j: Journal, skip: Iterable[str] = ()) -> list[Issue]:
    skipped = set(skip)
    return [
        _issue(EXIT_PROOF, "not_restored", dest=e.path)
        for e in j.entries
        if e.path not in skipped and _sha_now(ctx.root, e.path) != e.prev_sha256
    ]


def _undone(ctx: _Ctx, j: Journal) -> Journal:
    j = replace(j, state="undone")
    ctx.save(j)
    return j


def _lifo(ctx: _Ctx) -> list[Issue]:
    rows = [r for r, _f, _j in _distills(ctx.root, ctx.reorg_id) if r.state in ACTIVE]
    mine = next((r for r in rows if r.card == ctx.card), None)
    if mine is None:
        return []

    # A missing order (seq 0, e.g. a record lost) counts as the latest: fail closed.
    def order(r: DistillStatus) -> float:
        return r.seq if r.seq > 0 else float("inf")

    later = [
        r
        for r in rows
        if r.card != ctx.card
        and (order(r), r.card) > (order(mine), mine.card)
        and (order(r) > order(mine) or order(mine) == float("inf"))
    ]
    return [_issue(EXIT_USAGE, "lifo", detail=r.card) for r in sorted(later, key=order)]


def _undo_merged(ctx: _Ctx, j: Journal, commit: str, branch: str, card_path: str) -> ApplyOutcome:
    root = ctx.root
    # As 2a undo: a foreign staged change would be swept into the revert commit, and a
    # dirty output would mix with the revert; refuse both (fail closed).
    try:
        staged = gitops.run(root, "diff", "--cached", "--name-only", "-z", "--relative")
        dirty = gitops.dirty(root, sorted(e.path for e in j.entries))
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))], j)
    names = sorted(f.decode("utf-8", "surrogateescape") for f in staged.split(b"\0") if f)
    blocking = [Issue(code=EXIT_USAGE, key="index_not_clean", dest=n) for n in names]
    blocking += [Issue(code=EXIT_USAGE, key="dirty", dest=n) for n in dirty]
    if blocking:
        return _outcome(blocking, j)
    if not ctx.opts.yes:
        if not ctx.prompter.is_tty():
            return _outcome([Issue(code=EXIT_USAGE, key="needs_yes")], j)
        if not ctx.yes_no(_m("revert_q", ctx.lang, sha=commit[:12], branch=branch)):
            return _outcome([Issue(code=EXIT_USAGE, key="declined")], j)
    try:
        gitops.revert_no_commit(root, commit)
    except gitops.GitError:
        with contextlib.suppress(gitops.GitError):
            gitops.revert_abort(root)
        return _outcome([Issue(code=EXIT_USAGE, key="revert_conflict")], j)
    issues = _not_back(ctx, j, skip=[card_path])
    if issues:
        ctx.prompter.say(_m("revert_staged", ctx.lang))
        return _outcome(issues, j)
    if not ctx.opts.yes and not ctx.yes_no(_m("revert_commit_q", ctx.lang, branch=branch)):
        ctx.prompter.say(_m("revert_staged", ctx.lang))
        return _outcome([], j)
    try:
        gitops.commit(
            root,
            _m("revert_subject", ctx.lang, id=ctx.reorg_id, card=ctx.card),
            f"This reverts commit {commit}.",
        )
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_USAGE, key="commit_rejected", detail=str(exc))], j)
    _say_refs(ctx)
    return _outcome([], _undone(ctx, j))


def undo(
    project: Project,
    cfg: Config,
    reorg_id: str,
    card: str,
    prompter: Prompter,
    *,
    yes: bool,
    now: datetime,
    lang: str,
) -> ApplyOutcome:
    """``declutter reorg distill-undo`` (fase 2b §9): the last distillation of the run only
    (LIFO). Never deletes the branch or the tag."""
    try:
        ctx = _make_ctx(project, cfg, reorg_id, card, DistillOptions(yes=yes), prompter, now, lang)
        j = read_journal(ctx.folder, ctx.root)
    except ReorgError as exc:
        return _outcome(exc.issues, None)
    if j is None or j.state == "undone":
        prompter.say(_m("nothing", lang, card=card))
        return _outcome([], j)
    ctx.journal = j
    if j.batch is not None:
        return _outcome(
            [
                _issue(
                    EXIT_USAGE,
                    "batch_card",
                    detail=(
                        f"{card} batch {j.batch}: declutter reorg distill-undo {reorg_id} "
                        f"--auto {j.batch}; declutter reorg distill-auto {reorg_id} --skip {card}"
                    ),
                )
            ],
            j,
        )
    lifo = _lifo(ctx)
    if lifo:
        return _outcome(lifo, j)
    u = _unit_of(ctx.m, card)
    path = distill.card_path(ctx.m, u)
    try:
        done = _reconcile(ctx, j)
        if done is not j:
            ctx.save(done)
            j = done
        if j.state == "staged":
            _rmtree(ctx.folder / "stage")
            issues = _switch_base(ctx, j)
            _say_refs(ctx)
            return _outcome(issues, j) if issues else _outcome([], _undone(ctx, j))
        if j.state in ("tagged", "backed_up"):
            _rmtree(ctx.folder / "stage")
            issues = _switch_base(ctx, j)
            _say_refs(ctx)
            return _outcome(issues, j) if issues else _outcome([], _undone(ctx, j))
        if j.state in ("writing", "written"):
            untouched = restore(ctx.root, ctx.folder, j)
            _rmtree(ctx.folder / "stage")
            issues = _switch_base(ctx, j)
            for p in untouched:
                prompter.say(_m("untouched", lang, path=p))
            issues += _not_back(ctx, j)
            _say_refs(ctx)
            return _outcome(issues, j) if issues else _outcome([], _undone(ctx, j))
        # committed
        commit = j.commit
        if commit is None or not gitops.is_repo(ctx.root):
            return _outcome([Issue(code=EXIT_INTERNAL, key="journal_invalid", detail="commit")], j)
        head = gitops.head_branch(ctx.root)
        merged = head != ctx.branch and gitops.is_ancestor(ctx.root, commit, "HEAD")
        if merged:
            return _undo_merged(ctx, j, commit, head or "HEAD", path)
        issues = _switch_base(ctx, j)
        _say_refs(ctx)
        if issues:
            return _outcome(issues, j)
        # The filled card stays on the distillation branch (§9): it is not compared.
        issues = _not_back(ctx, j, skip=[path])
        if issues:
            return _outcome(issues, j)
        prompter.say(_m("card_on_branch", lang, card=card, branch=ctx.branch))
        return _outcome([], _undone(ctx, j))
    except ReorgError as exc:
        return _outcome(exc.issues, j)
    except gitops.GitError as exc:
        return _outcome([Issue(code=EXIT_INTERNAL, key="git_error", detail=str(exc))], j)
