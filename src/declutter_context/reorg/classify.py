"""Deterministic classifier: features, destination rules, safety passes and budget (spec §5).

Pure functions over :class:`~declutter_context.model.Unit` and
:class:`~declutter_context.model.SourceDoc`.
Nothing here interprets text: every feature is a count or a lexicon match on the
comparison form (spec §5.1-§5.3). Destinations follow rules 1-11 of spec §5.4 in fixed
order; the passes of spec §5.5 emit literal copies (G1 safety pins, trap copies, card
excerpts); spec §5.6.1-3 proposes budget demotions; spec §5.7 sets confidence and the
review order; spec §5.8 gives overrides their effect.

Issues carry ids, keys, ranges and field names only; never file content nor reasons.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .. import fsutil, loadset
from ..model import Block, Decision, Features, SourceDoc, Unit
from ..signals_files import _CHANGELOG_RE
from . import Issue, mdblocks
from .lexicon import Lexicon, comparison_form, zone_mentions

FIXED_DESTS: frozenset[str] = frozenset({"L1", "L1c", "L4", "L5t", "L5m"})  # plus "L2:<dir>"
GUARD_ACK_MIN = 10  # spec §5.8: a guard_ack needs a reason of at least 10 characters

SizeFn = Callable[[Sequence[Decision]], int]

_GEN_OPEN_RE = re.compile(r"^<!-- declutter:gen (\S+)")
_GEN_END_RE = re.compile(r"^<!-- declutter:gen-end (\S+)")
_BRANCH_RE = re.compile(r"(?:rama|branch)\s+`([^`]+)`", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(?<!\d)(\d{1,2})-(\d{1,2})-(\d{4})")
_CODE_KINDS = frozenset(
    {
        "fence_open",
        "fence_body",
        "fence_close",
        "comment_open",
        "comment_body",
        "comment_close",
        "comment_single",
    }
)

_PATHS: Mapping[str, Mapping[str, str]] = {
    "es": {
        "history": "docs/historial",
        "undated": "sin-fecha.md",
        "L5t": "docs/trampas.md",
        "L5m": "docs/mapa.md",
    },
    "en": {
        "history": "docs/history",
        "undated": "undated.md",
        "L5t": "docs/pitfalls.md",
        "L5m": "docs/map.md",
    },
}


# --------------------------------------------------------------------------- shared types


@dataclass(frozen=True)
class UnitOverride:
    """Per-unit override (spec §5.8). ``field`` is "dest" | "card" | "keep" | "accept"."""

    heading_key: str
    field: str
    value: str | bool
    reason: str


@dataclass(frozen=True)
class BlockOverride:
    """Per-block ``guard_ack`` (spec §5.8); it never covers more than its own block."""

    block_key: str
    guard_ack: str


@dataclass(frozen=True)
class Overrides:
    units: tuple[UnitOverride, ...] = ()
    blocks: tuple[BlockOverride, ...] = ()

    def for_unit(self, heading_key: str) -> tuple[UnitOverride, ...]:
        return tuple(o for o in self.units if o.heading_key == heading_key)

    def ack(self, block_key: str) -> str:
        """The block's ``guard_ack``, or "" if none or if it is shorter than 10 characters.

        An invalid ``guard_ack`` (reported by :func:`apply_overrides`) never removes a pin.
        """
        for o in self.blocks:
            if o.block_key == block_key and len(o.guard_ack.strip()) >= GUARD_ACK_MIN:
                return o.guard_ack
        return ""


@dataclass(frozen=True)
class CopySpec:
    unit: str
    block: str
    source: str
    src: tuple[int, int]  # contiguous source range of the copy
    pin: str  # "block" | "subsection" | "list_group"
    target: str  # "invariants" | "trampas" | "card"
    categories: frozenset[str]


@dataclass(frozen=True)
class ClassifyContext:
    lexicon: Lexicon
    top_dirs: frozenset[str]  # first-level dirs: existing, not hidden, not in SKIP_DIRS
    paths_lang: str  # "es" | "en" (file names of L4/L5)
    agents_source: str | None  # "AGENTS.md" if it is a source
    claude_source: str | None  # "CLAUDE.md" if it is a source


def top_level_dirs(root: Path) -> frozenset[str]:
    """First-level directories of ``root`` that may be zones (spec §5.1 Z): real
    directories (not symlinks), not hidden, not in ``fsutil.SKIP_DIRS``."""
    out: set[str] = set()
    try:
        entries = list(root.iterdir())
    except OSError:
        return frozenset()
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in fsutil.SKIP_DIRS:
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            continue
        out.add(name)
    return frozenset(out)


def unit_bytes(doc: SourceDoc, start: int, end: int) -> int:
    """Bytes of lines ``start..end`` as written to a destination (each line + ``\\n``)."""
    return sum(len(line) + 1 for line in doc.lines[start - 1 : end])


# --------------------------------------------------------------------------- features


def normalize_date(heading: str) -> str | None:
    """``None`` if D does not match the title; else ``YYYY-MM-DD`` or ``"undated"``.

    The first date in position wins: ISO ``YYYY-MM-DD`` as is when valid, or ``D-M-YYYY``
    read day-month (swapped when the month is > 12 and the day is <= 12). Invalid dates,
    day-month without a year and branch-only titles give ``"undated"``.
    """
    text = comparison_form(heading)
    if not _CHANGELOG_RE.search(text):
        return None
    m = _DATE_RE.search(text)
    if m is None:
        return "undated"
    if m.group(1) is not None:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        day, month, year = int(m.group(4)), int(m.group(5)), int(m.group(6))
        if month > 12 and day <= 12:
            day, month = month, day
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return "undated"


def _is_preamble(unit: Unit) -> bool:
    return unit.heading_key == unit.id


def _is_generated(unit: Unit, texts: Sequence[str], claude_source: str | None) -> bool:
    """Rule 1 (spec §5.4): a region written by an earlier run."""
    if not _is_preamble(unit):
        first = next((s for s in texts[1:] if s.strip(" \t") != ""), None)
        return first is not None and _GEN_OPEN_RE.match(first) is not None
    if unit.source != claude_source:
        return False
    open_type: str | None = None
    content = False
    for s in texts:
        if open_type is not None:
            end = _GEN_END_RE.match(s)
            if end is not None and end.group(1) == open_type:
                open_type = None
            continue
        if s.strip(" \t") == "":
            continue
        content = True
        if s == "@AGENTS.md":
            continue
        if _GEN_END_RE.match(s) is None and (m := _GEN_OPEN_RE.match(s)) is not None:
            open_type = m.group(1)
            continue
        return False
    return content and open_type is None


def features(unit: Unit, doc: SourceDoc, ctx: ClassifyContext) -> Unit:
    """Fill the unit's :class:`Features` and the lexicon flags of each block (spec §5.1)."""
    texts = [mdblocks.decode_line(line) for line in doc.lines[unit.start - 1 : unit.end]]
    # A unit always starts outside every structure (line 1 or an ``##`` line), so its own
    # lines can be scanned alone; front matter can only open on line 1 of the source.
    kinds = mdblocks.scan(texts, strict=False, front_matter=unit.start == 1)
    lex = ctx.lexicon

    blocks: list[Block] = []
    for b in unit.blocks:
        body = "\n".join(texts[b.start - unit.start : b.end - unit.start + 1])
        blocks.append(
            replace(
                b,
                categories=lex.safety(body),
                imperative=lex.structural("imperative", body),
                decision=lex.structural("decision", body),
                trap=lex.structural("trap", body),
                claude_only=lex.structural("claude_only", body),
            )
        )

    imports = sum(
        1
        for s, kind in zip(texts, kinds, strict=True)
        if kind not in _CODE_KINDS and loadset.extract_imports(s)
    )

    counts: dict[str, int] = {}
    for s in texts:
        for seg in zone_mentions(s):
            if seg in ctx.top_dirs:
                counts[seg] = counts.get(seg, 0) + 1
    total = sum(counts.values())
    zone: str | None = None
    zone_ratio = 0.0
    zone_n = 0
    if total:
        zone = min(counts, key=lambda z: (-counts[z], z))
        zone_n = counts[zone]
        zone_ratio = zone_n / total

    title = unit.heading
    branch_m = _BRANCH_RE.search(comparison_form(title))
    feats = Features(
        dated=normalize_date(title),
        branch=branch_m.group(1) if branch_m else None,
        rule=lex.structural("rule", title),
        exec=lex.structural("exec", title),
        trap=lex.structural("trap", title),
        map=lex.structural("map", title),
        title_guard=lex.safety(title),
        k=sum(1 for b in blocks if b.imperative),
        s=sum(1 for b in blocks if b.categories),
        t=sum(1 for b in blocks if b.trap),
        imports=imports,
        q_ratio=(sum(1 for b in blocks if b.claude_only) / len(blocks)) if blocks else 0.0,
        zone=zone,
        zone_ratio=zone_ratio,
        zone_mentions=zone_n,
        generated=_is_generated(unit, texts, ctx.claude_source),
    )
    return replace(unit, blocks=tuple(blocks), features=feats)


