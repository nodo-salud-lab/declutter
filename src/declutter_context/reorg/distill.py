"""``declutter reorg distill-check``: may a card's invariant replace its literal copies? (fase 2b
spec §4, §5, §6.2).

Read-only. For an applied run (journal ``written`` or ``committed``) every G1 copy of
``AGENTS.md`` is classified as *with a card* (its unit is L4 with a ``card_id``) or
``no_card``; each card is parsed (§4.4) and checked against conditions (a)-(d) and the
structural and safety conditions e1-e10 (§5.2-§5.6). Nothing is written anywhere.

The core, :func:`check_run`, is a function of the manifest, the placements and a reader,
so ``distill-apply`` (phase 2b, task 2) can run it over its ``stage/`` folder. The
distilled entry of §6.2 is built by :func:`render_entry` and travels in
:attr:`CardCheck.entry` with its bytes and sha, ready for the write and the new pins.

Reports carry ids, paths, line numbers, columns, lengths, counts, bytes and categories;
never a byte of a card, a literal or an invariant (G6). The fields that hold content
(:attr:`CardCheck.invariant`, :attr:`Entry.lines`) are excluded from ``repr`` and never
serialized.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import fsutil, ratchet
from ..i18n import t
from ..loadset import extract_imports
from ..model import Pin, Placement
from . import (
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_PROOF,
    EXIT_SECRET,
    EXIT_USAGE,
    Issue,
    ReorgError,
    anchors,
    mdblocks,
    resolve_code,
)
from . import manifest as mf
from . import overlay as ov
from .apply import PLACEMENT_NAME, read_journal
from .build import anchor, load_template, section_titles
from .lexicon import Lexicon, comparison_form, scan_secrets

__all__ = [
    "GEN_END_CARD",
    "SCHEMA",
    "STATES",
    "CardCheck",
    "CheckReport",
    "CopyInfo",
    "Entry",
    "LineUpdate",
    "NoCardCopy",
    "Read",
    "Reason",
    "card_end_index",
    "card_path",
    "card_template",
    "check",
    "check_run",
    "disk_reader",
    "generated_updates",
    "last_distill",
    "render_entry",
    "report_json",
    "report_lines",
]

SCHEMA = "declutter.distill-check/1"
STATES: tuple[str, ...] = ("ready", "not_ready", "distilled", "no_card")
AGENTS = "AGENTS.md"
CHECKABLE_JOURNAL = frozenset({"written", "committed"})
SECTIONS: tuple[str, ...] = ("what", "why", "alternative", "pitfall", "invariant")
OPTIONAL_SECTIONS: tuple[str, ...] = ("why", "alternative", "pitfall")

# Reader of a project-relative path: bytes, ``None`` when absent; raises ``ReorgError``
# (code 2) for an unsafe read.
Read = Callable[[str], bytes | None]

GEN_END_CARD = b"<!-- declutter:gen-end card -->"
_ATX_RE = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
_SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+|\*+|_+)\s*$")
_MARKER_PREFIX = "<!-- declutter:"
_FENCE_KINDS = frozenset({"fence_open", "fence_body", "fence_close"})
_SRC_MARKER_RE = re.compile(
    r"^<!-- declutter:src (?P<source>\S+):(?P<a>\d+)-(?P<b>\d+) sha256=(?P<sha>[0-9a-f]{64}) "
    r"unit=(?P<unit>\S+) plan=(?P<plan>\S+) -->$"
)
_SENTINEL = "\x00declutter-origin\x00"


# --------------------------------------------------------------------------- report types


@dataclass(frozen=True)
class Reason:
    """One failed condition (or warning). Metadata only (G6)."""

    key: str
    code: int = EXIT_PROOF
    cls: str | None = None  # anchor class (token_missing / token_invented)
    path: str | None = None
    line: int | None = None  # 1-based line in ``path``
    col: int | None = None  # 1-based column (characters of the comparison form)
    length: int | None = None
    block: str | None = None
    pin: str | None = None
    section: str | None = None
    counts: tuple[tuple[str, int, int], ...] = ()  # (class, literal, invariant)
    detail: str = ""

    def to_obj(self) -> dict[str, object]:
        out: dict[str, object] = {"key": self.key, "code": self.code}
        for name in ("cls", "path", "line", "col", "length", "block", "pin", "section"):
            value = getattr(self, name)
            if value is not None:
                out["class" if name == "cls" else name] = value
        if self.counts:
            out["counts"] = [{"class": c, "literal": a, "invariant": b} for c, a, b in self.counts]
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class CopyInfo:
    """One G1 copy of a card in ``AGENTS.md`` (§4.1-§4.2)."""

    block: str
    unit: str
    pin: str | None
    source: str
    src: tuple[int, int]
    lines: int
    categories: tuple[str, ...]
    at: tuple[int, int] | None  # copy lines in the current AGENTS.md, if located
    bytes: int | None  # copy lines with their "\n"
    removed_bytes: int | None  # header + copy + blank line, with their "\n"

    def to_obj(self) -> dict[str, object]:
        return {
            "block": self.block,
            "pin": self.pin,
            "src": f"{self.source}:{self.src[0]}-{self.src[1]}",
            "lines": self.lines,
            "bytes": self.bytes,
            "categories": list(self.categories),
        }


@dataclass(frozen=True)
class Entry:
    """The distilled entry of §6.2 (Enmienda 05-11): header with the pointer, invariant, blank."""

    lines: tuple[bytes, ...] = field(repr=False)

    @property
    def data(self) -> bytes:
        return b"".join(x + b"\n" for x in self.lines)

    @property
    def window(self) -> tuple[bytes, ...]:
        """The lines of the ``invariant`` pin: header and invariant (§7.1)."""
        return self.lines[:-1]

    @property
    def sha256(self) -> str:
        return mdblocks.lines_sha(self.window)


@dataclass(frozen=True)
class CardCheck:
    card_id: str
    unit: str
    path: str
    state: str
    reasons: tuple[Reason, ...]
    warnings: tuple[Reason, ...]
    copies: tuple[CopyInfo, ...]
    bytes_removed: int | None
    bytes_added: int | None
    entry: Entry | None = None
    invariant: tuple[bytes, ...] = field(default=(), repr=False)
    invariant_at: tuple[int, int] | None = None  # 1-based lines in the card

    def to_obj(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "unit": self.unit,
            "path": self.path,
            "state": self.state,
            "reasons": [r.to_obj() for r in self.reasons],
            "warnings": [w.to_obj() for w in self.warnings],
            "copies": [c.to_obj() for c in self.copies],
            "bytes_removed": self.bytes_removed,
            "bytes_added": self.bytes_added,
        }


@dataclass(frozen=True)
class NoCardCopy:
    block: str
    pin: str | None
    unit: str
    dest: str
    bytes: int | None

    def to_obj(self) -> dict[str, object]:
        return {
            "block": self.block,
            "pin": self.pin,
            "unit": self.unit,
            "dest": self.dest,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class CheckReport:
    reorg: str
    lang: str
    paths_lang: str
    code: int
    cards: tuple[CardCheck, ...]
    no_card: tuple[NoCardCopy, ...]

    def totals(self) -> dict[str, int]:
        ready = [c for c in self.cards if c.state == "ready"]
        return {
            "cards": len(self.cards),
            "cards_ready": len(ready),
            "cards_not_ready": sum(1 for c in self.cards if c.state == "not_ready"),
            "cards_distilled": sum(1 for c in self.cards if c.state == "distilled"),
            "bytes_removed": sum(c.bytes_removed or 0 for c in ready),
            "bytes_added": sum(c.bytes_added or 0 for c in ready),
            "no_card": len(self.no_card),
            "no_card_bytes": sum(n.bytes or 0 for n in self.no_card),
        }


# --------------------------------------------------------------------------- small helpers


def _enc(text: str) -> bytes:
    return text.encode("utf-8")


def _windows(lines: Sequence[bytes], sha256: str, n: int) -> list[int]:
    """1-based starts of every window of ``n`` lines whose joined sha is ``sha256``."""
    if n < 1 or n > len(lines):
        return []
    return [
        s + 1 for s in range(len(lines) - n + 1) if mdblocks.lines_sha(lines[s : s + n]) == sha256
    ]


def _nbytes(lines: Iterable[bytes]) -> int:
    return sum(len(x) + 1 for x in lines)


def _blank(line: bytes) -> bool:
    return line.strip(b" \t") == b""


def _decode_all(lines: Sequence[bytes]) -> list[str] | None:
    try:
        return [mdblocks.decode_line(x) for x in lines]
    except UnicodeDecodeError:
        return None


def disk_reader(root: Path) -> Read:
    """Reader over the project folder through ``fsutil.safe_read_bytes`` (code 2 on refusal)."""

    def read(rel: str) -> bytes | None:
        try:
            return fsutil.safe_read_bytes(root / rel, root)
        except fsutil.UnsafeReadError as exc:
            if exc.reason == "missing":
                return None
            raise ReorgError(
                [Issue(code=EXIT_INTERNAL, key="unsafe_read", dest=rel, detail=exc.reason)]
            ) from None

    return read


# --------------------------------------------------------------------------- entry (§6.2)


def render_entry(
    reorg_id: str,
    unit: mf.UnitEntry,
    card_id: str,
    card_path: str,
    copies: Sequence[tuple[str, Iterable[str]]],
    invariant: Sequence[bytes],
) -> Entry:
    """The distilled entry of a card (§6.2), byte for byte.

    ``copies`` are ``(block, categories)`` in ``src0`` order; ``invariant`` the invariant
    lines copied as they are from the card. The pointer links the card (relative to the
    root), the unit's range in its original source and the first 12 hex of its sha.
    """
    blocks = ", ".join(b for b, _ in copies)
    cats = ", ".join(sorted({c for _, cs in copies for c in cs}))
    target = f"{unit.file}#{anchor(reorg_id, unit.id)}"
    a, b = unit.lines
    # Enmienda 05-11: the pointer lives on the header line, so an entry has the shape of a
    # copy (header + text + blank) and never grows AGENTS.md in lines by itself.
    header = (
        f"### ⟨{blocks}⟩ {card_id} → {target} · {cats} · [{card_id}]({card_path}) · "
        f"{unit.source}:{a}-{b} · sha256 {unit.sha256[:12]}"
    )
    return Entry(lines=(_enc(header), *invariant, b""))


# --------------------------------------------------------------------------- context


@dataclass(frozen=True)
class _Card:
    """The parsed sections of a card (§4.4)."""

    bodies: Mapping[str, tuple[int, int]]  # section -> [start, end) 0-based body lines
    pending: bytes  # the template's placeholder body line ("PENDIENTE" / "PENDING")
    invariant: tuple[bytes, ...]
    invariant_at: tuple[int, int] | None  # 1-based


class _Ctx:
    def __init__(
        self,
        m: mf.Manifest,
        placements: Sequence[Placement],
        read: Read,
        lexicon: Lexicon,
        pins: Sequence[Pin],
        events: Sequence[Mapping[str, object]],
    ) -> None:
        self.m = m
        self.places = tuple(placements)
        self.read = read
        self.lex = lexicon
        self.pins = tuple(pins)
        self.events = tuple(events)
        self.over = ov.build(m, self.places, self.events, self.pins)
        self.titles = section_titles(m.lang)
        pl = "en" if m.paths_lang == "en" else "es"
        self.paths = {role: cols[pl] for role, cols in mf.PATHS.items()}
        self._lines: dict[str, tuple[bytes, ...] | None] = {}

    def lines(self, path: str) -> tuple[bytes, ...] | None:
        if path not in self._lines:
            raw = self.read(path)
            self._lines[path] = None if raw is None else mdblocks.split_lines(raw).lines
        return self._lines[path]

    def place_at(self, dest: str, line: int) -> Placement | None:
        return next(
            (p for p in self.places if p.dest == dest and p.at[0] == line and p.at[1] == line),
            None,
        )

    def copy_pin(self, p: Placement) -> Pin | None:
        return next(
            (
                x
                for x in self.pins
                if x.role == "copy"
                and x.file == AGENTS
                and x.unit == p.unit
                and x.block == p.block
                and x.sha256 == p.sha256
            ),
            None,
        )

    def card_path(self, u: mf.UnitEntry) -> str:
        return card_path(self.m, u)

    def card_template(self, u: mf.UnitEntry, card_id: str) -> list[bytes]:
        return card_template(self.m, u, card_id)


def card_path(m: mf.Manifest, u: mf.UnitEntry) -> str:
    """The card of ``u`` as ``build`` names it (``paths_lang`` folder)."""
    return ov.card_path(m, u)


def card_template(m: mf.Manifest, u: mf.UnitEntry, card_id: str) -> list[bytes]:
    """The card as ``apply`` generated it, with a sentinel in the date/branch/zone line
    (the unit features are not in the manifest; that line is checked by sha)."""
    pl = "en" if m.paths_lang == "en" else "es"
    dec_dir = mf.PATHS["decisions_dir"][pl]
    anc = anchor(m.id, u.id)
    subs = {
        "id": m.id,
        "card_id": card_id,
        "title": u.heading,
        "date": _SENTINEL,
        "branch": _SENTINEL,
        "zone": _SENTINEL,
        "literal": f"{u.file}#{anc}",
        "literal_link": f"{posixpath.relpath(u.file, dec_dir)}#{anc}",
        "range": f"{u.source}:{u.lines[0]}-{u.lines[1]}",
        "sha256": u.sha256,
    }
    return load_template(m.lang, "card.md", subs)


def card_end_index(m: mf.Manifest, u: mf.UnitEntry, card_id: str) -> int:
    """0-based line of ``<!-- declutter:gen-end card -->`` in the generated card."""
    return card_template(m, u, card_id).index(GEN_END_CARD)


# --------------------------------------------------------------------------- card parsing


class _CardFail(Exception):
    def __init__(self, reasons: Sequence[Reason]) -> None:
        super().__init__(", ".join(r.key for r in reasons))
        self.reasons = tuple(reasons)


def _parse_card(
    ctx: _Ctx,
    u: mf.UnitEntry,
    card_id: str,
    path: str,
    lines: Sequence[bytes],
    texts: Sequence[str],
) -> tuple[_Card, list[Reason]]:
    """Structure of §4.4. Raises ``_CardFail`` when the sections cannot be delimited;
    returns the parsed card and the non-blocking structural reasons otherwise."""
    reasons: list[Reason] = []
    try:
        kinds = mdblocks.scan(texts, strict=True)
    except mdblocks.SourceError as exc:
        raise _CardFail([Reason("card_unbalanced", path=path, line=exc.line)]) from None
    tmpl = ctx.card_template(u, card_id)
    sentinel = _enc(_SENTINEL)
    date_idx = [i for i, x in enumerate(tmpl) if sentinel in x]
    end_t = tmpl.index(GEN_END_CARD)
    marker = tmpl[2]
    pending_state = tmpl[1]
    distilled_state = _enc(ctx.titles["card_state_line_distilled"])
    if not lines or lines[0] != tmpl[0]:
        reasons.append(Reason("card_structure", path=path, line=1))
    if len(lines) < 2 or lines[1] not in (pending_state, distilled_state):
        reasons.append(Reason("card_state", path=path, line=2))
    elif lines[1] == distilled_state:
        # Reached only when the distilled verification did not apply (§10.1).
        reasons.append(Reason("card_state", path=path, line=2, detail="distilled"))
    if lines.count(marker) != 1 or len(lines) < 3 or lines[2] != marker:
        reasons.append(Reason("card_markers", path=path, line=3))
    if lines.count(GEN_END_CARD) != 1:
        raise _CardFail([*reasons, Reason("card_markers", path=path)])
    end = lines.index(GEN_END_CARD)
    # Origin: title and literal line byte for byte; the date line by the sha of the
    # card placement of the run (the template with that line taken from the disk).
    if len(date_idx) != 1:
        raise ValueError("card.md template: one origin line expected")
    di = date_idx[0]
    origin = range(3, di + 2)
    if end <= di + 1 or any(lines[i] != tmpl[i] for i in origin if i != di):
        reasons.append(Reason("origin_changed", path=path, line=4))
    else:
        rebuilt = [*tmpl[:di], lines[di], *tmpl[di + 1 :]]
        place = next(
            (
                p
                for p in ctx.places
                if p.role == "generated" and p.kind == "card" and p.dest == path
            ),
            None,
        )
        if place is None or mdblocks.lines_sha(rebuilt) != place.sha256:
            reasons.append(Reason("origin_changed", path=path, line=di + 1))
    # Sections: the level-2 headings between the origin and the closing marker.
    titles_t = [x for x in tmpl[di + 2 : end_t] if x.startswith(b"## ")]
    if len(titles_t) != len(SECTIONS):
        raise ValueError("card.md template: five sections expected")
    first = di + 2
    # Other level-2 lines are body text (an invariant with one fails e2, §5.6).
    h2 = [i for i in range(first, end) if kinds[i] == "h2"]
    heads = [i for i in h2 if lines[i] in titles_t]
    if (
        [lines[i] for i in heads] != titles_t
        or heads[0] != first
        or end + 1 >= len(lines)
        or lines[end + 1] != tmpl[end_t + 1]
    ):
        raise _CardFail([*reasons, Reason("card_structure", path=path)])
    bounds = [*heads, end]
    bodies = {SECTIONS[k]: (bounds[k] + 1, bounds[k + 1]) for k in range(len(SECTIONS))}
    a, b = bodies["invariant"]
    body = list(range(a, b))
    help_line = tmpl[end_t - 1]
    nonblank = [i for i in body if not _blank(lines[i])]
    if nonblank and lines[nonblank[-1]] == help_line:
        body = [i for i in body if i != nonblank[-1]]
    while body and _blank(lines[body[0]]):
        body.pop(0)
    while body and _blank(lines[body[-1]]):
        body.pop()
    inv = tuple(lines[i] for i in body)
    at = (body[0] + 1, body[-1] + 1) if body else None
    pending = tmpl[tmpl.index(titles_t[0]) + 1]
    return _Card(bodies=bodies, pending=pending, invariant=inv, invariant_at=at), reasons


# --------------------------------------------------------------------------- conditions


def _pending_word(tmpl_body_line: bytes) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(tmpl_body_line.decode('utf-8').strip())}\b")


def _section_texts(texts: Sequence[str], span: tuple[int, int]) -> list[str]:
    return list(texts[span[0] : span[1]])


def _pending(ctx: _Ctx, card: _Card, texts: Sequence[str]) -> tuple[list[Reason], list[Reason]]:
    word = _pending_word(card.pending)
    reasons: list[Reason] = []
    warnings: list[Reason] = []

    def has(lines: Iterable[str]) -> bool:
        return any(word.search(comparison_form(x)) for x in lines)

    if has(_section_texts(texts, card.bodies["what"])):
        reasons.append(Reason("pending_what", section="what"))
    inv_texts = [mdblocks.decode_line(x) for x in card.invariant]
    if has(inv_texts):
        reasons.append(Reason("pending_invariant", section="invariant"))
    joined = " ".join(x.strip() for x in inv_texts).strip()
    if not joined:
        reasons.append(Reason("invariant_empty", section="invariant"))
    elif joined.casefold().rstrip(".").strip() == ctx.titles["not_stated"].casefold():
        reasons.append(Reason("invariant_not_stated", section="invariant"))
    for name in OPTIONAL_SECTIONS:
        if has(_section_texts(texts, card.bodies[name])):
            warnings.append(Reason("pending_optional", code=EXIT_OK, section=name))
    return reasons, warnings


def _structure(path: str, card: _Card) -> list[Reason]:
    """e1-e4 over the invariant (§5.6)."""
    out: list[Reason] = []
    lines = card.invariant
    first = card.invariant_at[0] if card.invariant_at else 1
    if not mdblocks.is_balanced(lines):
        out.append(Reason("invariant_unbalanced", path=path, line=first))
    texts = _decode_all(lines) or []
    kinds = mdblocks.scan(texts, strict=False)
    for i, (text, kind) in enumerate(zip(texts, kinds, strict=True)):
        if kind not in _FENCE_KINDS and (_ATX_RE.match(text) or _SETEXT_RE.match(text)):
            out.append(Reason("invariant_heading", path=path, line=first + i))
        if text.lstrip().startswith(_MARKER_PREFIX):
            out.append(Reason("invariant_marker", path=path, line=first + i))
    if extract_imports("\n".join(texts)):
        out.append(Reason("invariant_import", path=path, line=first))
    for i, raw in enumerate(lines):
        if b"\r" in raw:
            out.append(Reason("invariant_cr", path=path, line=first + i))
    return out


def _history(ctx: _Ctx, u: mf.UnitEntry) -> tuple[tuple[bytes, ...] | None, list[Reason]]:
    """(d1): the unit's literal between its markers, if intact."""
    lines = ctx.lines(u.file)
    if lines is None:
        return None, [Reason("marker_missing", path=u.file)]
    start: int | None = None
    found: re.Match[str] | None = None
    for i, line in enumerate(lines):
        try:
            text = mdblocks.decode_line(line)
        except UnicodeDecodeError:
            continue
        mm = _SRC_MARKER_RE.match(text)
        if mm and mm["unit"] == u.id and mm["plan"] == ctx.m.id:
            start, found = i, mm
            break
    if start is None or found is None:
        return None, [Reason("marker_missing", path=u.file)]
    end_line = _enc(f"<!-- declutter:end {u.id} -->")
    end = next((j for j in range(start + 1, len(lines)) if lines[j] == end_line), None)
    if end is None:
        return None, [Reason("marker_missing", path=u.file, line=start + 1)]
    block = tuple(lines[start + 1 : end])
    if (
        found["sha"] != u.sha256
        or found["source"] != u.source
        or (int(found["a"]), int(found["b"])) != u.lines
        or mdblocks.lines_sha(block) != u.sha256
    ):
        return None, [Reason("history_altered", path=u.file, line=start + 1)]
    return block, []


