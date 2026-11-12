"""Ratchet against context regrowth (spec §12).

Part 2 (T16): the commands ``check`` (R1-R6, ``--staged``), ``relax``, ``unpin`` and
``repin`` (spec §12.2, §12.3, §8 G8, G9). They read through ``fsutil``/``gitops`` and
report metadata only (paths, ids, line numbers, counts, titles cut to 60), never content.

Part 1 (T07): the pure core. ``pins.json`` (spec §12.1, §8 G8, §7 C9), the baseline in
``ratchet.json`` whose maxima only go down (spec §12.3), the event log ``ratchet.log``,
the contiguous-window search used by ``verify`` and R5 (spec §12.2) and the final
``.declutter/.gitignore`` that lets the control files into the commit (spec §12.1).

The core does no I/O: callers read and write bytes. Validation errors name the field
path, never its value (G6).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import fsutil
from .model import Block, Config, Pin, Placement, Project
from .reorg import gitops, mdblocks, projection
from .reorg.classify import top_level_dirs
from .reorg.mdblocks import heading_key, lines_sha
from .signals_files import _CHANGELOG_RE

if TYPE_CHECKING:
    from .reorg import Prompter
    from .reorg.lexicon import Lexicon

__all__ = [
    "GITIGNORE_FINAL",
    "LOG_EVENTS",
    "LOG_REL",
    "PINS_REL",
    "PINS_SCHEMA",
    "RATCHET_REL",
    "RATCHET_SCHEMA",
    "RatchetFailure",
    "RatchetReport",
    "RatchetSchemaError",
    "RatchetState",
    "baseline",
    "check",
    "check_files",
    "compute_pins",
    "distill_new_pins",
    "distill_old_pins",
    "distill_releases",
    "find_window",
    "gitignore_final",
    "log_events",
    "log_line",
    "measure",
    "pin_released",
    "pins_dumps",
    "pins_loads",
    "ratchet_dumps",
    "ratchet_loads",
    "relax",
    "released",
    "releasing_event",
    "repin",
    "title_key",
    "unpin",
]

RATCHET_SCHEMA = "declutter.ratchet/1"
PINS_SCHEMA = "declutter.pins/1"
RATCHET_REL = ".declutter/ratchet.json"
PINS_REL = ".declutter/pins.json"
LOG_REL = ".declutter/ratchet.log"
GITIGNORE_FINAL = b"*\n!.gitignore\n!ratchet.json\n!pins.json\n!ratchet.log\n"

AGENTS = "AGENTS.md"
LOG_EVENTS = frozenset({"baseline", "relax", "unpin", "repin", "distill", "tighten"})
PIN_ROLES = frozenset({"copy", "l1_block", "unit", "invariant", "card"})
WOULD_FAIL = "ratchet_would_fail"

_PIN_ID_RE = re.compile(r"^P-(\d{3,})$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[0-9a-f]{12}$")
_REORG_RE = re.compile(r"^\d{8}-\d{4}(?:-[1-9]\d*)?$")
_RELEASING = frozenset({"unpin", "repin", "distill"})
# Events that may carry extra top-level keys (fase 2b spec §7.3, §7.5).
_EXTRA_EVENTS = frozenset({"distill", "tighten"})


@dataclass(frozen=True)
class RatchetState:
    s1_max: int
    s3_max_pct: int
    slack_lines: int = 5
    zone_max_lines: int = 150
    forbid_changelog_headings: bool = True
    allowed_dated_headings: tuple[str, ...] = ()
    reorg: str = ""


class RatchetSchemaError(Exception):
    """``ratchet.json``, ``pins.json`` or ``ratchet.log`` is malformed (exit code 2)."""

    def __init__(self, where: str, reason: str) -> None:
        super().__init__(f"{where}: {reason}")
        self.where = where
        self.reason = reason


# --------------------------------------------------------------------------- windows


def title_key(heading: str) -> str:
    """The ``heading_key`` of a heading (no ``~N`` suffix), as in ``allowed_dated_headings``."""
    return heading_key(heading)


def find_window(lines: Sequence[bytes], sha256: str, n: int) -> int | None:
    """1-based start of the first ``n`` contiguous lines whose joined sha is ``sha256``."""
    if n < 1 or n > len(lines):
        return None
    for start in range(len(lines) - n + 1):
        if lines_sha(lines[start : start + n]) == sha256:
            return start + 1
    return None


# --------------------------------------------------------------------------- pins


def _window(lines: Sequence[bytes], at: tuple[int, int], what: str) -> tuple[bytes, ...]:
    a, b = at
    if a < 1 or b < a or b > len(lines):
        raise ValueError(f"{what}: range outside AGENTS.md")
    return tuple(lines[a - 1 : b])


def _pin_number(pin_id: str) -> int:
    m = _PIN_ID_RE.match(pin_id)
    return int(m.group(1)) if m else 0


def compute_pins(
    reorg_id: str,
    agents_lines: Sequence[bytes],
    placements: Sequence[Placement],
    l1_blocks: Sequence[tuple[str, Block, tuple[int, int]]],
    protected: Sequence[tuple[str, tuple[int, int], frozenset[str]]],
    prior: Sequence[Pin],
    *,
    block_categories: Mapping[str, Iterable[str]] | None = None,
) -> tuple[Pin, ...]:
    """``prior`` followed by the new pins of this run (spec §12.1, §8 G8, §9.1).

    One ``copy`` pin per ``copy`` placement in ``AGENTS.md``, one ``l1_block`` pin per
    block with categories of an L1 primary (``at`` is its range in ``AGENTS.md``) and one
    ``unit`` pin per protected unit (``at`` includes the title). A pin with the same file
    and sha as an earlier one (prior or new) is not added. New ids continue from the
    highest prior ``P-NNN``. ``block_categories`` maps a copied block id to its safety
    categories, which a placement does not carry.
    """
    cats_of = block_categories or {}
    seen = {(p.file, p.sha256) for p in prior}
    next_n = max((_pin_number(p.id) for p in prior), default=0) + 1
    new: list[Pin] = []

    def add(
        unit: str, block: str | None, role: str, cats: Iterable[str], at: tuple[int, int]
    ) -> None:
        nonlocal next_n
        window = _window(agents_lines, at, role)
        sha = lines_sha(window)
        if (AGENTS, sha) in seen:
            return
        seen.add((AGENTS, sha))
        new.append(
            Pin(
                id=f"P-{next_n:03d}",
                reorg=reorg_id,
                unit=unit,
                block=block,
                file=AGENTS,
                role=role,
                categories=tuple(sorted(set(cats))),
                lines=len(window),
                sha256=sha,
            )
        )
        next_n += 1

    for p in placements:
        if p.role != "copy" or p.dest != AGENTS:
            continue
        if p.unit is None:
            raise ValueError("copy placement without unit")
        add(p.unit, p.block, "copy", cats_of.get(p.block or "", ()), p.at)
    for unit, block, at in l1_blocks:
        if block.categories:
            add(unit, block.id, "l1_block", block.categories, at)
    for unit, at, cats in protected:
        add(unit, None, "unit", cats, at)
    return (*prior, *new)


# --------------------------------------------------------------------------- baseline


def baseline(
    measured_s1: int,
    measured_s3_pct: float,
    zone_max_lines: int,
    allowed: Sequence[str],
    reorg_id: str,
    prior: RatchetState | None,
    *,
    kept: Iterable[str] = (),
) -> tuple[RatchetState, tuple[str, ...]]:
    """The new ``ratchet.json`` state and its warnings (spec §12.3).

    First time: the measured values, ``S3_max_pct = ceil(measured)``. With ``prior`` every
    maximum is ``min(prior, measured)`` (maxima only go down), ``slack_lines`` and
    ``forbid_changelog_headings`` are kept, and the allowed dated headings are those
    present now that were allowed before or are in ``kept`` (dated L1 units that no
    demotion can move: G2, imports, or kept by the human with a reason; §12.1). Any growth
    (a measured value above its prior maximum, or a dated heading in L1 that is neither
    allowed nor kept) gives ``ratchet_would_fail``.
    """
    s3 = math.ceil(measured_s3_pct)
    now_allowed = tuple(sorted(set(allowed)))
    if prior is None:
        state = RatchetState(
            s1_max=measured_s1,
            s3_max_pct=s3,
            zone_max_lines=zone_max_lines,
            allowed_dated_headings=now_allowed,
            reorg=reorg_id,
        )
        return state, ()
    before = set(prior.allowed_dated_headings) | set(kept)
    grew = measured_s1 > prior.s1_max or s3 > prior.s3_max_pct or not set(now_allowed) <= before
    state = RatchetState(
        s1_max=min(prior.s1_max, measured_s1),
        s3_max_pct=min(prior.s3_max_pct, s3),
        slack_lines=prior.slack_lines,
        zone_max_lines=min(prior.zone_max_lines, zone_max_lines),
        forbid_changelog_headings=prior.forbid_changelog_headings,
        allowed_dated_headings=tuple(k for k in now_allowed if k in before),
        reorg=reorg_id,
    )
    return state, ((WOULD_FAIL,) if grew else ())


# --------------------------------------------------------------------------- strict JSON


def _json_bytes(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _no_dup_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise RatchetSchemaError("$", "duplicate key")
        out[k] = v
    return out


def _parse_json(data: bytes, where: str = "$") -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RatchetSchemaError(where, "not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_no_dup_keys)
    except RatchetSchemaError:
        raise
    except (ValueError, RecursionError):
        raise RatchetSchemaError(where, "not JSON") from None


def _obj(
    v: object, where: str, keys: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise RatchetSchemaError(where, "not an object")
    present = set(v)
    if keys - present:
        raise RatchetSchemaError(where, "missing " + ",".join(sorted(keys - present)))
    if present - keys - optional:
        raise RatchetSchemaError(where, "unknown key")
    return v


def _str(v: object, where: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(v, str):
        raise RatchetSchemaError(where, "not a string")
    if pattern is not None and not pattern.match(v):
        raise RatchetSchemaError(where, "bad format")
    return v


def _int(v: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise RatchetSchemaError(where, "not an integer")
    if v < minimum:
        raise RatchetSchemaError(where, "out of range")
    return v


def _list(v: object, where: str) -> list[Any]:
    if not isinstance(v, list):
        raise RatchetSchemaError(where, "not a list")
    return v


def _reorg(v: object, where: str) -> str:
    s = _str(v, where)
    if s and not _REORG_RE.match(s):
        raise RatchetSchemaError(where, "bad format")
    return s


# --------------------------------------------------------------------------- ratchet.json

_RATCHET_KEYS = frozenset(
    {
        "schema",
        "S1_max",
        "S3_max_pct",
        "slack_lines",
        "zone_max_lines",
        "forbid_changelog_headings",
        "allowed_dated_headings",
        "reorg",
    }
)


def ratchet_dumps(s: RatchetState) -> bytes:
    """Deterministic ``ratchet.json`` with the keys of spec §12.1."""
    return _json_bytes(
        {
            "schema": RATCHET_SCHEMA,
            "S1_max": s.s1_max,
            "S3_max_pct": s.s3_max_pct,
            "slack_lines": s.slack_lines,
            "zone_max_lines": s.zone_max_lines,
            "forbid_changelog_headings": s.forbid_changelog_headings,
            "allowed_dated_headings": list(s.allowed_dated_headings),
            "reorg": s.reorg,
        }
    )


def ratchet_loads(b: bytes) -> RatchetState:
    """Strict reader; any deviation raises ``RatchetSchemaError``."""
    o = _obj(_parse_json(b), "$", _RATCHET_KEYS)
    if o["schema"] != RATCHET_SCHEMA:
        raise RatchetSchemaError("$.schema", "unknown schema")
    forbid = o["forbid_changelog_headings"]
    if not isinstance(forbid, bool):
        raise RatchetSchemaError("$.forbid_changelog_headings", "not a boolean")
    allowed = _list(o["allowed_dated_headings"], "$.allowed_dated_headings")
    return RatchetState(
        s1_max=_int(o["S1_max"], "$.S1_max"),
        s3_max_pct=_int(o["S3_max_pct"], "$.S3_max_pct"),
        slack_lines=_int(o["slack_lines"], "$.slack_lines"),
        zone_max_lines=_int(o["zone_max_lines"], "$.zone_max_lines"),
        forbid_changelog_headings=forbid,
        allowed_dated_headings=tuple(
            _str(k, f"$.allowed_dated_headings[{i}]", pattern=_KEY_RE)
            for i, k in enumerate(allowed)
        ),
        reorg=_reorg(o["reorg"], "$.reorg"),
    )


# --------------------------------------------------------------------------- pins.json

_PIN_KEYS = frozenset(
    {"id", "reorg", "unit", "block", "file", "role", "categories", "lines", "sha256"}
)


def _pin_obj(p: Pin) -> dict[str, object]:
    return {
        "id": p.id,
        "reorg": p.reorg,
        "unit": p.unit,
        "block": p.block,
        "file": p.file,
        "role": p.role,
        "categories": list(p.categories),
        "lines": p.lines,
        "sha256": p.sha256,
    }


def pins_dumps(p: Sequence[Pin]) -> bytes:
    """Deterministic ``pins.json`` (spec §12.1), pins in the given order."""
    return _json_bytes({"schema": PINS_SCHEMA, "pins": [_pin_obj(x) for x in p]})


def _pin_of(v: object, w: str) -> Pin:
    o = _obj(v, w, _PIN_KEYS)
    role = _str(o["role"], f"{w}.role")
    if role not in PIN_ROLES:
        raise RatchetSchemaError(f"{w}.role", "value not allowed")
    block = o["block"]
    file = _str(o["file"], f"{w}.file")
    if not file:
        raise RatchetSchemaError(f"{w}.file", "empty")
    cats = _list(o["categories"], f"{w}.categories")
    return Pin(
        id=_str(o["id"], f"{w}.id", pattern=_PIN_ID_RE),
        reorg=_reorg(o["reorg"], f"{w}.reorg"),
        unit=_str(o["unit"], f"{w}.unit"),
        block=None if block is None else _str(block, f"{w}.block"),
        file=file,
        role=role,
        categories=tuple(_str(c, f"{w}.categories[{i}]") for i, c in enumerate(cats)),
        lines=_int(o["lines"], f"{w}.lines", minimum=1),
        sha256=_str(o["sha256"], f"{w}.sha256", pattern=_SHA_RE),
    )


def pins_loads(b: bytes) -> tuple[Pin, ...]:
    """Strict reader; duplicate ids or any deviation raise ``RatchetSchemaError``."""
    o = _obj(_parse_json(b), "$", frozenset({"schema", "pins"}))
    if o["schema"] != PINS_SCHEMA:
        raise RatchetSchemaError("$.schema", "unknown schema")
    items = _list(o["pins"], "$.pins")
    pins = tuple(_pin_of(x, f"$.pins[{i}]") for i, x in enumerate(items))
    if len({p.id for p in pins}) != len(pins):
        raise RatchetSchemaError("$.pins", "duplicate id")
    return pins


# --------------------------------------------------------------------------- ratchet.log


def log_line(
    event: str,
    *,
    date: str,
    reorg: str,
    old: object,
    new: object,
    reason: str,
    pin: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> bytes:
    """One compact, key-sorted JSON line ending in ``\\n`` (spec §12.1).

    ``extra`` (only for ``distill`` and ``tighten``, fase 2b §7.3/§7.5) adds top-level
    keys; it can neither replace a base key nor be given for another event."""
    if event not in LOG_EVENTS:
        raise ValueError("unknown ratchet event")
    obj: dict[str, object] = {
        "event": event,
        "date": date,
        "reorg": reorg,
        "old": old,
        "new": new,
        "reason": reason,
    }
    if pin is not None:
        obj["pin"] = pin
    if extra:
        if event not in _EXTRA_EVENTS:
            raise ValueError("extra keys only for distill and tighten")
        clash = set(extra) & {*obj, "pin"}
        if clash:
            raise ValueError("extra keys clash with the base keys")
        obj.update(extra)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def log_events(data: bytes) -> tuple[Mapping[str, object], ...]:
    """Events of ``ratchet.log`` in file order; empty lines are skipped."""
    out: list[Mapping[str, object]] = []
    for n, raw in enumerate(data.split(b"\n"), start=1):
        if raw == b"":
            continue
        where = f"line {n}"
        o = _parse_json(raw, where)
        if not isinstance(o, dict):
            raise RatchetSchemaError(where, "not an object")
        if o.get("event") not in LOG_EVENTS:
            raise RatchetSchemaError(where, "unknown event")
        out.append(o)
    return tuple(out)


def distill_old_pins(e: Mapping[str, object]) -> tuple[str, ...]:
    """The ids in ``old.pins[].id`` of a ``distill`` event (fase 2b §7.3); () otherwise."""
    if e.get("event") != "distill":
        return ()
    old = e.get("old")
    items = old.get("pins") if isinstance(old, Mapping) else None
    if not isinstance(items, list):
        return ()
    return tuple(
        str(x["id"]) for x in items if isinstance(x, Mapping) and isinstance(x.get("id"), str)
    )


def distill_new_pins(e: Mapping[str, object]) -> tuple[str, ...] | None:
    """The ids in ``new.pins`` of a ``distill`` event; ``None`` when malformed."""
    new = e.get("new")
    items = new.get("pins") if isinstance(new, Mapping) else None
    if not isinstance(items, list) or not items or not all(isinstance(x, str) for x in items):
        return None
    return tuple(items)


def _names(e: Mapping[str, object], pin_id: str) -> bool:
    if e.get("event") == "distill":
        return pin_id in distill_old_pins(e)
    return e.get("event") in _RELEASING and e.get("pin") == pin_id


def _after_baseline(
    events: Sequence[Mapping[str, object]], reorg: str | None
) -> Sequence[Mapping[str, object]] | None:
    """Events after the last ``baseline`` of ``reorg`` (all without ``reorg``); ``None``
    when that baseline is not in the log."""
    if reorg is None:
        return events
    marks = [
        i for i, e in enumerate(events) if e.get("event") == "baseline" and e.get("reorg") == reorg
    ]
    if not marks:
        return None
    return events[marks[-1] + 1 :]


def released(
    pin_id: str, events: Sequence[Mapping[str, object]], *, reorg: str | None = None
) -> bool:
    """True if an ``unpin`` or ``repin`` of ``pin_id`` is logged after the pin's reorg.

    A ``distill`` never counts here: it releases only through :func:`pin_released`,
    which validates the whole event against the pin and ``pins.json`` (fase 2b §10.2).

    With ``reorg`` (the pin's reorg id), only events after the last ``baseline`` of that
    reorg count, so an id reused by a later run is not released by an older event; if
    that baseline is not in the log, the pin is not released. Without ``reorg`` any
    matching event counts.
    """
    scope = _after_baseline(events, reorg)
    return scope is not None and any(
        e.get("event") in ("unpin", "repin") and e.get("pin") == pin_id for e in scope
    )


def _int_of(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def distill_releases(e: Mapping[str, object], pin: Pin, pins: Sequence[Pin]) -> bool:
    """Whether the ``distill`` event ``e`` legitimately releases ``pin`` (fase 2b §10.2,
    D13). Fail closed: the event is of the pin's reorg and unit; only a ``copy`` pin is
    releasable; ``old.pins`` records the pin's exact sha and lines; ``new.pins`` are two
    existing pins (one ``invariant``, one ``card``) of the same reorg and unit whose shas
    and lines are the event's ``entry``/``card`` ones; old and new ids are disjoint."""
    if e.get("event") != "distill" or pin.role != "copy":
        return False
    if e.get("reorg") != pin.reorg or e.get("unit") != pin.unit:
        return False
    old, new = e.get("old"), e.get("new")
    if not isinstance(old, Mapping) or not isinstance(new, Mapping):
        return False
    items = old.get("pins")
    if not isinstance(items, list):
        return False
    mine = [x for x in items if isinstance(x, Mapping) and x.get("id") == pin.id]
    if len(mine) != 1 or mine[0].get("sha256") != pin.sha256:
        return False
    if _int_of(mine[0].get("lines")) != pin.lines:
        return False
    if mine[0].get("block") is not None and mine[0].get("block") != pin.block:
        return False
    wanted = distill_new_pins(e)
    if wanted is None or len(wanted) != 2 or len(set(wanted)) != 2:
        return False
    if set(wanted) & set(distill_old_pins(e)):
        return False
    by_id = {p.id: p for p in pins}
    got = [by_id.get(x) for x in wanted]
    if any(g is None for g in got):
        return False
    roles = {g.role: g for g in got if g is not None}
    inv, card = roles.get("invariant"), roles.get("card")
    if inv is None or card is None:
        return False
    for g in (inv, card):
        if g.reorg != pin.reorg or g.unit != pin.unit or g.block is not None:
            return False
    return (
        inv.file == AGENTS
        and inv.sha256 == new.get("entry_sha256")
        and inv.lines == _int_of(new.get("entry_lines"))
        and card.sha256 == new.get("card_sha256")
        and card.lines == _int_of(new.get("card_lines"))
    )


# --------------------------------------------------------------------------- .gitignore


def gitignore_final(prev: bytes | None) -> bytes:
    """``.declutter/.gitignore`` that versions the control files (spec §12.1).

    ``None`` gives ``GITIGNORE_FINAL``. An existing file must have a line exactly ``*``
    (spec §6.1); it is kept as a byte prefix and each missing ``!`` line is appended. A
    negation only counts when it comes after the last ``*``, since ``*`` re-ignores what
    precedes it.
    """
    if prev is None:
        return GITIGNORE_FINAL
    lines = prev.split(b"\n")
    stars = [i for i, ln in enumerate(lines) if ln == b"*"]
    if not stars:
        raise ValueError(".declutter/.gitignore has no '*' line")
    after = set(lines[stars[-1] + 1 :])
    wanted = GITIGNORE_FINAL.split(b"\n")[1:-1]
    missing = [ln for ln in wanted if ln not in after]
    if not missing:
        return prev
    head = prev if prev.endswith(b"\n") else prev + b"\n"
    return head + b"".join(ln + b"\n" for ln in missing)


# =========================================================================== part 2 (T16)

CLAUDE = "CLAUDE.md"
HISTORY_DIRS: tuple[str, ...] = ("docs/historial", "docs/history")
LITERAL_FILES: tuple[str, ...] = (
    "docs/trampas.md",
    "docs/pitfalls.md",
    "docs/mapa.md",
    "docs/map.md",
)
TITLE_MAX = 60

# Exit code of each failure kind (spec §10.5): R1-R6 are threshold failures (3), an
# invalid control file or an unsafe read is 2, a missing precondition is 1.
_RULE_CODE: Mapping[str, int] = {
    "R1": 3,
    "R2": 3,
    "R3": 3,
    "R4": 3,
    "R5": 3,
    "R6": 3,
    "schema": 2,
    "read": 2,
    "no_git": 1,
    "no_baseline": 1,
}
_PRECEDENCE = (5, 2, 1, 4, 3)

_SRC_RE = re.compile(
    r"^<!-- declutter:src (?P<source>\S+):(?P<a>\d+)-(?P<b>\d+) sha256=(?P<sha>[0-9a-f]{64}) "
    r"unit=(?P<unit>\S+) plan=(?P<plan>\S+) -->$"
)
_END_RE = re.compile(r"^<!-- declutter:end (?P<unit>\S+) -->$")
_SRC_PREFIX = "<!-- declutter:src "
_END_PREFIX = "<!-- declutter:end "
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,24}$")
_CONFIRM_WORDS = frozenset({"confirmo", "confirm"})