# --------------------------------------------------------------------------- placement


def _file_for(dest: str, f: Features, paths_lang: str) -> str:
    paths = _PATHS["en" if paths_lang == "en" else "es"]
    if dest == "L1":
        return "AGENTS.md"
    if dest == "L1c":
        return "CLAUDE.md"
    if dest.startswith("L2:"):
        return f"{dest[3:]}/AGENTS.md"
    if dest == "L4":
        if f.dated is None or f.dated == "undated":
            return f"{paths['history']}/{paths['undated']}"
        return f"{paths['history']}/{f.dated[:7]}.md"
    return paths[dest]  # L5t, L5m


def _dominant_zone(f: Features) -> str | None:
    if f.zone is not None and f.zone_ratio >= 0.6 and f.zone_mentions >= 3:
        return f.zone
    return None


def _pointer_for(dest: str, card: bool, f: Features) -> str | None:
    if dest in ("L5t", "L5m"):
        return "root"
    if dest != "L4":
        return None
    zone = _dominant_zone(f)
    if f.rule:
        return f"root+zone:{zone}" if zone else "root"
    if card and zone:
        return f"zone:{zone}"
    return None


def _default_card(dest: str, f: Features) -> bool:
    return dest == "L4" and (f.rule or f.k >= 1)


def _place(
    unit: Unit, d: Decision, dest: str, paths_lang: str, card: bool | None = None
) -> Decision:
    f = unit.features
    c = _default_card(dest, f) if card is None else (card and dest == "L4")
    return replace(
        d, dest=dest, file=_file_for(dest, f, paths_lang), card=c, pointer=_pointer_for(dest, c, f)
    )