def _generated_line(ctx: _Ctx, dest: str, kind: str, line: bytes, key: str) -> list[Reason]:
    """e9: ``line`` exactly once inside the located windows of the run's ``kind`` regions."""
    lines = ctx.lines(dest)
    count = 0
    if lines is not None:
        for i, p in enumerate(ctx.places):
            if p.role != "generated" or p.dest != dest or p.kind != kind:
                continue
            exp = ctx.over.expected[i]  # after the earlier distillations (§10.1)
            if exp is None:
                continue
            sha, n = exp
            start = ratchet.find_window(lines, sha, n)
            if start is not None:
                count += lines[start - 1 : start - 1 + n].count(line)
    return [] if count == 1 else [Reason(key, path=dest)]


@dataclass(frozen=True)
class LineUpdate:
    """A generated line that ``distill-apply`` rewrites (§6.3) and e9 checks (§5.6)."""

    dest: str
    kind: str
    old: bytes
    new: bytes
    key: str


def generated_updates(m: mf.Manifest, u: mf.UnitEntry, card_id: str) -> tuple[LineUpdate, ...]:
    """The index row and the pointer lines of a card, before and after distilling (§6.3),
    rebuilt from the manifest and ``section_titles.json``."""
    t = section_titles(m.lang)
    pl = "en" if m.paths_lang == "en" else "es"
    paths = {role: cols[pl] for role, cols in mf.PATHS.items()}
    path = card_path(m, u)
    t60 = mf.truncate60(u.heading)
    link = posixpath.relpath(path, paths["decisions_dir"])
    title = t60.replace("|", "\\|")
    out: list[LineUpdate] = []

    def row(state: str) -> bytes:
        return _enc(f"| {card_id} | {title} | {state} | [{t['card_link']}]({link}) |")

    out.append(
        LineUpdate(
            paths["decisions_index"],
            "index",
            row(t["card_state"]),
            row(t["card_state_distilled"]),
            "index_row_changed",
        )
    )
    pointer = u.pointer or ""
    targets: list[tuple[str, str, str]] = []
    if pointer.startswith("root"):
        targets.append((AGENTS, "dated-rules", posixpath.relpath(path, ".")))
    if "zone:" in pointer:
        zone = pointer.split("zone:", 1)[1]
        targets.append((f"{zone}/AGENTS.md", "zone", posixpath.relpath(path, zone)))
    for dest, kind, rel in targets:
        before = _enc(f"- {card_id} {t60} → {rel} ({t['pending']})")
        after = _enc(f"- {card_id} {t60} → {rel} ({t['distilled']})")
        out.append(LineUpdate(dest, kind, before, after, "pointer_changed"))
    return tuple(out)