@dataclass(frozen=True)
class RatchetFailure:
    """One failed rule. ``detail`` is metadata only (ids, numbers, short titles)."""

    rule: str  # "R1".."R6", or "schema" | "read" | "no_git" | "no_baseline"
    path: str
    detail: str

    @property
    def code(self) -> int:
        return _RULE_CODE.get(self.rule, 2)


@dataclass(frozen=True)
class RatchetReport:
    code: int
    failures: tuple[RatchetFailure, ...]


def _resolve(failures: Sequence[RatchetFailure]) -> int:
    present = {f.code for f in failures}
    return next((c for c in _PRECEDENCE if c in present), 0)


def _trim(title: str) -> str:
    title = title.strip()
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1] + "…"


def _safe_id(value: str) -> str:
    return value if _SAFE_ID_RE.match(value) else "?"


def releasing_event(
    pin: Pin, events: Sequence[Mapping[str, object]]
) -> Mapping[str, object] | None:
    """The last ``unpin``/``repin``/``distill`` naming ``pin`` after its reorg's baseline."""
    scope = _after_baseline(events, pin.reorg)
    if scope is None:
        return None
    named = [e for e in scope if _names(e, pin.id)]
    return named[-1] if named else None


def pin_released(pin: Pin, events: Sequence[Mapping[str, object]], pins: Sequence[Pin]) -> bool:
    """``released`` (spec §12.2 R5), except that a ``repin`` whose new sha is the pin's
    current sha is what set that sha: the repinned block is enforced again, and a
    ``distill`` releases only under :func:`distill_releases` (fase 2b §10.2, D13).
    Shared by ``ratchet check`` and ``reorg verify`` (G8: both fail under the same
    condition)."""
    last = releasing_event(pin, events)
    if last is None:
        return False
    if last.get("event") == "repin":
        new = last.get("new")
        if isinstance(new, Mapping) and new.get("sha256") == pin.sha256:
            return False
    if last.get("event") == "distill":
        return distill_releases(last, pin, pins)
    return True


