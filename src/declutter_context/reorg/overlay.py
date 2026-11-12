"""The distillation overlay over a run's placements (fase 2b spec §7.3, §10.1).

``placement.json`` is the immutable record of a run (2a). A distillation does not rewrite
it: every ``distill`` event of ``ratchet.log`` lists the placements it removed or changed
(``placements[]``), the new spans it generated (``generated[]``) and the line delta of
each file (``files[]``). :func:`build` replays those events, in file order, over the run's
placements and gives the **expected** sha and length of each placement, so that
``verify``, ``distill-check`` and ``distill-apply`` agree on what the files should hold.

Fail closed: an event is applied only when **all** of it is what a distillation of its
card may do. It removes exactly the ``AGENTS.md`` copies (with their generated header and
blank line) of its unit whose ``copy`` pins it legitimately releases
(:func:`ratchet.distill_releases`); it changes only the card placement of its unit, the
decisions index and the pointers of that unit (same length, save the card); its
``generated[]`` spans are the region's blank, title+marker and closing marker and the
entry's blank line; and every ``files[].lines_delta`` is the one its own changes imply.
Any other event is not applied and is reported in :attr:`Overlay.invalid` (callers give
code 4). A ``placements[]`` entry names its placement by ``dest`` and the run's ``at``
and must carry the expected sha and length at that moment. Pure: no I/O, metadata only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .. import ratchet
from ..model import Pin, Placement
from . import manifest as mf
from . import mdblocks
from .build import section_titles, slug

__all__ = [
    "GeneratedSpan",
    "Overlay",
    "build",
    "card_path",
    "distill_events",
    "region_lines",
]

AGENTS = "AGENTS.md"
GEN_END_DISTILLED = b"<!-- declutter:gen-end invariants-distilled -->"


@dataclass(frozen=True)
class GeneratedSpan:
    dest: str
    kind: str
    sha256: str
    lines: int


@dataclass(frozen=True)
class Overlay:
    # Per placement index: (sha256, lines) expected now, or None when removed.
    expected: tuple[tuple[str, int] | None, ...]
    changed: frozenset[int]
    removed: frozenset[int]
    generated: tuple[GeneratedSpan, ...]
    deltas: Mapping[str, int] = field(default_factory=dict)
    events: tuple[Mapping[str, object], ...] = ()  # the applied (valid) events
    invalid: tuple[str, ...] = ()  # ``distill`` ids (or "?") of the rejected events


def card_path(m: mf.Manifest, u: mf.UnitEntry) -> str:
    """The card of ``u`` as ``build`` names it (``paths_lang`` folder)."""
    pl = "en" if m.paths_lang == "en" else "es"
    s = slug(u.heading)
    name = f"{u.card_id}-{s}.md" if s else f"{u.card_id}.md"
    return f"{mf.PATHS['decisions_dir'][pl]}/{name}"


def region_lines(m: mf.Manifest) -> tuple[tuple[bytes, bytes], bytes]:
    """((title, marker), closing marker) of the distilled region (§6.1)."""
    title = f"## {section_titles(m.lang)['invariants_distilled']}".encode()
    marker = f"<!-- declutter:gen invariants-distilled {m.id} -->".encode()
    return (title, marker), GEN_END_DISTILLED


def distill_events(
    reorg_id: str, events: Sequence[Mapping[str, object]]
) -> tuple[Mapping[str, object], ...]:
    """The ``distill`` events of ``reorg_id`` after the last ``baseline`` of that run, in
    file order (none when that baseline is not in the log)."""
    marks = [
        i
        for i, e in enumerate(events)
        if e.get("event") == "baseline" and e.get("reorg") == reorg_id
    ]
    if not marks:
        return ()
    return tuple(
        e
        for e in events[marks[-1] + 1 :]
        if e.get("event") == "distill" and e.get("reorg") == reorg_id
    )


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _at(v: object) -> tuple[int, int] | None:
    if isinstance(v, list) and len(v) == 2:
        a, b = _int(v[0]), _int(v[1])
        if a is not None and b is not None:
            return (a, b)
    return None


def _items(e: Mapping[str, object], key: str) -> list[Mapping[str, object]] | None:
    raw = e.get(key)
    if not isinstance(raw, list) or not all(isinstance(x, Mapping) for x in raw):
        return None
    return list(raw)


class _Reject(Exception):
    pass


def _apply_one(
    m: mf.Manifest,
    placements: Sequence[Placement],
    pins: Sequence[Pin],
    e: Mapping[str, object],
    expected: list[tuple[str, int] | None],
    region_done: bool,
) -> tuple[dict[int, tuple[str, int] | None], list[GeneratedSpan], dict[str, int], bool]:
    """Validate one event; return its placement updates, spans and deltas (or raise)."""
    unit = next(
        (
            u
            for u in m.units
            if u.id == e.get("unit") and u.card_id == e.get("card") and u.dest == "L4"
        ),
        None,
    )
    if unit is None or e.get("distill") != f"{m.id}/{unit.card_id}":
        raise _Reject
    # Removable: the AGENTS.md copies of the unit whose pin this event releases, with
    # their generated header (line before) and blank line (line after).
    released = [
        p
        for p in pins
        if p.id in ratchet.distill_old_pins(e) and ratchet.distill_releases(e, p, pins)
    ]
    if not released or len(released) != len(ratchet.distill_old_pins(e)):
        raise _Reject
    removable: set[int] = set()
    for pin in released:
        hits = [
            i
            for i, p in enumerate(placements)
            if p.role == "copy"
            and p.dest == AGENTS
            and p.unit == unit.id
            and p.block == pin.block
            and p.sha256 == pin.sha256
        ]
        if len(hits) != 1:
            raise _Reject
        cp = placements[hits[0]]
        removable.add(hits[0])
        for line, kind in ((cp.at[0] - 1, "invariants"), (cp.at[1] + 1, "blank")):
            k = next(
                (
                    i
                    for i, p in enumerate(placements)
                    if p.dest == AGENTS
                    and p.role == "generated"
                    and p.kind == kind
                    and p.at == (line, line)
                ),
                None,
            )
            if k is None:
                raise _Reject
            removable.add(k)
    # Changeable: the unit's card (mandatory), the index and the unit's pointers.
    pl = "en" if m.paths_lang == "en" else "es"
    changeable: dict[tuple[str, str], bool] = {
        (card_path(m, unit), "card"): True,
        (mf.PATHS["decisions_index"][pl], "index"): False,
    }
    pointer = unit.pointer or ""
    if pointer.startswith("root"):
        changeable[(AGENTS, "dated-rules")] = False
    if "zone:" in pointer:
        changeable[(f"{pointer.split('zone:', 1)[1]}/AGENTS.md", "zone")] = False
    items = _items(e, "placements")
    gen = _items(e, "generated")
    files = _items(e, "files")
    if items is None or gen is None or files is None:
        raise _Reject
    updates: dict[int, tuple[str, int] | None] = {}
    implied: dict[str, int] = {}
    seen_kinds: set[tuple[str, str]] = set()
    for item in items:
        dest, at = item.get("dest"), _at(item.get("at"))
        old, old_n = item.get("old_sha256"), _int(item.get("old_lines"))
        new, new_n = item.get("new_sha256"), _int(item.get("new_lines"))
        hit = next(
            (
                i
                for i, p in enumerate(placements)
                if p.role != "control" and p.dest == dest and p.at == at
            ),
            None,
        )
        if hit is None or hit in updates or expected[hit] is None:
            raise _Reject
        if expected[hit] != (old, old_n) or old_n is None:
            raise _Reject
        p = placements[hit]
        if new is None:
            if hit not in removable or new_n != 0:
                raise _Reject
            updates[hit] = None
            implied[p.dest] = implied.get(p.dest, 0) - old_n
            continue
        key = (p.dest, p.kind or "")
        if p.role != "generated" or key not in changeable or key in seen_kinds:
            raise _Reject
        if not isinstance(new, str) or new_n is None or new_n < 1:
            raise _Reject
        if not changeable[key] and new_n != old_n:  # only the card changes length
            raise _Reject
        seen_kinds.add(key)
        updates[hit] = (new, new_n)
        implied[p.dest] = implied.get(p.dest, 0) + new_n - old_n
    if set(removable) != {i for i, v in updates.items() if v is None}:
        raise _Reject  # every copy of the card, with its header and blank, at once
    if not any(changeable[k] and k in seen_kinds for k in changeable):
        raise _Reject  # the card itself
    # Generated spans: the entry's blank line, and the region once.
    (title, marker), end = region_lines(m)
    blank = (mdblocks.lines_sha([b""]), 1)
    head = (mdblocks.lines_sha([title, marker]), 2)
    tail = (mdblocks.lines_sha([end]), 1)
    shapes = []
    spans: list[GeneratedSpan] = []
    for item in gen:
        gsha, gn, gkind = item.get("sha256"), _int(item.get("lines")), item.get("kind")
        if item.get("dest") != AGENTS or not isinstance(gsha, str) or gn is None:
            raise _Reject
        shape = (gsha, gn)
        if (gkind, shape) not in (
            ("blank", blank),
            ("invariants-distilled", head),
            ("invariants-distilled", tail),
        ):
            raise _Reject
        shapes.append(shape)
        spans.append(GeneratedSpan(AGENTS, str(gkind), gsha, gn))
    creates = head in shapes
    want = [blank, head, tail, blank] if creates else [blank]
    if sorted(shapes) != sorted(want) or (creates and region_done):
        raise _Reject
    new_obj = e.get("new")
    entry_lines = _int(new_obj.get("entry_lines")) if isinstance(new_obj, Mapping) else None
    if entry_lines is None or entry_lines < 2:
        raise _Reject
    implied[AGENTS] = implied.get(AGENTS, 0) + entry_lines + sum(s.lines for s in spans)
    deltas: dict[str, int] = {}
    for item in files:
        path, delta = item.get("path"), _int(item.get("lines_delta"))
        if not isinstance(path, str) or delta is None or path in deltas:
            raise _Reject
        deltas[path] = delta
    if {k: v for k, v in deltas.items() if v} != {k: v for k, v in implied.items() if v}:
        raise _Reject
    return updates, spans, deltas, creates


def build(
    m: mf.Manifest,
    placements: Sequence[Placement],
    events: Sequence[Mapping[str, object]],
    pins: Sequence[Pin],
) -> Overlay:
    """Replay the valid ``distill`` events of run ``m`` over ``placements`` (§10.1)."""
    expected: list[tuple[str, int] | None] = [(p.sha256, p.at[1] - p.at[0] + 1) for p in placements]
    changed: set[int] = set()
    removed: set[int] = set()
    generated: list[GeneratedSpan] = []
    deltas: dict[str, int] = {}
    applied: list[Mapping[str, object]] = []
    invalid: list[str] = []
    region_done = False
    for e in distill_events(m.id, events):
        try:
            updates, spans, ds, creates = _apply_one(m, placements, pins, e, expected, region_done)
        except _Reject:
            invalid.append(str(e.get("distill") or "?"))
            continue
        for i, v in updates.items():
            expected[i] = v
            if v is None:
                removed.add(i)
                changed.discard(i)
            else:
                changed.add(i)
        generated += spans
        for path, d in ds.items():
            deltas[path] = deltas.get(path, 0) + d
        region_done = region_done or creates
        applied.append(e)
    return Overlay(
        expected=tuple(expected),
        changed=frozenset(changed),
        removed=frozenset(removed),
        generated=tuple(generated),
        deltas=deltas,
        events=tuple(applied),
        invalid=tuple(invalid),
    )