def _confidence(unit: Unit, d: Decision) -> str:
    if "rule:11" in d.reasons:
        return "low"
    if demoted(d) or "rule:10" in d.reasons or ("rule:7" in d.reasons and unit.features.k >= 1):
        return "medium"
    return "high"


def _needs_confirmation(unit: Unit, d: Decision) -> bool:
    if "generated" in d.reasons:
        return False
    return d.dest == "L1c" or demoted(d) or (unit.features.k >= 1 and d.dest != "L1")


def _finish(unit: Unit, d: Decision) -> Decision:
    return replace(
        d, confidence=_confidence(unit, d), needs_confirmation=_needs_confirmation(unit, d)
    )


# --------------------------------------------------------------------------- rules


def decide(unit: Unit, ctx: ClassifyContext) -> Decision:
    """Destination by rules 1-11 of spec §5.4 (first match wins), without overrides."""
    f = unit.features
    from_agents = unit.source == ctx.agents_source
    protected = False
    dest: str
    reasons: list[str]
    if f.generated:
        dest = "L1c" if unit.source == ctx.claude_source else "L1"
        reasons = ["generated", "rule:1"]
    elif _is_preamble(unit):
        dest, reasons = "L1", ["rule:2"]
    elif f.title_guard:
        dest, protected = "L1", True
        reasons = ["G2:" + ",".join(sorted(f.title_guard)), "rule:3"]
    elif f.imports >= 1:
        dest, reasons = "L1", ["imports", "rule:3b"]
    elif f.trap:
        dest, reasons = "L5t", ["rule:4"]
    elif f.map:
        dest, reasons = "L5m", ["rule:5"]
    elif f.dated is not None and f.rule:
        dest, reasons = "L1", ["dated_rule", "rule:6"]
    elif f.dated is not None:
        dest, reasons = "L4", ["rule:7"]
    else:
        claude_only = f.q_ratio >= 0.5 and not from_agents
        if claude_only and f.s == 0:
            dest, reasons = "L1c", ["rule:8"]
        else:
            reasons = ["claude_only_with_safety"] if claude_only else []
            if f.exec or f.rule:
                dest = "L1"
                reasons.append("rule:9")
            elif (
                f.zone is not None
                and f.zone_ratio >= 0.7
                and f.zone_mentions >= 5
                and f.k == 0
                and f.s == 0
                and not from_agents
            ):
                dest = f"L2:{f.zone}"
                reasons.append("rule:10")
            else:
                dest = "L1"
                reasons += ["rule:11", "review"]
    if f.dated is not None:
        reasons.append(f"D:date={f.dated}")
    if f.rule:
        reasons.append("R:title")
    reasons += [f"K={f.k}", f"S={f.s}"]
    base = Decision(
        unit=unit.id,
        dest=dest,
        file="",
        card=False,
        pointer=None,
        confidence="high",
        reasons=tuple(reasons),
        protected=protected,
        needs_confirmation=False,
    )
    return _finish(unit, _place(unit, base, dest, ctx.paths_lang))