# --------------------------------------------------------------------------- reading


class _ReadFailure(Exception):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


class _Source:
    """The working tree (``fsutil.safe_read_bytes``) or the index (``git cat-file``)."""

    def __init__(
        self, root: Path, staged: bool, overlay: Mapping[str, bytes] | None = None
    ) -> None:
        self.root = root
        self.staged = staged
        self.overlay = overlay or {}
        self._index: tuple[str, ...] | None = None

    def read(self, rel: str) -> bytes | None:
        if rel in self.overlay:
            return self.overlay[rel]
        if self.staged:
            try:
                return gitops.show_bytes(self.root, "", rel)
            except gitops.GitError:
                raise _ReadFailure(rel, "git") from None
        try:
            return fsutil.safe_read_bytes(self.root / rel, self.root)
        except fsutil.UnsafeReadError as exc:
            if exc.reason == "missing":
                return None
            raise _ReadFailure(rel, exc.reason) from None

    def index(self) -> tuple[str, ...]:
        if self._index is None:
            try:
                raw = gitops.run(self.root, "ls-files", "-z", "--cached")
            except gitops.GitError:
                raise _ReadFailure(".", "git") from None
            names = raw.split(b"\0")[:-1] if raw else []
            self._index = tuple(n.decode("utf-8", "surrogateescape") for n in names)
        return self._index

    def zones(self) -> tuple[str, ...]:
        """Top-level folders with an ``AGENTS.md`` (spec §9.7)."""
        if self.staged:
            found = {
                p.split("/")[0]
                for p in self.index()
                if p.count("/") == 1 and p.endswith("/AGENTS.md")
            }
            ok = {z for z in found if not z.startswith(".") and z not in fsutil.SKIP_DIRS}
            return tuple(sorted(ok))
        return tuple(
            sorted(z for z in top_level_dirs(self.root) if (self.root / z / AGENTS).is_file())
        )

    def literal_files(self) -> tuple[str, ...]:
        """``docs/historial/**`` (or ``docs/history/**``) Markdown files and the pitfalls
        and map files (spec §12.2 R6)."""
        out: set[str] = set()
        if self.staged:
            for p in self.index():
                if p in LITERAL_FILES or (
                    p.endswith(".md") and any(p.startswith(d + "/") for d in HISTORY_DIRS)
                ):
                    out.add(p)
            return tuple(sorted(out))
        for d in HISTORY_DIRS:
            base = self.root / d
            if fsutil.is_dir(base):
                for f in fsutil.iter_files(base, "*.md"):
                    out.add(f.relative_to(self.root).as_posix())
        out.update(p for p in LITERAL_FILES if fsutil.is_file(self.root / p))
        return tuple(sorted(out))


