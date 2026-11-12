"""Exact line model, bounded CommonMark parser, units and blocks (spec §4.2-§4.7).

Bytes are the truth: a source is split on ``b"\\n"`` only (never ``str.splitlines()``), a
``\\r`` stays inside its line, and every comparison and hash is made over the raw bytes of
each line. Structure (fences, HTML block comments, front matter, ``##`` headings) is
recognized on a decoded copy of each line with one trailing ``\\r`` removed.

Errors carry metadata only (reason, line number, byte offset, path); never file content.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..model import Block, SourceDoc, Unit

H2_RE = re.compile(r"^##(?!#)\s*(.*)$")  # same expression as phase-1 S4 (spec §4.4)
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
COMMENT_OPEN_RE = re.compile(r"^ {0,3}<!--")
HEADING3_RE = re.compile(r"^#{3,}(?:[ \t]|$)")
ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s")

_BOM = b"\xef\xbb\xbf"

SourceErrorReason = Literal[
    "not_utf8", "nul_byte", "open_fence", "open_comment", "open_front_matter",
    "front_matter_conflict",
]  # fmt: skip

LineKind = Literal[
    "text", "blank", "h2", "fence_open", "fence_body", "fence_close",
    "comment_open", "comment_body", "comment_close", "comment_single",
    "fm_open", "fm_body", "fm_close",
]  # fmt: skip

OpenKind = Literal["fence", "comment", "front_matter"]

_OPEN_REASON: dict[OpenKind, SourceErrorReason] = {
    "fence": "open_fence",
    "comment": "open_comment",
    "front_matter": "open_front_matter",
}


class SourceError(Exception):
    """A source (or a file to extend) that cannot be parsed safely: exit code 2.

    ``line`` is the opening line for ``open_*``; ``offset`` is the byte offset for
    ``not_utf8``; ``paths`` lists every source involved in ``front_matter_conflict``.
    """

    def __init__(
        self,
        reason: SourceErrorReason,
        *,
        path: str = "",
        line: int | None = None,
        offset: int | None = None,
        paths: tuple[str, ...] = (),
    ) -> None:
        self.reason: SourceErrorReason = reason
        self.path = path
        self.line = line
        self.offset = offset
        self.paths = paths
        where = ""
        if line is not None:
            where = f" at line {line}"
        elif offset is not None:
            where = f" at byte offset {offset}"
        who = ", ".join(paths) if paths else path
        super().__init__(f"{reason}{where}" + (f" in {who}" if who else ""))


@dataclass(frozen=True)
class SplitResult:
    lines: tuple[bytes, ...]
    bom: bool
    final_newline: bool
    eol: str  # "lf" | "crlf" | "mixed" | "none"


# --------------------------------------------------------------------------- line model


def split_lines(raw: bytes) -> SplitResult:
    """Split ``raw`` under the exact line model of spec §4.2."""
    bom = raw.startswith(_BOM)
    body = raw[len(_BOM) :] if bom else raw
    if body == b"":
        return SplitResult((), bom, False, "none")
    parts = body.split(b"\n")
    final_newline = body.endswith(b"\n")
    if final_newline:
        parts = parts[:-1]
    lines = tuple(parts)
    if b"\n" not in body:
        eol = "none"
    else:
        followed = lines if final_newline else lines[:-1]
        crs = [ln.endswith(b"\r") for ln in followed]
        eol = "lf" if not any(crs) else "crlf" if all(crs) else "mixed"
    return SplitResult(lines, bom, final_newline, eol)


def decode_line(line: bytes) -> str:
    """Strict UTF-8 decode of one line with one trailing ``\\r`` removed (structure only)."""
    text = line.decode("utf-8")
    return text[:-1] if text.endswith("\r") else text


def _strip_cr(text: str) -> str:
    return text[:-1] if text.endswith("\r") else text


# --------------------------------------------------------------------------- scanner


def _scan(
    lines: Sequence[str], *, front_matter: bool
) -> tuple[tuple[LineKind, ...], tuple[OpenKind, int] | None]:
    """Classify each line (already without its trailing ``\\r``); report what stays open."""
    kinds: list[LineKind] = []
    state: OpenKind | None = None
    open_line = 0
    close_re: re.Pattern[str] | None = None
    for i, s in enumerate(lines, 1):
        kind: LineKind
        if state == "fence":
            assert close_re is not None
            if close_re.match(s):
                kind = "fence_close"
                state = None
            else:
                kind = "fence_body"
        elif state == "comment":
            if "-->" in s:
                kind = "comment_close"
                state = None
            else:
                kind = "comment_body"
        elif state == "front_matter":
            if s in ("---", "..."):
                kind = "fm_close"
                state = None
            else:
                kind = "fm_body"
        elif front_matter and i == 1 and s == "---":
            kind = "fm_open"
            state = "front_matter"
            open_line = i
        elif (m := FENCE_OPEN_RE.match(s)) and not (m[1][0] == "`" and "`" in m[2]):
            kind = "fence_open"
            state = "fence"
            close_re = re.compile(rf"^ {{0,3}}{re.escape(m[1][0])}{{{len(m[1])},}} *$")
            open_line = i
        elif COMMENT_OPEN_RE.match(s):
            rest = s[s.index("<!--") + 4 :]
            if "-->" in rest:
                kind = "comment_single"
            else:
                kind = "comment_open"
                state = "comment"
                open_line = i
        elif s.strip(" \t") == "":
            kind = "blank"
        elif H2_RE.match(s):
            kind = "h2"
        else:
            kind = "text"
        kinds.append(kind)
    return tuple(kinds), (None if state is None else (state, open_line))


def scan(lines: Sequence[str], *, strict: bool, front_matter: bool = True) -> tuple[LineKind, ...]:
    """Line kinds under spec §4.4. Lines must already be decoded without a trailing ``\\r``.

    ``strict`` raises :class:`SourceError` (``open_*`` with the opening line) when a fence,
    comment or front matter is still open at the end; otherwise it is closed implicitly.
    ``front_matter=False`` disables front matter recognition on line 1 (for fragments).
    """
    kinds, still_open = _scan(lines, front_matter=front_matter)
    if strict and still_open is not None:
        raise SourceError(_OPEN_REASON[still_open[0]], line=still_open[1])
    return kinds


# --------------------------------------------------------------------------- sources


def load_source(path: str, raw: bytes) -> SourceDoc:
    """Validate (no NUL, strict UTF-8) and split ``raw``; the sha256 is of the raw bytes."""
    if b"\x00" in raw:
        raise SourceError("nul_byte", path=path)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError("not_utf8", path=path, offset=exc.start) from None
    split = split_lines(raw)
    return SourceDoc(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        lines=split.lines,
        bom=split.bom,
        final_newline=split.final_newline,
        eol=split.eol,
    )


def _decoded(doc: SourceDoc) -> list[str]:
    return [decode_line(ln) for ln in doc.lines]


def _strict_kinds(doc: SourceDoc) -> tuple[list[str], tuple[LineKind, ...]]:
    texts = _decoded(doc)
    try:
        kinds = scan(texts, strict=True)
    except SourceError as exc:
        raise SourceError(exc.reason, path=doc.path, line=exc.line) from None
    return texts, kinds


def parse(raw: bytes, path: str = "") -> SourceDoc:
    """``load_source`` plus a strict structural scan (open structures -> SourceError)."""
    doc = load_source(path, raw)
    _strict_kinds(doc)
    return doc


# --------------------------------------------------------------------------- hashes and keys


def heading_key(heading: str) -> str:
    return hashlib.sha256(unicodedata.normalize("NFC", heading).casefold().encode()).hexdigest()[
        :12
    ]


def lines_sha(lines: Sequence[bytes]) -> str:
    return hashlib.sha256(b"\n".join(lines)).hexdigest()


def sha12(lines: Sequence[bytes]) -> str:
    return lines_sha(lines)[:12]


def _suffixed(base: str, seen: dict[str, int]) -> str:
    n = seen.get(base, 0) + 1
    seen[base] = n
    return base if n == 1 else f"{base}~{n}"


# --------------------------------------------------------------------------- blocks


def _block_spans(
    texts: Sequence[str], kinds: Sequence[LineKind], first: int, last: int
) -> list[tuple[str, int, int]]:
    """Blocks (kind, start, end), 1-based, over lines ``first..last`` (spec §4.6)."""

    def closing(i: int, close: tuple[LineKind, ...]) -> int:
        # From an opening line at i, the line that closes it (the same line for singles).
        j = i
        while j < last and kinds[j - 1] not in close:
            j += 1
        return j

    def starts_other(i: int) -> bool:
        s = texts[i - 1]
        k = kinds[i - 1]
        return (
            k != "text"
            or s.lstrip().startswith("|")
            or HEADING3_RE.match(s) is not None
            or ITEM_RE.match(s) is not None
        )

    out: list[tuple[str, int, int]] = []
    i = first
    while i <= last:
        k = kinds[i - 1]
        s = texts[i - 1]
        if k == "blank":
            i += 1
            continue
        if k == "fm_open":
            end = closing(i + 1, ("fm_close",)) if i < last else i
            out.append(("front_matter", i, end))
        elif k == "fence_open":
            end = closing(i + 1, ("fence_close",)) if i < last else i
            out.append(("fence", i, end))
        elif k in ("comment_open", "comment_single"):
            end = i if k == "comment_single" else closing(i, ("comment_close",))
            out.append(("comment", i, end))
        elif k == "text" and s.lstrip().startswith("|"):
            end = i
            while end < last and kinds[end] == "text" and texts[end].lstrip().startswith("|"):
                end += 1
            out.append(("table", i, end))
        elif k == "text" and HEADING3_RE.match(s):
            end = i
            out.append(("heading", i, end))
        elif k == "text" and ITEM_RE.match(s):
            end = i
            while end < last:
                nxt = end + 1
                nk = kinds[nxt - 1]
                ns = texts[nxt - 1]
                if nk == "blank" or ns[:1] not in (" ", "\t"):
                    break
                if nk == "fence_open":
                    end = closing(nxt + 1, ("fence_close",)) if nxt < last else nxt
                elif nk == "comment_open":
                    end = closing(nxt, ("comment_close",))
                else:
                    end = nxt
            out.append(("item", i, end))
        else:
            end = i
            while end < last and kinds[end] != "blank" and not starts_other(end + 1):
                end += 1
            out.append(("paragraph", i, end))
        i = end + 1
    return out


def _make_blocks(
    doc: SourceDoc,
    texts: Sequence[str],
    kinds: Sequence[LineKind],
    unit_id: str,
    unit_key: str,
    first: int,
    last: int,
) -> tuple[Block, ...]:
    seen: dict[str, int] = {}
    blocks: list[Block] = []
    for n, (kind, start, end) in enumerate(_block_spans(texts, kinds, first, last), 1):
        base = f"{unit_key}:{sha12(doc.lines[start - 1 : end])}"
        blocks.append(
            Block(
                id=f"{unit_id}.b{n}",
                kind=kind,
                start=start,
                end=end,
                key=_suffixed(base, seen),
            )
        )
    return tuple(blocks)


# --------------------------------------------------------------------------- units


def _preamble_suffix(source_index: int) -> str:
    return "" if source_index == 0 else chr(ord("a") + source_index)


def _unit_id(number: int, width: int) -> str:
    return f"U{number:0{width}d}"


def _units_of(
    doc: SourceDoc,
    texts: Sequence[str],
    kinds: Sequence[LineKind],
    *,
    first_number: int,
    preamble_id: str,
    width: int,
    seen_keys: dict[str, int],
) -> tuple[Unit, ...]:
    n_lines = len(doc.lines)
    heads = [i for i, k in enumerate(kinds, 1) if k == "h2"]
    units: list[Unit] = []
    if n_lines and (not heads or heads[0] > 1):
        last = heads[0] - 1 if heads else n_lines
        units.append(
            Unit(
                id=preamble_id,
                source=doc.path,
                heading="",
                heading_key=preamble_id,
                start=1,
                end=last,
                blocks=_make_blocks(doc, texts, kinds, preamble_id, preamble_id, 1, last),
            )
        )
    for idx, start in enumerate(heads):
        end = heads[idx + 1] - 1 if idx + 1 < len(heads) else n_lines
        m = H2_RE.match(texts[start - 1])
        assert m is not None
        heading = m.group(1).strip()
        uid = _unit_id(first_number + idx, width)
        key = _suffixed(heading_key(heading), seen_keys)
        units.append(
            Unit(
                id=uid,
                source=doc.path,
                heading=heading,
                heading_key=key,
                start=start,
                end=end,
                blocks=_make_blocks(doc, texts, kinds, uid, key, start + 1, end),
            )
        )
    return tuple(units)


def parse_units(
    doc: SourceDoc, *, first_number: int, preamble_id: str, width: int
) -> tuple[Unit, ...]:
    """Units and blocks of one source (heading-key suffixes counted within this source)."""
    texts, kinds = _strict_kinds(doc)
    return _units_of(
        doc,
        texts,
        kinds,
        first_number=first_number,
        preamble_id=preamble_id,
        width=width,
        seen_keys={},
    )


def parse_sources(docs: Sequence[SourceDoc]) -> tuple[Unit, ...]:
    """Units of every source in order: preamble ids ``U00``, ``U00b``…, continuous ``##``
    numbering, width 3 when there are more than 99 ``##`` units, ``~N`` heading-key
    suffixes in source order, and ``front_matter_conflict`` when >1 source has one."""
    scanned = [_strict_kinds(doc) for doc in docs]
    with_fm = tuple(
        doc.path for doc, (_, kinds) in zip(docs, scanned, strict=True) if kinds[:1] == ("fm_open",)
    )
    if len(with_fm) > 1:
        raise SourceError("front_matter_conflict", path=", ".join(with_fm), paths=with_fm)
    total = sum(kinds.count("h2") for _, kinds in scanned)
    width = 3 if total > 99 else 2
    seen_keys: dict[str, int] = {}
    units: list[Unit] = []
    number = 1
    for index, (doc, (texts, kinds)) in enumerate(zip(docs, scanned, strict=True)):
        preamble_id = _unit_id(0, width) + _preamble_suffix(index)
        units.extend(
            _units_of(
                doc,
                texts,
                kinds,
                first_number=number,
                preamble_id=preamble_id,
                width=width,
                seen_keys=seen_keys,
            )
        )
        number += kinds.count("h2")
    return tuple(units)


# --------------------------------------------------------------------------- copy structure


def _heading_level(text: str) -> int:
    return len(text) - len(text.lstrip("#"))


def subsection_range(doc: SourceDoc, unit: Unit, block: Block) -> tuple[int, int]:
    """Lines of the subsection opened by a ``heading`` block: up to the line before the next
    heading block of the same or a higher level inside the unit, or the end of the unit."""
    if block.kind != "heading":
        raise ValueError("subsection_range needs a heading block")
    level = _heading_level(decode_line(doc.lines[block.start - 1]))
    for other in unit.blocks:
        if (
            other.kind == "heading"
            and other.start > block.start
            and _heading_level(decode_line(doc.lines[other.start - 1])) <= level
        ):
            return block.start, other.start - 1
    return block.start, unit.end


def list_group_range(unit: Unit, block: Block, doc: SourceDoc) -> tuple[int, int] | None:
    """A paragraph whose last line ends with ``:`` plus the following items (at most one
    blank line before the first item and between items); ``None`` if it forms no group."""
    if block.kind != "paragraph":
        return None
    if not decode_line(doc.lines[block.end - 1]).rstrip(" \t").endswith(":"):
        return None
    blocks = list(unit.blocks)
    idx = next(i for i, b in enumerate(blocks) if b.id == block.id)
    prev_end = block.end
    last_item: Block | None = None
    for nxt in blocks[idx + 1 :]:
        if nxt.kind != "item" or nxt.start - prev_end - 1 > 1:
            break
        last_item = nxt
        prev_end = nxt.end
    if last_item is None:
        return None
    return block.start, last_item.end


def is_balanced(lines: Sequence[bytes], *, front_matter: bool = True) -> bool:
    """True when a scan that starts outside every structure also ends outside (spec §4.6).

    ``front_matter=False`` treats a leading ``---`` as text (for fragments not at line 1).
    Lines that are not valid UTF-8 are never balanced.
    """
    try:
        texts = [decode_line(ln) for ln in lines]
    except UnicodeDecodeError:
        return False
    return _scan(texts, front_matter=front_matter)[1] is None


def open_structure_at_end(raw: bytes) -> tuple[str, int] | None:
    """For a file to extend (spec §4.3): ``(kind, opening line)`` of a fence, comment or
    front matter still open at its end, or ``None``. Raises SourceError on NUL/non-UTF-8."""
    doc = load_source("", raw)
    return _scan(_decoded(doc), front_matter=True)[1]


def level2_headings(text: str) -> list[str]:
    """Level-2 heading texts under spec §4.4, tolerant of open structures (used by S4).

    ``text`` is split on ``"\\n"`` only; one trailing ``"\\r"`` per line is ignored.
    """
    texts = [_strip_cr(s) for s in text.split("\n")]
    kinds = scan(texts, strict=False)
    out: list[str] = []
    for s, k in zip(texts, kinds, strict=True):
        if k == "h2":
            m = H2_RE.match(s)
            assert m is not None
            out.append(m.group(1))
    return out
