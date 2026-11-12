"""mdblocks: exact line model, bounded CommonMark parser, units and blocks (spec §4).

Synthetic bytes only. Exact bytes (CRLF, BOM, no final newline, NUL) are built as
``bytes`` literals, never through text I/O.
"""

from __future__ import annotations

import hashlib
import unicodedata

import pytest

from declutter_context.model import Block, SourceDoc, Unit
from declutter_context.reorg import mdblocks
from declutter_context.reorg.mdblocks import SourceError

MARKER = "ZQX-SEED-7731"


def _doc(raw: bytes, path: str = "CLAUDE.md") -> SourceDoc:
    return mdblocks.parse(raw, path)


def _units(*raws: bytes) -> tuple[Unit, ...]:
    names = ["CLAUDE.md", "AGENTS.md", "extra.md", "more.md"]
    return mdblocks.parse_sources([_doc(r, names[i]) for i, r in enumerate(raws)])


def _h2_units(units: tuple[Unit, ...]) -> list[str]:
    return [u.heading for u in units if u.heading]


def _kinds(unit: Unit) -> list[tuple[str, int, int]]:
    return [(b.kind, b.start, b.end) for b in unit.blocks]


# --------------------------------------------------------------------------- line model


@pytest.mark.parametrize(
    ("raw", "lines", "final_newline", "eol"),
    [
        (b"a\nb", (b"a", b"b"), False, "lf"),
        (b"a\n", (b"a",), True, "lf"),
        (b"\n", (b"",), True, "lf"),
        (b"a\r\n", (b"a\r",), True, "crlf"),
        (b"a", (b"a",), False, "none"),
        (b"a\r\nb\r", (b"a\r", b"b\r"), False, "crlf"),
        (b"", (), False, "none"),
    ],
)
def test_split_lines_spec_examples(
    raw: bytes, lines: tuple[bytes, ...], final_newline: bool, eol: str
) -> None:
    r = mdblocks.split_lines(raw)
    assert (r.lines, r.final_newline, r.eol, r.bom) == (lines, final_newline, eol, False)


def test_last_line_cr_without_newline_does_not_count() -> None:
    assert mdblocks.split_lines(b"a\nb\r").eol == "lf"


def test_lone_cr_kept_in_line() -> None:
    assert mdblocks.split_lines(b"a\rb\n").lines == (b"a\rb",)


def test_mixed_eol() -> None:
    assert mdblocks.split_lines(b"a\r\nb\n").eol == "mixed"


def test_bom_stripped_and_recorded() -> None:
    r = mdblocks.split_lines(b"\xef\xbb\xbfa\nb\n")
    assert r.bom is True
    assert r.lines == (b"a", b"b")
    only_bom = mdblocks.split_lines(b"\xef\xbb\xbf")
    assert (only_bom.bom, only_bom.lines, only_bom.final_newline) == (True, (), False)


def test_single_newline_file() -> None:
    r = mdblocks.split_lines(b"\n")
    assert (r.lines, r.final_newline) == ((b"",), True)


def test_decode_line_strips_only_one_cr() -> None:
    assert mdblocks.decode_line(b"## a\r") == "## a"
    assert mdblocks.decode_line("café".encode()) == "café"


# --------------------------------------------------------------------------- validation


def test_load_source_records_raw_sha_and_size() -> None:
    raw = b"\xef\xbb\xbf# t\r\nx"
    doc = mdblocks.load_source("AGENTS.md", raw)
    assert doc.path == "AGENTS.md"
    assert doc.sha256 == hashlib.sha256(raw).hexdigest()
    assert doc.bytes == len(raw)
    assert (doc.bom, doc.final_newline, doc.eol) == (True, False, "crlf")
    assert doc.lines == (b"# t\r", b"x")


def test_load_source_not_utf8_reports_offset() -> None:
    with pytest.raises(SourceError) as ei:
        mdblocks.load_source("CLAUDE.md", b"ok\n\xff")
    err = ei.value
    assert (err.reason, err.offset, err.line, err.path) == ("not_utf8", 3, None, "CLAUDE.md")
    raw = MARKER.encode() + b"\n\xfe more " + MARKER.encode()
    with pytest.raises(SourceError) as ei2:
        mdblocks.load_source("CLAUDE.md", raw)
    assert ei2.value.offset == len(MARKER) + 1
    assert MARKER not in str(ei2.value)
    assert MARKER not in repr(ei2.value)