# --------------------------------------------------------------------------- rules


def _texts(data: bytes) -> list[str]:
    return [t[:-1] if t.endswith("\r") else t for t in data.decode("utf-8", "replace").split("\n")]


def _r3(path: str, data: bytes, allowed: frozenset[str]) -> list[RatchetFailure]:
    texts = _texts(data)
    kinds = mdblocks.scan(texts, strict=False)
    out: list[RatchetFailure] = []
    for n, (text, kind) in enumerate(zip(texts, kinds, strict=True), start=1):
        if kind != "h2":
            continue
        m = mdblocks.H2_RE.match(text)
        if m is None:
            continue
        title = m.group(1).strip()
        if not _CHANGELOG_RE.search(title):
            continue
        key = title_key(title)
        if key in allowed:
            continue
        out.append(
            RatchetFailure("R3", path, f"line={n} key={key} title={_trim(title)} remedy=reorg")
        )
    return out


def _r6(path: str, data: bytes) -> list[RatchetFailure]:
    """R6 (spec §12.2): every ``declutter:src`` marker has its ``declutter:end`` and the lines in
    between keep the marker's sha.

    Markers are single-line comments outside fences (``mdblocks.scan``, as R3 reads
    headings), and the end of a block is the first ``declutter:end`` of its unit at which the
    sha matches: a moved literal may itself contain marker-like lines (fenced or not)
    without being read as markers. When no end matches, the next marker decides
    (``altered`` for the same unit, else ``orphan_src``/``orphan_end``).
    """
    lines = mdblocks.split_lines(data).lines
    texts = [
        t[:-1] if t.endswith("\r") else t for t in (b.decode("utf-8", "replace") for b in lines)
    ]
    kinds = mdblocks.scan(texts, strict=False, front_matter=False)
    out: list[RatchetFailure] = []

    def fail(n: int, unit: str, state: str) -> None:
        out.append(RatchetFailure("R6", path, f"line={n} unit={_safe_id(unit)} state={state}"))

    def marker(i: int) -> bool:
        return kinds[i] == "comment_single" and (
            texts[i].startswith(_SRC_PREFIX) or texts[i].startswith(_END_PREFIX)
        )

    ends: dict[str, list[int]] = {}
    for i in range(len(texts)):
        if marker(i) and texts[i].startswith(_END_PREFIX):
            e = _END_RE.match(texts[i])
            if e is not None:
                ends.setdefault(e["unit"], []).append(i)

    i = 0
    while i < len(texts):
        if not marker(i):
            i += 1
            continue
        text = texts[i]
        if text.startswith(_END_PREFIX):
            e = _END_RE.match(text)
            fail(i + 1, e["unit"] if e is not None else "?", "orphan_end")
            i += 1
            continue
        m = _SRC_RE.match(text)
        if m is None:
            fail(i + 1, "?", "bad_marker")
            i += 1
            continue
        unit, sha = m["unit"], m["sha"]
        exact = next(
            (j for j in ends.get(unit, ()) if j > i and lines_sha(lines[i + 1 : j]) == sha), None
        )
        if exact is not None:
            i = exact + 1
            continue
        k = next((j for j in range(i + 1, len(texts)) if marker(j)), None)
        if k is None:
            fail(i + 1, unit, "orphan_src")
            break
        e = _END_RE.match(texts[k]) if texts[k].startswith(_END_PREFIX) else None
        if e is not None and e["unit"] == unit:
            fail(i + 1, unit, "altered")
            i = k + 1
        elif texts[k].startswith(_END_PREFIX):
            fail(i + 1, unit, "orphan_src")
            fail(k + 1, e["unit"] if e is not None else "?", "orphan_end")
            i = k + 1
        else:
            fail(i + 1, unit, "orphan_src")
            i = k
    return out