# --------------------------------------------------------------------------- overrides


def _valid_dest(value: str, ctx: ClassifyContext) -> bool:
    if value in FIXED_DESTS:
        return True
    return value.startswith("L2:") and value[3:] in ctx.top_dirs


def _has_reason(o: UnitOverride) -> bool:
    return o.reason.strip() != ""


def _keep(unit: Unit, ov: Overrides) -> bool:
    """``keep`` or an explicit ``dest: "L1"`` (equivalent to keep, spec §5.8)."""
    return any(
        _has_reason(o)
        and ((o.field == "keep" and o.value is True) or (o.field == "dest" and o.value == "L1"))
        for o in ov.for_unit(unit.heading_key)
    )


def _accepted(unit: Unit, ov: Overrides) -> bool:
    """An explicit ``dest`` or an ``accept`` with a reason (spec §5.8)."""
    return any(
        _has_reason(o)
        and (
            (o.field == "dest" and isinstance(o.value, str))
            or (o.field == "accept" and o.value is True)
        )
        for o in ov.for_unit(unit.heading_key)
    )


def _l1c_confirmed(unit: Unit, ov: Overrides) -> bool:
    return any(
        _has_reason(o) and o.field == "dest" and o.value == "L1c"
        for o in ov.for_unit(unit.heading_key)
    )


def _unit_issue(code: int, key: str, unit: Unit, detail: str) -> Issue:
    return Issue(
        code=code,
        key=key,
        unit=unit.id,
        source=unit.source,
        src=(unit.start, unit.end),
        detail=detail,
    )


def _well_typed(o: UnitOverride) -> bool:
    if o.field == "dest":
        return isinstance(o.value, str)
    if o.field in ("card", "keep", "accept"):
        return isinstance(o.value, bool)
    return False