def _generated_lines(ctx: _Ctx, u: mf.UnitEntry, card_id: str, path: str) -> list[Reason]:
    del path
    out: list[Reason] = []
    for up in generated_updates(ctx.m, u, card_id):
        out += _generated_line(ctx, up.dest, up.kind, up.old, up.key)
    return out


def _block_categories(u: mf.UnitEntry, block: str) -> tuple[str, ...]:
    for b in u.blocks:
        if b.id == block:
            return tuple(b.guard)
    return ()


def _locate(ctx: _Ctx, u: mf.UnitEntry, p: Placement) -> tuple[CopyInfo, list[Reason]]:
    """§4.1-§4.2 for one copy: its pin, its window, its generated header and blank."""
    reasons: list[Reason] = []
    pin = ctx.copy_pin(p)
    block = p.block or ""
    cats = _block_categories(u, block)
    n = p.at[1] - p.at[0] + 1
    if pin is None:
        reasons.append(Reason("pin_missing", block=block))
    elif ratchet.pin_released(pin, ctx.events, ctx.pins):
        reasons.append(Reason("pin_released", block=block, pin=pin.id))
    lines = ctx.lines(AGENTS) or ()
    starts = _windows(lines, p.sha256, n)
    at: tuple[int, int] | None = None
    nbytes: int | None = None
    removed: int | None = None
    if not starts:
        reasons.append(Reason("copy_not_located", block=block, path=AGENTS))
    elif len(starts) > 1:
        reasons.append(Reason("copy_ambiguous", block=block, path=AGENTS))
    else:
        s = starts[0]
        at = (s, s + n - 1)
        window = lines[s - 1 : s - 1 + n]
        nbytes = _nbytes(window)
        target = f"{u.file}#{anchor(ctx.m.id, u.id)}"
        header = _enc(f"### ⟨{block}⟩ → {target} · {', '.join(cats)}")
        hp = ctx.place_at(AGENTS, p.at[0] - 1)
        bp = ctx.place_at(AGENTS, p.at[1] + 1)
        ok = (
            s >= 2
            and lines[s - 2] == header
            and hp is not None
            and hp.role == "generated"
            and hp.sha256 == mdblocks.lines_sha([header])
            and s - 1 + n < len(lines)
            and lines[s - 1 + n] == b""
            and bp is not None
            and bp.role == "generated"
            and bp.kind == "blank"
            and bp.sha256 == mdblocks.lines_sha([b""])
        )
        if ok:
            removed = len(header) + 1 + nbytes + 1
        else:
            reasons.append(Reason("header_changed", block=block, path=AGENTS, line=s - 1))
    if p.source is None or p.src is None:
        raise ValueError("copy placement without source range")
    info = CopyInfo(
        block=block,
        unit=u.id,
        pin=None if pin is None else pin.id,
        source=p.source,
        src=p.src,
        lines=n,
        categories=cats,
        at=at,
        bytes=nbytes,
        removed_bytes=removed,
    )
    return info, reasons