def test_load_source_nul_byte() -> None:
    raw = MARKER.encode() + b"\x00x\n"
    with pytest.raises(SourceError) as ei:
        mdblocks.load_source("CLAUDE.md", raw)
    assert ei.value.reason == "nul_byte"
    assert MARKER not in str(ei.value)


# --------------------------------------------------------------------------- parser


def test_h2_inside_fence_is_not_a_unit() -> None:
    units = _units(b"```\n## no\n```\n## yes\n")
    assert _h2_units(units) == ["yes"]
    assert (units[0].id, units[0].start, units[0].end) == ("U00", 1, 3)


def test_four_backtick_fence() -> None:
    units = _units(b"````\n```\n## no\n````\n## yes\n")
    assert _h2_units(units) == ["yes"]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_fence_indented_1_to_3_spaces(n: int) -> None:
    pad = b" " * n
    units = _units(pad + b"```\n## no\n" + pad + b"```\n## yes\n")
    assert _h2_units(units) == ["yes"]


def test_fence_indented_4_spaces_is_not_fence() -> None:
    units = _units(b"    ```\n## yes\n")
    assert _h2_units(units) == ["yes"]


def test_tilde_fence_with_backticks_inside_not_closed() -> None:
    units = _units(b"~~~\n```\n## no\n```\n~~~\n## yes\n")
    assert _h2_units(units) == ["yes"]
    assert _kinds(units[0]) == [("fence", 1, 5)]


def test_longer_close_closes_fence() -> None:
    assert _h2_units(_units(b"```\n## no\n`````\n## yes\n")) == ["yes"]


def test_shorter_close_does_not_close() -> None:
    raw = b"````\n```\n## no\n````\n"
    assert _h2_units(_units(raw)) == []
    assert mdblocks.level2_headings(raw.decode()) == []


def test_close_accepts_trailing_spaces_only() -> None:
    assert _h2_units(_units(b"```\nx\n```   \n## yes\n")) == ["yes"]
    with pytest.raises(SourceError) as ei:
        _units(b"```\nx\n``` y\n## no\n")
    assert (ei.value.reason, ei.value.line) == ("open_fence", 1)


def test_inline_triple_backticks_in_prose_do_not_open_fence() -> None:
    units = _units(b"```npm run dev``` arranca\n## Datos de pacientes\nx\n")
    assert [(u.id, u.start, u.end) for u in units] == [("U00", 1, 1), ("U01", 2, 3)]
    assert units[1].heading == "Datos de pacientes"


def test_inline_comment_mid_paragraph_does_not_open_comment() -> None:
    units = _units(b"escribe <!-- para\n## X\n")
    assert _h2_units(units) == ["X"]


def test_comment_inside_fence_fence_wins() -> None:
    units = _units(b"```\n<!--\n```\n## X\n")
    assert _h2_units(units) == ["X"]
    assert _kinds(units[0]) == [("fence", 1, 3)]


def test_multiline_block_comment_hides_h2() -> None:
    units = _units(b"<!--\n## hidden\n-->\n## real\n")
    assert _h2_units(units) == ["real"]
    assert _kinds(units[0]) == [("comment", 1, 3)]


def test_single_line_comment_block() -> None:
    units = _units(b"<!-- a --> b\ntext\n")
    assert _kinds(units[0]) == [("comment", 1, 1), ("paragraph", 2, 2)]


def test_front_matter_block_and_only_line_1() -> None:
    units = _units(b"---\n## no\n---\nbody\n## yes\n")
    assert _h2_units(units) == ["yes"]
    assert _kinds(units[0]) == [("front_matter", 1, 3), ("paragraph", 4, 4)]
    later = _units(b"a\n---\n## yes\n---\n")
    assert _h2_units(later) == ["yes"]
    dots = _units(b"---\nk: v\n...\n")
    assert _kinds(dots[0]) == [("front_matter", 1, 3)]


def test_crlf_source_fences_and_headings() -> None:
    units = _units(b"```\r\n## in\r\n```\r\n## Out\r\nx\r\n")
    assert _h2_units(units) == ["Out"]
    assert (units[0].start, units[0].end) == (1, 3)
    assert (units[1].start, units[1].end) == (4, 5)


def test_u2028_and_x85_do_not_split_lines() -> None:
    raw = "a\u2028b\x85c\n## H\n".encode()
    doc = _doc(raw)
    assert len(doc.lines) == 2
    units = mdblocks.parse_sources([doc])
    assert [(u.start, u.end) for u in units] == [(1, 1), (2, 2)]