def apply_overrides(
    units: Sequence[Unit],
    decisions: Sequence[Decision],
    ov: Overrides,
    ctx: ClassifyContext,
) -> tuple[tuple[Decision, ...], tuple[Issue, ...]]:
    """Give the overrides their effect (spec §5.8) and report the invalid ones (code 4).

    Invalid overrides are reported and not applied; the others still are. Overrides whose
    key matches no unit or block are ignored here (``plan --from`` lists them as orphans).
    """
    issues: list[Issue] = []
    by_key: dict[str, list[Unit]] = {}
    blocks_by_key: dict[str, list[tuple[Unit, Block]]] = {}
    for u in units:
        by_key.setdefault(u.heading_key, []).append(u)
        for b in u.blocks:
            blocks_by_key.setdefault(b.key, []).append((u, b))

    for bo in ov.blocks:
        found = blocks_by_key.get(bo.block_key, [])
        if len(found) > 1:
            issues.append(Issue(code=4, key="ambiguous_override", detail=f"blocks={len(found)}"))
        elif found and len(bo.guard_ack.strip()) < GUARD_ACK_MIN:
            u, b = found[0]
            issues.append(
                Issue(
                    code=4,
                    key="check.8",
                    unit=u.id,
                    block=b.id,
                    source=u.source,
                    src=(b.start, b.end),
                    detail="guard_ack",
                )
            )

    out = list(decisions)
    index = {d.unit: i for i, d in enumerate(out)}
    seen: set[str] = set()
    for o in ov.units:
        key = o.heading_key
        if key in seen:
            continue
        seen.add(key)
        found_units = by_key.get(key, [])
        if not found_units:
            continue
        if len(found_units) > 1:
            issues.append(
                Issue(code=4, key="ambiguous_override", detail=f"units={len(found_units)}")
            )
            continue
        u = found_units[0]
        if u.id not in index:
            continue
        d = out[index[u.id]]
        valid: list[UnitOverride] = []
        for item in ov.for_unit(key):
            if not _well_typed(item):
                issues.append(_unit_issue(4, "manifest_invalid", u, item.field))
            elif not _has_reason(item):
                issues.append(_unit_issue(4, "check.8", u, item.field))
            else:
                valid.append(item)
        dest_o = next((x for x in reversed(valid) if x.field == "dest"), None)
        card_o = next((x for x in reversed(valid) if x.field == "card"), None)
        keep = any(x.field == "keep" and x.value is True for x in valid)
        accept = any(x.field == "accept" and x.value is True for x in valid)
        if keep and dest_o is not None and dest_o.value != "L1":
            issues.append(_unit_issue(4, "check.12", u, "keep+dest"))
            continue
        reasons = list(d.reasons)
        new = d
        if dest_o is not None:
            value = str(dest_o.value)
            if not _valid_dest(value, ctx):
                issues.append(_unit_issue(4, "check.3", u, "dest"))
            elif d.protected and value != d.dest:
                issues.append(_unit_issue(4, "check.4", u, "dest"))
            else:
                if value != d.dest:
                    new = _place(u, new, value, ctx.paths_lang)
                reasons.append("override:dest")
        if keep:
            if new.dest != "L1":
                new = _place(u, new, "L1", ctx.paths_lang)
            reasons.append("override:keep")
        if card_o is not None:
            if new.dest == "L4":
                new = _place(u, new, "L4", ctx.paths_lang, card=bool(card_o.value))
                reasons.append("override:card")
            elif not may_demote(u, new, ov):
                issues.append(_unit_issue(4, "card_not_l4", u, "card"))
            # else: the ratchet or the budget may still demote it to L4 (§5.6.1); the
            # override is given its effect, or rejected, by apply_card_overrides.
        if accept:
            reasons.append("override:accept")
        if new is d and reasons == list(d.reasons):
            continue
        out[index[u.id]] = _finish(u, replace(new, reasons=tuple(reasons)))
    return tuple(out), tuple(issues)


# --------------------------------------------------------------------------- passes


def _contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def copies(
    units: Sequence[Unit],
    docs: Mapping[str, SourceDoc],
    decisions: Sequence[Decision],
    ov: Overrides,
) -> tuple[CopySpec, ...]:
    """Literal copies of spec §5.5 for every unit whose primary is not L1 in AGENTS.md."""
    by_unit = {d.unit: d for d in decisions}
    out: list[CopySpec] = []
    for u in units:
        d = by_unit.get(u.id)
        if d is None or d.dest == "L1":
            continue
        doc = docs[u.source]
        emitted: list[CopySpec] = []
        for b in u.blocks:
            if b.categories and not ov.ack(b.key):
                rng: tuple[int, int]
                if b.kind == "heading":
                    rng, pin = mdblocks.subsection_range(doc, u, b), "subsection"
                elif (group := mdblocks.list_group_range(u, b, doc)) is not None:
                    rng, pin = group, "list_group"
                else:
                    rng, pin = (b.start, b.end), "block"
                emitted.append(CopySpec(u.id, b.id, u.source, rng, pin, "invariants", b.categories))
            if b.trap and d.dest != "L5t":
                emitted.append(
                    CopySpec(
                        u.id, b.id, u.source, (b.start, b.end), "block", "trampas", b.categories
                    )
                )
            if d.card and (b.imperative or b.decision):
                emitted.append(
                    CopySpec(u.id, b.id, u.source, (b.start, b.end), "block", "card", b.categories)
                )
        inv = [(i, c) for i, c in enumerate(emitted) if c.target == "invariants"]
        dropped = {
            i
            for i, c in inv
            for j, other in inv
            if j != i and _contains(other.src, c.src) and (other.src != c.src or j < i)
        }
        out.extend(c for i, c in enumerate(emitted) if i not in dropped)
    return tuple(out)