def _anchors(
    ctx: _Ctx,
    path: str,
    card: _Card,
    copies: Sequence[CopyInfo],
    unit_literal: Sequence[bytes] | None,
) -> list[Reason]:
    """(b1), (b2) and (c) (§5.3, §5.4); needs every copy located."""
    lines = ctx.lines(AGENTS) or ()
    cited: list[str] = []
    origin: list[tuple[str, int]] = []
    for c in copies:
        if c.at is None:
            return []
        for k, raw in enumerate(lines[c.at[0] - 1 : c.at[1]]):
            cited.append(mdblocks.decode_line(raw))
            origin.append((c.source, c.src[0] + k))
    inv = [mdblocks.decode_line(x) for x in card.invariant]
    first = card.invariant_at[0] if card.invariant_at else 1
    out: list[Reason] = []
    inv_set = set(anchors.tokens(inv))
    seen: set[tuple[str, str]] = set()
    for tok in anchors.token_spans(cited):
        key = (tok.cls, tok.text)
        if key in seen:
            continue
        seen.add(key)
        if key not in inv_set:
            src, line = origin[tok.line]
            out.append(
                Reason(
                    "token_missing",
                    cls=tok.cls,
                    path=src,
                    line=line,
                    col=tok.col,
                    length=tok.length,
                )
            )
    if unit_literal is not None:
        unit_texts = _decode_all(unit_literal) or []
        unit_set = set(anchors.tokens(unit_texts))
        seen = set()
        for tok in anchors.token_spans(inv):
            key = (tok.cls, tok.text)
            if key in seen:
                continue
            seen.add(key)
            if key not in unit_set:
                out.append(
                    Reason(
                        "token_invented",
                        cls=tok.cls,
                        path=path,
                        line=first + tok.line,
                        col=tok.col,
                        length=tok.length,
                    )
                )
    lit_d = anchors.deontic(cited)
    inv_d = anchors.deontic(inv)
    if lit_d != inv_d:
        counts = tuple(
            (cls, lit_d[cls], inv_d[cls])
            for cls in anchors.DEONTIC_CLASSES
            if lit_d[cls] != inv_d[cls]
        )
        out.append(Reason("deontic_mismatch", counts=counts))
    return out