def test_table_block() -> None:
    units = _units(b"| a | b |\n|---|---|\n  | c |\ntext\n")
    assert _kinds(units[0]) == [("table", 1, 3), ("paragraph", 4, 4)]


def test_heading3_blocks_and_paragraph_runs() -> None:
    units = _units(b"## U\n### A\npara one\npara two\n\n#### B\n# h1 is prose\n")
    assert _kinds(units[0]) == [
        ("heading", 2, 2),
        ("paragraph", 3, 4),
        ("heading", 6, 6),
        ("paragraph", 7, 7),
    ]


def test_item_with_continuations_and_subitems() -> None:
    units = _units(b"- a\n  cont\n  - sub\n\t tab\n- b\n\n1. c\n2) d\nafter\n")
    assert _kinds(units[0]) == [
        ("item", 1, 4),
        ("item", 5, 5),
        ("item", 7, 7),
        ("item", 8, 8),
        ("paragraph", 9, 9),
    ]


def test_item_continuation_fence_with_blank_line_inside() -> None:
    units = _units(b"- a\n  ```\n  x\n\n  ```\n  after\nnext para\n")
    assert _kinds(units[0]) == [("item", 1, 6), ("paragraph", 7, 7)]


def test_item_continuation_comment_with_blank_line_inside() -> None:
    units = _units(b"- a\n  <!--\n\n  -->\nnext\n")
    assert _kinds(units[0]) == [("item", 1, 4), ("paragraph", 5, 5)]


@pytest.mark.parametrize(
    ("raw", "reason", "line"),
    [
        (b"x\n```\ncode\n", "open_fence", 2),
        (b"a\n<!--\nb\n", "open_comment", 2),
        (b"---\na\n", "open_front_matter", 1),
    ],
)
def test_unclosed_structures_rejected(raw: bytes, reason: str, line: int) -> None:
    with pytest.raises(SourceError) as ei:
        mdblocks.parse(raw, "CLAUDE.md")
    assert (ei.value.reason, ei.value.line, ei.value.path) == (reason, line, "CLAUDE.md")
    with pytest.raises(SourceError):
        mdblocks.scan([mdblocks.decode_line(x) for x in raw.split(b"\n")[:-1]], strict=True)
    tolerant = mdblocks.scan([mdblocks.decode_line(x) for x in raw.split(b"\n")[:-1]], strict=False)
    assert len(tolerant) == raw.count(b"\n")


def test_scan_line_kinds() -> None:
    lines = ["---", "k: v", "---", "", "## T", "<!-- a -->", "<!--", "b", "-->", "```", "x", "```"]
    assert mdblocks.scan(lines, strict=True) == (
        "fm_open",
        "fm_body",
        "fm_close",
        "blank",
        "h2",
        "comment_single",
        "comment_open",
        "comment_body",
        "comment_close",
        "fence_open",
        "fence_body",
        "fence_close",
    )


# --------------------------------------------------------------------------- units and keys


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\n\n",
        b"intro\n\n## A\nx\n## B\n",
        b"## A\n## B\n\n",
        b"p\r\n## A\r\n```\r\n## no\r\n```\r\n## C",
        b"---\nk: v\n---\n## A\n- x\n  y\n| t |\n",
    ],
)
def test_units_cover_all_lines_without_gaps(raw: bytes) -> None:
    doc = _doc(raw)
    units = mdblocks.parse_sources([doc])
    covered = [n for u in units for n in range(u.start, u.end + 1)]
    assert covered == list(range(1, len(doc.lines) + 1))
    for u in units:
        for b in u.blocks:
            assert u.start <= b.start <= b.end <= u.end
            if u.heading:
                assert b.start > u.start


def test_no_h2_everything_is_U00() -> None:
    units = _units(b"a\n\nb\n")
    assert [(u.id, u.heading, u.heading_key, u.start, u.end) for u in units] == [
        ("U00", "", "U00", 1, 3)
    ]
    assert _kinds(units[0]) == [("paragraph", 1, 1), ("paragraph", 3, 3)]


def test_empty_source_has_no_units() -> None:
    assert _units(b"") == ()