# --------------------------------------------------------------------------- budget


DEMOTED = frozenset({"budget:demoted", "ratchet:demoted"})


def demoted(d: Decision) -> bool:
    """Proposed for L4 by the budget (§5.6.1) or by the ratchet (§12.2 R3)."""
    return any(r in DEMOTED for r in d.reasons)


def may_demote(unit: Unit, d: Decision, ov: Overrides) -> bool:
    """A ``dated_rule`` unit still in L1 that a demotion could move to L4 (spec §5.6.1):
    never G2, undated, ``keep`` (or explicit ``dest: "L1"``) or with imports."""
    return (
        d.dest == "L1"
        and "dated_rule" in d.reasons
        and not d.protected
        and not _keep(unit, ov)
        and unit.features.imports == 0
        and unit.features.dated not in (None, "undated")
    )


def is_kept(unit: Unit, ov: Overrides) -> bool:
    """``keep`` or an explicit ``dest: "L1"`` with a reason (spec §5.8)."""
    return _keep(unit, ov)


def _demote(unit: Unit, d: Decision, paths_lang: str, reason: str = "budget:demoted") -> Decision:
    placed = _place(unit, d, "L4", paths_lang, card=True)
    return _finish(unit, replace(placed, reasons=(*d.reasons, reason)))


def demote_new_dated(
    units: Sequence[Unit],
    decisions: Sequence[Decision],
    ov: Overrides,
    *,
    ctx: ClassifyContext,
    allowed: frozenset[str] | None,
) -> tuple[Decision, ...]:
    """The remedy of R3 (spec §12.2: «``declutter reorg`` lo propondrá»): with a ratchet in place,
    a ``dated_rule`` unit whose changelog-like title is not in ``allowed_dated_headings``
    would make ``ratchet check`` fail if it stayed in L1, so it is proposed for L4 with a
    card and a pointer, like a budget demotion (``ratchet:demoted``, needs acceptance).
    ``keep`` (or ``dest: "L1"``) keeps it in L1 as the human's decision. ``allowed`` is
    ``None`` when there is no prior ``ratchet.json``."""
    if allowed is None:
        return tuple(decisions)
    out = list(decisions)
    index = {d.unit: i for i, d in enumerate(out)}
    for u in units:
        if u.id not in index:
            continue
        d = out[index[u.id]]
        title = u.heading.strip()
        if not may_demote(u, d, ov) or not _CHANGELOG_RE.search(title):
            continue
        if mdblocks.heading_key(title) in allowed:
            continue
        out[index[u.id]] = _demote(u, d, ctx.paths_lang, "ratchet:demoted")
    return tuple(out)


def apply_card_overrides(
    units: Sequence[Unit],
    decisions: Sequence[Decision],
    ov: Overrides,
    ctx: ClassifyContext,
) -> tuple[tuple[Decision, ...], tuple[Issue, ...]]:
    """Card overrides of units that a demotion moved to L4 (spec §5.8, §10.3 key ``f``):
    run after the demotions, so a demoted unit (L4) accepts ``card`` like any L4 unit.
    One that :func:`apply_overrides` left pending and was not demoted is ``card_not_l4``."""
    counts: dict[str, int] = {}
    for u in units:
        counts[u.heading_key] = counts.get(u.heading_key, 0) + 1
    out = list(decisions)
    index = {d.unit: i for i, d in enumerate(out)}
    issues: list[Issue] = []
    for u in units:
        if u.id not in index or counts[u.heading_key] > 1:
            continue
        valid = [
            o
            for o in ov.for_unit(u.heading_key)
            if o.field == "card" and _well_typed(o) and _has_reason(o)
        ]
        if not valid:
            continue
        d = out[index[u.id]]
        if demoted(d):
            new = _place(u, d, "L4", ctx.paths_lang, card=bool(valid[-1].value))
            out[index[u.id]] = _finish(u, replace(new, reasons=(*d.reasons, "override:card")))
        elif may_demote(u, d, ov):
            issues.append(_unit_issue(4, "card_not_l4", u, "card"))
    return tuple(out), tuple(issues)