# --------------------------------------------------------------------------- distilled


def last_distill(
    ctx_events: Sequence[Mapping[str, object]], card_id: str
) -> Mapping[str, object] | None:
    """The last ``distill`` event (already restricted to the run) of ``card_id``."""
    named = [e for e in ctx_events if e.get("card") == card_id]
    return named[-1] if named else None


def _distilled(
    ctx: _Ctx, u: mf.UnitEntry, card_id: str, path: str, places: Sequence[Placement]
) -> CardCheck | None:
    """§5.1 ``distilled`` (§10.1): the card's ``distill`` event is in the log, its new
    pins exist and their windows are intact, and the history literal keeps its sha.
    ``None`` when no event of this card exists (the state line alone proves nothing)."""
    e = last_distill(ctx.over.events, card_id)
    if e is None:
        return None
    reasons: list[Reason] = []
    wanted = ratchet.distill_new_pins(e) or ()
    if not wanted:
        reasons.append(Reason("pin_missing", path=path, detail="distill event"))
    by_id = {x.id: x for x in ctx.pins}
    for pid in wanted:
        pin = by_id.get(pid)
        if pin is None:
            reasons.append(Reason("pin_missing", pin=pid))
            continue
        lines = ctx.lines(pin.file) or ()
        if ratchet.find_window(lines, pin.sha256, pin.lines) is None:
            reasons.append(Reason("pin_missing", pin=pid, path=pin.file))
    _unit_literal, rs = _history(ctx, u)
    reasons += rs
    copies: list[CopyInfo] = []
    for p in sorted(places, key=lambda x: (x.src or (0, 0), x.at)):
        pin_of = ctx.copy_pin(p)
        if p.source is None or p.src is None:
            raise ValueError("copy placement without source range")
        copies.append(
            CopyInfo(
                block=p.block or "",
                unit=u.id,
                pin=None if pin_of is None else pin_of.id,
                source=p.source,
                src=p.src,
                lines=p.at[1] - p.at[0] + 1,
                categories=_block_categories(u, p.block or ""),
                at=None,
                bytes=None,
                removed_bytes=None,
            )
        )
    # The entry pinned in AGENTS.md must be the one the frozen card's invariant renders.
    inv_pin = next((by_id[x] for x in wanted if x in by_id and by_id[x].role == "invariant"), None)
    raw = ctx.read(path)
    lines = () if raw is None else mdblocks.split_lines(raw).lines
    texts = _decode_all(lines)
    entry_ok = False
    if inv_pin is not None and texts is not None:
        try:
            card, _rs = _parse_card(ctx, u, card_id, path, lines, texts)
        except _CardFail:
            card = None
        if card is not None:
            entry = render_entry(
                ctx.m.id,
                u,
                card_id,
                path,
                [(c.block, c.categories) for c in copies],
                card.invariant,
            )
            entry_ok = entry.sha256 == inv_pin.sha256 and len(entry.window) == inv_pin.lines
    if not entry_ok:
        reasons.append(Reason("entry_mismatch", path=path))
    return CardCheck(
        card_id=card_id,
        unit=u.id,
        path=path,
        state="not_ready" if reasons else "distilled",
        reasons=tuple(reasons),
        warnings=(),
        copies=tuple(copies),
        bytes_removed=None,
        bytes_added=None,
    )