def test_preamble_ids_across_sources() -> None:
    units = _units(b"p\n## A\n", b"p\n## B\n", b"p\n## C\n")
    assert [(u.id, u.source) for u in units] == [
        ("U00", "CLAUDE.md"),
        ("U01", "CLAUDE.md"),
        ("U00b", "AGENTS.md"),
        ("U02", "AGENTS.md"),
        ("U00c", "extra.md"),
        ("U03", "extra.md"),
    ]
    no_first_preamble = _units(b"## A\n", b"p\n## B\n")
    assert [u.id for u in no_first_preamble] == ["U01", "U00b", "U02"]


def test_three_digit_ids_over_99_units() -> None:
    raw = b"intro\n" + b"".join(f"## H{i}\n".encode() for i in range(100))
    ids = [u.id for u in _units(raw)]
    assert ids[0] == "U000"
    assert ids[1] == "U001"
    assert ids[-1] == "U100"
    assert len(ids) == 101
    raw99 = b"intro\n" + b"".join(f"## H{i}\n".encode() for i in range(99))
    ids99 = [u.id for u in _units(raw99)]
    assert (ids99[0], ids99[1], ids99[-1]) == ("U00", "U01", "U99")


def test_width_counts_units_across_sources() -> None:
    a = b"".join(f"## A{i}\n".encode() for i in range(60))
    b = b"p\n" + b"".join(f"## B{i}\n".encode() for i in range(40))
    ids = [u.id for u in _units(a, b)]
    assert ids[0] == "U001"
    assert "U000b" in ids
    assert ids[-1] == "U100"


def test_heading_key_nfc_casefold_and_duplicate_suffix() -> None:
    nfc = unicodedata.normalize("NFC", "Café Ñandú")
    nfd = unicodedata.normalize("NFD", "Café Ñandú")
    expected = hashlib.sha256(nfc.casefold().encode()).hexdigest()[:12]
    assert mdblocks.heading_key(nfc) == expected
    assert mdblocks.heading_key(nfd) == expected
    assert mdblocks.heading_key("NOTAS") == mdblocks.heading_key("notas")
    units = _units(b"## Notas\n##   notas  \n", b"## NOTAS\n## Otra\n")
    base = mdblocks.heading_key("notas")
    assert [u.heading_key for u in units] == [
        base,
        base + "~2",
        base + "~3",
        mdblocks.heading_key("Otra"),
    ]
    assert units[1].heading == "notas"


def test_block_ids_and_keys() -> None:
    units = _units(b"## A\n- x\n\npara\n")
    u = units[0]
    assert [b.id for b in u.blocks] == ["U01.b1", "U01.b2"]
    assert u.blocks[0].key == f"{u.heading_key}:{mdblocks.sha12([b'- x'])}"


def test_block_key_duplicate_suffix_within_unit() -> None:
    u = _units(b"## A\n- x\n- x\n- x\n")[0]
    k = f"{u.heading_key}:{mdblocks.sha12([b'- x'])}"
    assert [b.key for b in u.blocks] == [k, k + "~2", k + "~3"]


def test_identical_blocks_in_different_units_have_distinct_keys() -> None:
    a, b = _units(b"## A\n- x\n## B\n- x\n")
    assert a.blocks[0].key != b.blocks[0].key


def test_block_key_uses_raw_bytes_with_cr() -> None:
    u = _units(b"## A\r\n- x\r\n")[0]
    assert u.blocks[0].key.endswith(mdblocks.sha12([b"- x\r"]))


def test_front_matter_in_two_sources_conflict() -> None:
    a = _doc(b"---\nk: v\n---\n## A\n", "CLAUDE.md")
    b = _doc(b"---\nk: w\n---\n## B\n", "AGENTS.md")
    with pytest.raises(SourceError) as ei:
        mdblocks.parse_sources([a, b])
    assert ei.value.reason == "front_matter_conflict"
    assert ei.value.paths == ("CLAUDE.md", "AGENTS.md")
    assert "k: v" not in str(ei.value)


def test_hashes() -> None:
    lines = [b"a", b"b\r"]
    full = hashlib.sha256(b"a\nb\r").hexdigest()
    assert mdblocks.lines_sha(lines) == full
    assert mdblocks.sha12(lines) == full[:12]


# --------------------------------------------------------------------------- copy structure


def _block(unit: Unit, start: int) -> Block:
    return next(b for b in unit.blocks if b.start == start)