def _load_controls(
    src: _Source, out: list[RatchetFailure]
) -> tuple[RatchetState | None, tuple[Pin, ...] | None, tuple[Mapping[str, object], ...] | None]:
    """``ratchet.json``, ``pins.json`` and the log events; ``None`` when unusable."""
    state: RatchetState | None = None
    raw_state = src.read(RATCHET_REL)
    if raw_state is None:
        out.append(RatchetFailure("no_baseline", RATCHET_REL, "missing"))
    else:
        try:
            state = ratchet_loads(raw_state)
        except RatchetSchemaError as exc:
            out.append(RatchetFailure("schema", RATCHET_REL, f"{exc.where}: {exc.reason}"))
    pins: tuple[Pin, ...] | None = None
    raw_pins = src.read(PINS_REL)
    if raw_pins is None:
        if raw_state is not None:
            out.append(RatchetFailure("schema", PINS_REL, "missing"))
    else:
        try:
            pins = pins_loads(raw_pins)
        except RatchetSchemaError as exc:
            out.append(RatchetFailure("schema", PINS_REL, f"{exc.where}: {exc.reason}"))
    events: tuple[Mapping[str, object], ...] | None = None
    try:
        events = log_events(src.read(LOG_REL) or b"")
    except RatchetSchemaError as exc:
        out.append(RatchetFailure("schema", LOG_REL, f"{exc.where}: {exc.reason}"))
    return state, pins, events