# --------------------------------------------------------------------------- one card


def _check_card(ctx: _Ctx, u: mf.UnitEntry, card_id: str, places: Sequence[Placement]) -> CardCheck:
    path = ctx.card_path(u)
    reasons: list[Reason] = []
    warnings: list[Reason] = []
    copies: list[CopyInfo] = []
    for p in sorted(places, key=lambda x: (x.src or (0, 0), x.at)):
        info, rs = _locate(ctx, u, p)
        copies.append(info)
        reasons += rs
    if not places:
        reasons.append(Reason("no_copies"))
    unit_literal, rs = _history(ctx, u)
    reasons += rs
    reasons += _generated_lines(ctx, u, card_id, path)

    def done(card: _Card | None = None, entry: Entry | None = None) -> CardCheck:
        removed: int | None = None
        if copies and all(c.removed_bytes is not None for c in copies):
            removed = sum(c.removed_bytes or 0 for c in copies)
        return CardCheck(
            card_id=card_id,
            unit=u.id,
            path=path,
            state="not_ready" if reasons else "ready",
            reasons=tuple(reasons),
            warnings=tuple(warnings),
            copies=tuple(copies),
            bytes_removed=removed,
            bytes_added=None if entry is None else len(entry.data),
            entry=entry,
            invariant=() if card is None else card.invariant,
            invariant_at=None if card is None else card.invariant_at,
        )

    raw = ctx.read(path)
    if raw is None or b"\x00" in raw:
        reasons.append(Reason("card_unreadable", code=EXIT_INTERNAL, path=path))
        return done()
    for hit in scan_secrets(path, raw):
        reasons.append(
            Reason(
                "invariant_secret", code=EXIT_SECRET, path=path, line=hit.line, detail=hit.category
            )
        )
    lines = mdblocks.split_lines(raw).lines
    texts = _decode_all(lines)
    if texts is None:
        reasons.append(Reason("card_unreadable", code=EXIT_INTERNAL, path=path))
        return done()
    distilled_state = _enc(ctx.titles["card_state_line_distilled"])
    if len(lines) >= 2 and lines[1] == distilled_state:
        found = _distilled(ctx, u, card_id, path, places)
        if found is not None:
            secrets = [r for r in reasons if r.key == "invariant_secret"]
            return (
                found
                if not secrets
                else replace(found, state="not_ready", reasons=(*secrets, *found.reasons))
            )
    try:
        card, rs = _parse_card(ctx, u, card_id, path, lines, texts)
    except _CardFail as exc:
        reasons.extend(exc.reasons)
        return done()
    reasons += rs
    pend, warns = _pending(ctx, card, texts)
    reasons += pend
    warnings += warns
    if not card.invariant or any(r.key == "invariant_empty" for r in pend):
        return done(card)
    reasons += _structure(path, card)
    needed = {c for info in copies for c in info.categories}
    got = ctx.lex.safety("\n".join(_decode_all(card.invariant) or []))
    lost = sorted(needed - got)
    if lost:
        reasons.append(Reason("categories_lost", detail=", ".join(lost)))
    if copies:
        reasons += _anchors(ctx, path, card, copies, unit_literal)
    entry = render_entry(
        ctx.m.id, u, card_id, path, [(c.block, c.categories) for c in copies], card.invariant
    )
    removed_total = sum(c.removed_bytes or 0 for c in copies)
    all_located = copies and all(c.removed_bytes is not None for c in copies)
    if all_located and len(entry.data) >= removed_total:
        reasons.append(Reason("not_shorter", detail=f"{len(entry.data)} >= {removed_total} B"))
    # Enmienda 05-11: nor longer in lines (header + text + blank of every copy), so a
    # distillation never grows S1 beyond the one-time region overhead (§6.1).
    removed_lines = sum(c.lines + 2 for c in copies)
    if all_located and len(entry.lines) > removed_lines:
        reasons.append(
            Reason(
                "not_shorter",
                detail=f"{len(entry.lines)} > {removed_lines} lines: shorten the invariant",
            )
        )
    return done(card, entry)