def apply_budget(
    units: Sequence[Unit],
    docs: Mapping[str, SourceDoc],
    decisions: Sequence[Decision],
    ov: Overrides,
    *,
    ctx: ClassifyContext,
    l1_max_bytes: int,
    agents_size: SizeFn,
) -> tuple[Decision, ...]:
    """Propose demotions of ``dated_rule`` units while AGENTS.md exceeds the budget
    (spec §5.6.1): largest first; skipped when the net saving is <= 0; never G2, undated,
    ``keep`` (or explicit ``dest: "L1"``) or units with imports."""
    order = {u.id: i for i, u in enumerate(units)}
    current = list(decisions)
    index = {d.unit: i for i, d in enumerate(current)}
    cands = [
        u
        for u in units
        if u.id in index
        and (d := current[index[u.id]]).dest == "L1"
        and "dated_rule" in d.reasons
        and not d.protected
        and not _keep(u, ov)
        and u.features.imports == 0
        and u.features.dated not in (None, "undated")
    ]
    cands.sort(key=lambda u: (-unit_bytes(docs[u.source], u.start, u.end), order[u.id]))
    while cands and agents_size(current) > l1_max_bytes:
        u = cands.pop(0)
        trial = list(current)
        trial[index[u.id]] = _demote(u, current[index[u.id]], ctx.paths_lang)
        added = sum(
            unit_bytes(docs[c.source], c.src[0], c.src[1])
            for c in copies(units, docs, trial, ov)
            if c.unit == u.id and c.target == "invariants"
        )
        if unit_bytes(docs[u.source], u.start, u.end) - added <= 0:
            continue
        current = trial
    return tuple(current)


# --------------------------------------------------------------------------- confidence


def _units_by_id(units: Iterable[Unit]) -> dict[str, Unit]:
    return {u.id: u for u in units}


def finalize(
    units: Sequence[Unit], decisions: Sequence[Decision], ov: Overrides
) -> tuple[Decision, ...]:
    """Recompute ``confidence`` and ``needs_confirmation`` (spec §5.7, §5.8).

    ``needs_confirmation`` is a property of the decision; whether it is still pending
    given ``ov`` is :func:`pending_confirmations`.
    """
    del ov  # part of the contract; overrides do not change these two fields
    by_id = _units_by_id(units)
    return tuple(_finish(by_id[d.unit], d) if d.unit in by_id else d for d in decisions)


def review_order(units: Sequence[Unit], decisions: Sequence[Decision]) -> tuple[str, ...]:
    """Unit ids in review order (spec §5.7): low; K>=1, S=0 out of L1; medium; the rest."""
    by_unit = {d.unit: d for d in decisions}

    def group(u: Unit) -> int:
        d = by_unit[u.id]
        if d.confidence == "low":
            return 0
        if u.features.k >= 1 and u.features.s == 0 and d.dest != "L1":
            return 1
        if d.confidence == "medium":
            return 2
        return 3

    present = [u for u in units if u.id in by_unit]
    ranked = sorted(enumerate(present), key=lambda iu: (group(iu[1]), iu[0]))
    return tuple(u.id for _, u in ranked)


def pending_confirmations(
    units: Sequence[Unit], decisions: Sequence[Decision], ov: Overrides
) -> tuple[str, ...]:
    """Units still waiting for a human decision (spec §6.4.15 and §6.4.16), source order."""
    by_unit = {d.unit: d for d in decisions}
    out: list[str] = []
    for u in units:
        d = by_unit.get(u.id)
        if d is None or "generated" in d.reasons:
            continue
        if d.dest == "L1c":
            if not _l1c_confirmed(u, ov):
                out.append(u.id)
        elif (demoted(d) or (u.features.k >= 1 and d.dest != "L1")) and (not _accepted(u, ov)):
            out.append(u.id)
    return tuple(out)