def _layer_files(src: _Source, zones: Sequence[str]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for rel in (AGENTS, CLAUDE, *(f"{z}/{AGENTS}" for z in zones)):
        data = src.read(rel)
        if data is not None:
            files[rel] = data
        elif src.staged:
            files[rel] = b""  # absent from the index: never fall back to the disk
    return files


def measure(
    project: Project, cfg: Config, overlay: Mapping[str, bytes] | None = None
) -> tuple[int, float]:
    """``(S1, worst Codex %)`` as ``ratchet check`` measures them over the working tree,
    with ``overlay`` (project-relative path -> bytes) read in place of the disk (fase 2b
    §7.5). Raises ``_ReadFailure`` for an unsafe read."""
    src = _Source(project.root, False, overlay)
    zones = src.zones()
    proj = projection.project_after(project, cfg, _layer_files(src, zones), zones)
    return int(proj.after.get("S1") or 0), float(proj.codex_max_pct if proj.codex else 0.0)


def _check_rules(project: Project, cfg: Config, src: _Source, out: list[RatchetFailure]) -> None:
    state, pins, events = _load_controls(src, out)
    zones = src.zones()

    if state is not None:
        files = _layer_files(src, zones)
        proj = projection.project_after(project, cfg, files, zones)
        s1 = int(proj.after.get("S1") or 0)
        if s1 > state.s1_max + state.slack_lines:
            out.append(
                RatchetFailure(
                    "R1",
                    f"{CLAUDE}+{AGENTS}",
                    f"lines={s1} max={state.s1_max} slack={state.slack_lines}",
                )
            )
        if proj.codex and proj.codex_max_pct > state.s3_max_pct:
            worst = max(proj.codex, key=lambda c: c.pct)
            path = AGENTS if worst.name == "root" else f"{worst.name}/{AGENTS}"
            out.append(
                RatchetFailure(
                    "R2",
                    path,
                    f"chain={worst.name} pct={worst.pct:.1f} max={state.s3_max_pct}",
                )
            )
        if state.forbid_changelog_headings:
            allowed = frozenset(state.allowed_dated_headings)
            for rel in (AGENTS, CLAUDE, *(f"{z}/{AGENTS}" for z in zones)):
                data = files.get(rel)
                if data:
                    out.extend(_r3(rel, data, allowed))
        for z in zones:
            rel = f"{z}/{AGENTS}"
            n = len(mdblocks.split_lines(files.get(rel, b"")).lines)
            if n > state.zone_max_lines:
                out.append(RatchetFailure("R4", rel, f"lines={n} max={state.zone_max_lines}"))

    if pins is not None and events is not None:
        for pin in pins:
            data = src.read(pin.file)
            lines = mdblocks.split_lines(data).lines if data is not None else ()
            if find_window(lines, pin.sha256, pin.lines) is not None:
                continue
            if pin_released(pin, events, pins):
                continue
            out.append(
                RatchetFailure(
                    "R5",
                    pin.file,
                    f"pin={pin.id} role={pin.role} unit={_safe_id(pin.unit)} lines={pin.lines}",
                )
            )

    for rel in src.literal_files():
        data = src.read(rel)
        if data is not None:
            out.extend(_r6(rel, data))


def check(project: Project, cfg: Config, *, staged: bool) -> RatchetReport:
    """``declutter ratchet check [--staged]`` (spec §12.2): R1-R6 over the working tree or the
    index. Any rule failing gives 3, an invalid control file 2, ``--staged`` without git
    or a missing baseline 1; the code follows the precedence of spec §10.5 and every
    failure found is listed. Writes nothing."""
    root = project.root
    if staged and not gitops.is_repo(root):
        f = RatchetFailure("no_git", ".", "--staged needs a git repository")
        return RatchetReport(code=1, failures=(f,))
    out: list[RatchetFailure] = []
    try:
        _check_rules(project, cfg, _Source(root, staged), out)
    except _ReadFailure as exc:
        out.append(RatchetFailure("read", exc.path, exc.reason))
    return RatchetReport(code=_resolve(out), failures=tuple(out))


def check_files(project: Project, cfg: Config, overlay: Mapping[str, bytes]) -> RatchetReport:
    """``ratchet check`` over the working tree with ``overlay`` (project-relative path ->
    bytes) read in place of the disk: the check of a staged distillation (fase 2b §8.2
    step 3). Writes nothing."""
    out: list[RatchetFailure] = []
    try:
        _check_rules(project, cfg, _Source(project.root, False, overlay), out)
    except _ReadFailure as exc:
        out.append(RatchetFailure("read", exc.path, exc.reason))
    return RatchetReport(code=_resolve(out), failures=tuple(out))


# --------------------------------------------------------------------------- commands

_TEXT: Mapping[str, Mapping[str, str]] = {
    "es": {
        "no_tty": "ratchet: hace falta una terminal para confirmar (G9); no se cambió nada",
        "no_reason": "ratchet: falta el motivo (--reason)",
        "one_max": "ratchet: indica exactamente uno de --s1-to o --s3-to",
        "not_up": "ratchet relax: {key} pasaría de {old} a {new}; relax solo sube un máximo",
        "no_baseline": "ratchet: no hay línea base ({path})",
        "invalid": "ratchet: {path} inválido en {where}: {reason}",
        "read": "ratchet: no se pudo leer {path} ({reason})",
        "write": "ratchet: no se pudo escribir {path} ({reason})",
        "no_pin": "ratchet: no existe la fijación {pin}",
        "released": "ratchet: la fijación {pin} ya está liberada",
        "bad_lines": "ratchet repin: el rango {a}-{b} no está dentro de {path} ({n} líneas)",
        "not_agents": "ratchet repin: la fijación {pin} no está en AGENTS.md",
        "not_utf8": "ratchet repin: el rango {a}-{b} de {path} no es UTF-8",
        "same": "ratchet repin: el bloque {a}-{b} ya tiene el sha de {pin}; nada que registrar",
        "relax": "Subir {key}: {old} → {new}. Motivo: {reason}",
        "unpin": "Quitar la fijación {pin} ({role}, {file}, unidad {unit}, {lines} líneas, "
        "categorías: {cats}). Motivo: {reason}",
        "repin": "Volver a fijar {pin} ({role}) con {path}:{a}-{b} ({lines} líneas). "
        "sha {old} → {new}. Motivo: {reason}",
        "repin_cats": "Categorías actuales del bloque según el léxico: {cats}",
        "none": "ninguna",
        "ask": "Escribe «confirmo» para continuar: ",
        "not_confirmed": "ratchet: no se dio la confirmación (G9); no se cambió nada",
        "done": "ratchet: registrado en .declutter/ratchet.log",
    },
    "en": {
        "no_tty": "ratchet: a terminal is needed to confirm (G9); nothing changed",
        "no_reason": "ratchet: a reason is required (--reason)",
        "one_max": "ratchet: give exactly one of --s1-to or --s3-to",
        "not_up": "ratchet relax: {key} would go from {old} to {new}; relax only raises a maximum",
        "no_baseline": "ratchet: no baseline ({path})",
        "invalid": "ratchet: {path} invalid at {where}: {reason}",
        "read": "ratchet: cannot read {path} ({reason})",
        "write": "ratchet: cannot write {path} ({reason})",
        "no_pin": "ratchet: pin {pin} does not exist",
        "released": "ratchet: pin {pin} is already released",
        "bad_lines": "ratchet repin: range {a}-{b} is not inside {path} ({n} lines)",
        "not_agents": "ratchet repin: pin {pin} is not in AGENTS.md",
        "not_utf8": "ratchet repin: range {a}-{b} of {path} is not UTF-8",
        "same": "ratchet repin: block {a}-{b} already has the sha of {pin}; nothing to record",
        "relax": "Raise {key}: {old} → {new}. Reason: {reason}",
        "unpin": "Remove pin {pin} ({role}, {file}, unit {unit}, {lines} lines, "
        "categories: {cats}). Reason: {reason}",
        "repin": "Repin {pin} ({role}) to {path}:{a}-{b} ({lines} lines). "
        "sha {old} → {new}. Reason: {reason}",
        "repin_cats": "Current categories of the block per the lexicon: {cats}",
        "none": "none",
        "ask": "Type 'confirm' to continue: ",
        "not_confirmed": "ratchet: confirmation not given (G9); nothing changed",
        "done": "ratchet: recorded in .declutter/ratchet.log",
    },
}


def _tx(lang: str, name: str, /, **kw: object) -> str:
    table = _TEXT.get(lang, _TEXT["en"])
    return table[name].format(**kw) if kw else table[name]


class _Stop(Exception):
    """Ends a command with ``code`` after saying ``message``."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _read_disk(project: Project, rel: str, lang: str) -> bytes | None:
    try:
        return fsutil.safe_read_bytes(project.root / rel, project.root)
    except fsutil.UnsafeReadError as exc:
        if exc.reason == "missing":
            return None
        raise _Stop(2, _tx(lang, "read", path=rel, reason=exc.reason)) from None


def _invalid(rel: str, exc: RatchetSchemaError, lang: str) -> _Stop:
    return _Stop(2, _tx(lang, "invalid", path=rel, where=exc.where, reason=exc.reason))


def _load_log(project: Project, lang: str) -> tuple[bytes, tuple[Mapping[str, object], ...]]:
    data = _read_disk(project, LOG_REL, lang) or b""
    try:
        return data, log_events(data)
    except RatchetSchemaError as exc:
        raise _invalid(LOG_REL, exc, lang) from None


def _load_pins(project: Project, lang: str) -> tuple[Pin, ...]:
    data = _read_disk(project, PINS_REL, lang)
    if data is None:
        return ()
    try:
        return pins_loads(data)
    except RatchetSchemaError as exc:
        raise _invalid(PINS_REL, exc, lang) from None


def _find_pin(pins: Sequence[Pin], pin_id: str, lang: str) -> Pin:
    for p in pins:
        if p.id == pin_id:
            return p
    raise _Stop(1, _tx(lang, "no_pin", pin=_safe_id(pin_id)))


def _confirm(prompter: Prompter, lines: Sequence[str], lang: str) -> None:
    """G9: the confirmation word, typed in a TTY; ``--yes`` does not exist here."""
    if not prompter.is_tty():
        raise _Stop(1, _tx(lang, "no_tty"))
    for line in lines:
        prompter.say(line)
    if prompter.ask(_tx(lang, "ask")).strip().casefold() not in _CONFIRM_WORDS:
        raise _Stop(1, _tx(lang, "not_confirmed"))


def _write(project: Project, rel: str, data: bytes, lang: str) -> None:
    try:
        fsutil.atomic_write_bytes(project.root / rel, data, project.root)
    except fsutil.UnsafeWriteError as exc:
        raise _Stop(2, _tx(lang, "write", path=rel, reason=exc.reason)) from None


def _append_log(project: Project, prior: bytes, line: bytes, lang: str) -> None:
    head = prior if not prior or prior.endswith(b"\n") else prior + b"\n"
    _write(project, LOG_REL, head + line, lang)


def _reason(reason: str, lang: str) -> str:
    if not reason.strip():
        raise _Stop(1, _tx(lang, "no_reason"))
    return reason


def _run(prompter: Prompter, body: Callable[[], None]) -> int:
    try:
        body()
    except _Stop as stop:
        prompter.say(stop.message)
        return stop.code
    return 0


def relax(
    project: Project,
    *,
    s1_to: int | None,
    s3_to: int | None,
    reason: str,
    prompter: Prompter,
    today: date,
    lang: str = "es",
) -> int:
    """``declutter ratchet relax`` (spec §12.3): raises one maximum of ``ratchet.json`` after the
    G9 confirmation and logs it. No TTY → 1; it never lowers a maximum (that is the
    baseline's job)."""

    def body() -> None:
        _reason(reason, lang)
        if (s1_to is None) == (s3_to is None) or any(
            v is not None and v < 0 for v in (s1_to, s3_to)
        ):
            raise _Stop(1, _tx(lang, "one_max"))
        raw = _read_disk(project, RATCHET_REL, lang)
        if raw is None:
            raise _Stop(1, _tx(lang, "no_baseline", path=RATCHET_REL))
        try:
            state = ratchet_loads(raw)
        except RatchetSchemaError as exc:
            raise _invalid(RATCHET_REL, exc, lang) from None
        prior_log, _ = _load_log(project, lang)
        if s1_to is not None:
            key, old, new = "S1_max", state.s1_max, s1_to
            new_state = replace(state, s1_max=s1_to)
        else:
            assert s3_to is not None
            key, old, new = "S3_max_pct", state.s3_max_pct, s3_to
            new_state = replace(state, s3_max_pct=s3_to)
        if new <= old:
            raise _Stop(1, _tx(lang, "not_up", key=key, old=old, new=new))
        _confirm(prompter, [_tx(lang, "relax", key=key, old=old, new=new, reason=reason)], lang)
        line = log_line(
            "relax",
            date=today.isoformat(),
            reorg=state.reorg,
            old={key: old},
            new={key: new},
            reason=reason,
        )
        _append_log(project, prior_log, line, lang)
        _write(project, RATCHET_REL, ratchet_dumps(new_state), lang)
        prompter.say(_tx(lang, "done"))

    return _run(prompter, body)


def unpin(
    project: Project,
    pin_id: str,
    *,
    reason: str,
    prompter: Prompter,
    today: date,
    lang: str = "es",
) -> int:
    """``declutter ratchet unpin`` (spec §12.3, §8 G8/G9): releases one pin after the G9
    confirmation by logging an ``unpin`` event. The entry stays in ``pins.json`` as the
    audit record of what was pinned; R5 and ``verify`` skip it because of the event."""

    def body() -> None:
        _reason(reason, lang)
        pins = _load_pins(project, lang)
        prior_log, events = _load_log(project, lang)
        pin = _find_pin(pins, pin_id, lang)
        if pin_released(pin, events, pins):
            raise _Stop(1, _tx(lang, "released", pin=pin.id))
        cats = ", ".join(pin.categories) or _tx(lang, "none")
        msg = _tx(
            lang,
            "unpin",
            pin=pin.id,
            role=pin.role,
            file=pin.file,
            unit=_safe_id(pin.unit),
            lines=pin.lines,
            cats=cats,
            reason=reason,
        )
        _confirm(prompter, [msg], lang)
        line = log_line(
            "unpin",
            date=today.isoformat(),
            reorg=pin.reorg,
            old={"sha256": pin.sha256, "lines": pin.lines},
            new=None,
            reason=reason,
            pin=pin.id,
        )
        _append_log(project, prior_log, line, lang)
        prompter.say(_tx(lang, "done"))

    return _run(prompter, body)


def repin(
    project: Project,
    pin_id: str,
    *,
    lines: tuple[int, int],
    reason: str,
    prompter: Prompter,
    lexicon: Lexicon,
    today: date,
    lang: str = "es",
) -> int:
    """``declutter ratchet repin`` (spec §12.3): records the sha of the block at ``lines`` of
    ``AGENTS.md`` as the pin's new sha after the G9 confirmation. Shows the block's current
    categories per the lexicon; logs the old and the new sha."""

    def body() -> None:
        _reason(reason, lang)
        pins = _load_pins(project, lang)
        prior_log, _ = _load_log(project, lang)
        pin = _find_pin(pins, pin_id, lang)
        if pin.file != AGENTS:
            raise _Stop(1, _tx(lang, "not_agents", pin=pin.id))
        a, b = lines
        data = _read_disk(project, AGENTS, lang)
        all_lines = mdblocks.split_lines(data).lines if data is not None else ()
        if a < 1 or b < a or b > len(all_lines):
            raise _Stop(1, _tx(lang, "bad_lines", a=a, b=b, path=AGENTS, n=len(all_lines)))
        window = all_lines[a - 1 : b]
        try:
            texts = [mdblocks.decode_line(x) for x in window]
        except UnicodeDecodeError:
            raise _Stop(2, _tx(lang, "not_utf8", a=a, b=b, path=AGENTS)) from None
        new_sha = lines_sha(window)
        if new_sha == pin.sha256 and len(window) == pin.lines:
            raise _Stop(0, _tx(lang, "same", a=a, b=b, pin=pin.id))
        cats = ", ".join(sorted(lexicon.safety("\n".join(texts)))) or _tx(lang, "none")
        msg = _tx(
            lang,
            "repin",
            pin=pin.id,
            role=pin.role,
            path=AGENTS,
            a=a,
            b=b,
            lines=len(window),
            old=pin.sha256[:12],
            new=new_sha[:12],
            reason=reason,
        )
        _confirm(prompter, [msg, _tx(lang, "repin_cats", cats=cats)], lang)
        new_pin = replace(pin, lines=len(window), sha256=new_sha)
        line = log_line(
            "repin",
            date=today.isoformat(),
            reorg=pin.reorg,
            old={"sha256": pin.sha256, "lines": pin.lines},
            new={"sha256": new_sha, "lines": len(window)},
            reason=reason,
            pin=pin.id,
        )
        _append_log(project, prior_log, line, lang)
        _write(
            project, PINS_REL, pins_dumps([new_pin if p.id == pin.id else p for p in pins]), lang
        )
        prompter.say(_tx(lang, "done"))

    return _run(prompter, body)