# --------------------------------------------------------------------------- run


def _controls(read: Read) -> tuple[tuple[Pin, ...], tuple[Mapping[str, object], ...]]:
    def invalid(rel: str, where: str, reason: str) -> ReorgError:
        return ReorgError(
            [
                Issue(
                    code=EXIT_INTERNAL, key="control_invalid", dest=rel, detail=f"{where}: {reason}"
                )
            ]
        )

    data = read(ratchet.PINS_REL)
    if data is None:
        raise invalid(ratchet.PINS_REL, "$", "missing")
    try:
        pins = ratchet.pins_loads(data)
    except ratchet.RatchetSchemaError as exc:
        raise invalid(ratchet.PINS_REL, exc.where, exc.reason) from None
    log = read(ratchet.LOG_REL)
    if log is None:
        raise invalid(ratchet.LOG_REL, "$", "missing")
    try:
        events = ratchet.log_events(log)
    except ratchet.RatchetSchemaError as exc:
        raise invalid(ratchet.LOG_REL, exc.where, exc.reason) from None
    return pins, events


def check_run(
    m: mf.Manifest,
    placements: Sequence[Placement],
    read: Read,
    *,
    card: str | None,
    lexicon: Lexicon,
) -> CheckReport:
    """distill-check over ``read`` (the disk, or a stage folder) for run ``m`` (§5).

    Without ``card``, every card of the run with at least one copy in ``AGENTS.md``; with
    it, only that card (one that is not of the run: ``card_unknown``, code 1). Raises
    ``ReorgError`` with code 2 for an unreadable or invalid ``pins.json`` / ``ratchet.log``
    and for an ``AGENTS.md`` that is not UTF-8.
    """
    with_card = [u for u in m.units if u.card_id is not None and u.dest == "L4"]
    if card is not None and card not in {u.card_id for u in with_card}:
        raise ReorgError([Issue(code=EXIT_USAGE, key="distill.card_unknown", detail=card)])
    pins, events = _controls(read)
    ctx = _Ctx(m, placements, read, lexicon, pins, events)
    if _decode_all(ctx.lines(AGENTS) or ()) is None:
        raise ReorgError([Issue(code=EXIT_INTERNAL, key="not_utf8", dest=AGENTS)])
    copies = [p for p in placements if p.role == "copy" and p.dest == AGENTS]
    by_unit: dict[str, list[Placement]] = {}
    for p in copies:
        by_unit.setdefault(p.unit or "", []).append(p)
    cards: list[CardCheck] = []
    for u in with_card:
        cid = u.card_id or ""
        if card is None and not by_unit.get(u.id):
            continue
        if card is not None and cid != card:
            continue
        cards.append(_check_card(ctx, u, cid, by_unit.get(u.id, [])))
    card_units = {u.id for u in with_card}
    units = {u.id: u for u in m.units}
    agents = ctx.lines(AGENTS) or ()
    no_card: list[NoCardCopy] = []
    for p in sorted(copies, key=lambda x: x.at):
        if p.unit in card_units:
            continue
        owner = units.get(p.unit or "")
        pin = ctx.copy_pin(p)
        n = p.at[1] - p.at[0] + 1
        start = ratchet.find_window(agents, p.sha256, n)
        nbytes = None if start is None else _nbytes(agents[start - 1 : start - 1 + n])
        no_card.append(
            NoCardCopy(
                block=p.block or "",
                pin=None if pin is None else pin.id,
                unit=p.unit or "",
                dest="?" if owner is None else owner.dest,
                bytes=nbytes,
            )
        )
    code = resolve_code(r.code for c in cards for r in c.reasons)
    return CheckReport(
        reorg=m.id,
        lang=m.lang,
        paths_lang=m.paths_lang,
        code=code,
        cards=tuple(cards),
        no_card=tuple(no_card),
    )