def test_subsection_range_stops_at_same_or_higher_level() -> None:
    raw = b"## U\n### A\nx\n#### B\ny\n### C\nz\n## V\n"
    doc = _doc(raw)
    u = mdblocks.parse_sources([doc])[0]
    assert mdblocks.subsection_range(doc, u, _block(u, 2)) == (2, 5)
    assert mdblocks.subsection_range(doc, u, _block(u, 4)) == (4, 5)
    assert mdblocks.subsection_range(doc, u, _block(u, 6)) == (6, 7)
    with pytest.raises(ValueError):
        mdblocks.subsection_range(doc, u, _block(u, 3))


def test_subsection_range_ignores_hash_lines_inside_fence() -> None:
    raw = b"## U\n### A\n```\n### not a heading\n```\nx\n"
    doc = _doc(raw)
    u = mdblocks.parse_sources([doc])[0]
    assert mdblocks.subsection_range(doc, u, _block(u, 2)) == (2, 6)


def test_list_group_range() -> None:
    doc = _doc(b"## U\nReglas para datos:  \n- a\n\n- b\n  c\nfin\n")
    u = mdblocks.parse_sources([doc])[0]
    assert mdblocks.list_group_range(u, _block(u, 2), doc) == (2, 6)
    blank_first = _doc(b"## U\nReglas:\n\n- a\n- b\n")
    u2 = mdblocks.parse_sources([blank_first])[0]
    assert mdblocks.list_group_range(u2, _block(u2, 2), blank_first) == (2, 5)
    two_blanks = _doc(b"## U\nReglas:\n\n\n- a\n")
    u3 = mdblocks.parse_sources([two_blanks])[0]
    assert mdblocks.list_group_range(u3, _block(u3, 2), two_blanks) is None
    gap_between_items = _doc(b"## U\nReglas:\n- a\n\n\n- b\n")
    u4 = mdblocks.parse_sources([gap_between_items])[0]
    assert mdblocks.list_group_range(u4, _block(u4, 2), gap_between_items) == (2, 3)
    no_colon = _doc(b"## U\nReglas\n- a\n")
    u5 = mdblocks.parse_sources([no_colon])[0]
    assert mdblocks.list_group_range(u5, _block(u5, 2), no_colon) is None
    item = _block(u5, 3)
    assert mdblocks.list_group_range(u5, item, no_colon) is None


def test_is_balanced() -> None:
    assert mdblocks.is_balanced([b"```", b"x", b"```"])
    assert not mdblocks.is_balanced([b"```", b"x"])
    assert not mdblocks.is_balanced([b"<!-- a"])
    assert mdblocks.is_balanced([b"a <!-- b"])
    assert mdblocks.is_balanced([])
    assert mdblocks.is_balanced([b"---", b"k: v", b"---"])
    assert not mdblocks.is_balanced([b"---", b"k: v"])
    assert mdblocks.is_balanced([b"---", b"k: v"], front_matter=False)
    assert not mdblocks.is_balanced([b"\xff"])


def test_blocks_are_balanced_by_construction() -> None:
    raw = b"- a\n  ```\n\n  ```\n<!--\n\n-->\n~~~\n```\n~~~\n| t |\n"
    doc = _doc(raw)
    for u in mdblocks.parse_sources([doc]):
        for b in u.blocks:
            assert mdblocks.is_balanced(doc.lines[b.start - 1 : b.end])


def test_open_structure_at_end_for_extended_file() -> None:
    assert mdblocks.open_structure_at_end(b"a\n```\nx\n") == ("fence", 2)
    assert mdblocks.open_structure_at_end(b"a\n<!--\n") == ("comment", 2)
    assert mdblocks.open_structure_at_end(b"---\nx\n") == ("front_matter", 1)
    assert mdblocks.open_structure_at_end(b"a\n```\nx\n```") is None
    assert mdblocks.open_structure_at_end(b"") is None
    assert mdblocks.open_structure_at_end(b"\xef\xbb\xbf```\r\nx\r\n") == ("fence", 1)
    with pytest.raises(SourceError) as ei:
        mdblocks.open_structure_at_end(b"a\x00")
    assert ei.value.reason == "nul_byte"


# --------------------------------------------------------------------------- level2_headings


def test_level2_headings_tolerant_and_cr_stripped() -> None:
    text = "## A\r\n<!--\n## hidden\n-->\n##B\n### c\n```\n## open"
    assert mdblocks.level2_headings(text) == ["A", "B"]


def test_h2_re_matches_phase1_expression() -> None:
    assert mdblocks.H2_RE.pattern == r"^##(?!#)\s*(.*)$"