def _placements(root: Path, run: Path) -> tuple[Placement, ...]:
    rel = (run / PLACEMENT_NAME).relative_to(root).as_posix()
    raw = disk_reader(root)(rel)
    if raw is None:
        raise ReorgError([Issue(code=EXIT_USAGE, key="run_missing", detail=PLACEMENT_NAME)])
    try:
        return mf.placements_loads(raw)
    except mf.ManifestError as exc:
        raise ReorgError([exc.issue]) from None


def check(root: Path, reorg_id: str, *, card: str | None, lexicon: Lexicon) -> CheckReport:
    """``declutter reorg distill-check`` over the project folder (§5). Writes nothing.

    Code 1 (``ReorgError``) without the local run folder or with a journal that is not
    ``written``/``committed``; see :func:`check_run` for the rest.
    """
    m, run = mf.load_run(root, reorg_id)
    j = read_journal(run)
    if j is None or j.state not in CHECKABLE_JOURNAL:
        state = "planned" if j is None else j.state
        raise ReorgError([Issue(code=EXIT_USAGE, key="distill.not_applied", detail=state)])
    placements = _placements(root, run)
    return check_run(m, placements, disk_reader(root), card=card, lexicon=lexicon)


# --------------------------------------------------------------------------- output


def report_obj(rep: CheckReport) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "reorg": rep.reorg,
        "lang": rep.lang,
        "paths_lang": rep.paths_lang,
        "code": rep.code,
        "cards": [c.to_obj() for c in rep.cards],
        "no_card": [n.to_obj() for n in rep.no_card],
        "totals": rep.totals(),
    }


def report_json(rep: CheckReport) -> bytes:
    """``--json`` (``declutter.distill-check/1``), deterministic like the manifest."""
    text = json.dumps(report_obj(rep), ensure_ascii=False, sort_keys=True, indent=2)
    return (text + "\n").encode("utf-8")


def _reason_text(r: Reason, lang: str) -> str:
    parts = [r.key]
    label = t(f"distill.reason.{r.key}", lang)
    if label != f"distill.reason.{r.key}":
        parts.append(f"({label})")
    if r.cls:
        parts.append(r.cls)
    if r.block:
        parts.append(r.block)
    if r.pin:
        parts.append(r.pin)
    if r.section:
        parts.append(r.section)
    if r.path:
        parts.append(r.path + (f":{r.line}" if r.line is not None else ""))
    if r.col is not None:
        parts.append(f"col {r.col}")
    if r.length is not None:
        parts.append(f"len {r.length}")
    parts += [f"{c} {a}≠{b}" for c, a, b in r.counts]
    if r.detail:
        parts.append(r.detail)
    return " ".join(parts)


def report_lines(rep: CheckReport, lang: str) -> list[str]:
    """Text output (G6): one line per card and per copy, then the totals."""
    out: list[str] = []
    for c in rep.cards:
        head = f"{c.card_id} {c.unit} {c.state} [{t(f'distill.state.{c.state}', lang)}]"
        if c.reasons:
            head += ": " + "; ".join(_reason_text(r, lang) for r in c.reasons)
        out.append(head)
        for w in c.warnings:
            out.append(f"  {t('distill.warning', lang)}: {_reason_text(w, lang)}")
        for cp in c.copies:
            size = "?" if cp.bytes is None else str(cp.bytes)
            out.append(
                f"  {cp.block} {cp.pin or '-'} {cp.source}:{cp.src[0]}-{cp.src[1]} "
                f"{cp.lines} {t('unit.lines', lang)} {size} B {', '.join(cp.categories)}"
            )
    for n in rep.no_card:
        size = "?" if n.bytes is None else str(n.bytes)
        out.append(f"{n.block} {n.pin or '-'} no_card:{n.dest} {size} B")
    tot = rep.totals()
    out.append(t("distill.totals", lang, **tot))
    return out
