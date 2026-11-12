"""anchors: anchor tokenizer (condition b) and deontic classes (condition c), fase 2b §5.3-§5.4.

Synthetic text only. Every case is a pure function call.
"""

from __future__ import annotations

import unicodedata
from collections import Counter

import pytest

from declutter_context.reorg import anchors
from declutter_context.reorg.anchors import deontic, token_spans, tokens


def _set(text: str) -> set[tuple[str, str]]:
    return set(tokens([text]))


# --------------------------------------------------------------------------- tokenizer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("usa `map_terms` aquí", {("code", "map_terms")}),
        ("lee `docs/x.md` antes", {("code", "docs/x.md")}),
        ("usa ``a`b`` ya", {("code", "a`b")}),
        ("ver https://x.org/a.", {("url", "https://x.org/a")}),
        ("ver (https://x.org/a) y", {("url", "https://x.org/a")}),
        ("en docs/historial/ queda", {("path", "docs/historial/")}),
        ("lee AGENTS.md.", {("path", "AGENTS.md")}),
        ("no subas .env, nunca", {("path", ".env")}),
        ("corre ./scripts/run.sh;", {("path", "./scripts/run.sh")}),
        ("el 2026-10-01 pasó", {("date", "2026-10-01")}),
        ("el 07-10-2026 pasó", {("date", "07-10-2026")}),
        ("el 07/10/2026 pasó", {("date", "07/10/2026")}),
        ("hito M13-05 listo", {("ident", "M13-05")}),
        ("equipo PB840 y U13", {("ident", "PB840"), ("ident", "U13")}),
        ("versión v1.4.47 publicada", {("ident", "v1.4.47")}),
        ("rol service_role sin", {("ident", "service_role")}),
        ("llama extractFulltext ya", {("ident", "extractFulltext")}),
        ("activa RLS siempre", {("ident", "RLS")}),
        ("commit 30f87dd en main", {("ident", "30f87dd")}),
        ("Ley 19.628 de datos", {("number", "19.628")}),
        ("máximo 12 líneas o 60%", {("number", "12"), ("number", "60%")}),
        ("e.g. una cosa", set()),
        ("i.e. otra", set()),
        ("CI e IA no son anclas", set()),
        ("NUNCA SIEMPRE NEVER ALWAYS MUST SHALL NOT", set()),
        ("IMPORTANTE leer", {("ident", "IMPORTANTE")}),
        ("Supabase no es ancla", set()),
    ],
)
def test_token_classes(text: str, expected: set[tuple[str, str]]) -> None:
    assert _set(text) == expected


def test_code_span_masks_inner_path() -> None:
    assert tokens(["`docs/x.md`"]) == Counter({("code", "docs/x.md"): 1})


def test_ident_is_not_number_nor_date() -> None:
    got = _set("M13-05")
    assert got == {("ident", "M13-05")}
    assert not any(cls in ("number", "date") for cls, _ in got)


def test_nbsp_gives_the_same_token() -> None:
    assert _set("Ley\u00a019.628") == _set("Ley 19.628") == {("number", "19.628")}


def test_nfd_and_nfc_give_the_same_tokens() -> None:
    text = "versión v1.4.47 en `réplica_x` y M13-05"
    assert tokens([unicodedata.normalize("NFD", text)]) == tokens([text])


def test_zero_width_inside_identifier_is_removed() -> None:
    assert _set("hito M13\u200b-05") == {("ident", "M13-05")}


def test_dates_and_numbers_are_not_normalized() -> None:
    assert _set("07-10-2026") != _set("2026-10-07")


def test_case_is_kept() -> None:
    assert _set("`Map_Terms`") == {("code", "Map_Terms")}
    assert _set("`Map_Terms`") != _set("`map_terms`")


def test_spans_report_line_column_and_length() -> None:
    spans = token_spans(["nada", "ver M13-05 y `x_y`"])
    by = {(s.cls, s.text): s for s in spans}
    m = by[("ident", "M13-05")]
    assert (m.line, m.col, m.length) == (1, 5, 6)
    c = by[("code", "x_y")]
    assert (c.line, c.col, c.length) == (1, 15, 3)


def test_tokens_counts_multiplicity() -> None:
    assert tokens(["12 y 12"]) == Counter({("number", "12"): 2})


def test_token_classes_constant() -> None:
    assert anchors.TOKEN_CLASSES == ("code", "url", "path", "date", "ident", "number")


# --------------------------------------------------------------------------- deontic classes


def test_no_debe_is_one_must_not_and_no_must() -> None:
    assert deontic(["Esto no debe pasar."]) == Counter({"must_not": 1})


def test_nunca_equals_never() -> None:
    assert deontic(["NUNCA subas"]) == deontic(["never push"]) == Counter({"never": 1})


def test_debajo_and_debate_do_not_count() -> None:
    assert deontic(["queda debajo del debate y lo debilita"]) == Counter()


def test_must_not_does_not_count_must() -> None:
    assert deontic(["you must not do it"]) == Counter({"must_not": 1})
    assert deontic(["you mustn't", "shall not", "do not", "don't"]) == Counter({"must_not": 4})


def test_casefold_of_jamas() -> None:
    assert deontic(["JAMÁS"]) == Counter({"never": 1})
    assert deontic([unicodedata.normalize("NFD", "jamás")]) == Counter({"never": 1})


def test_deontic_counts_every_class() -> None:
    lines = ["Siempre corre; debe pasar.", "Prohibido subir; obligatorio revisar.", "always"]
    assert deontic(lines) == Counter({"always": 2, "must": 2, "must_not": 1})


def test_deontic_counts_inside_code() -> None:
    assert deontic(["`never`"]) == Counter({"never": 1})


def _fw(text: str) -> str:
    """Fullwidth form of ASCII letters (U+FF21..U+FF5A)."""
    return "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalpha() else c for c in text)


def test_deontic_counts_fullwidth_and_compatibility_forms() -> None:
    """A prohibition written in fullwidth letters is still a prohibition (NFKC)."""
    assert anchors.deontic([_fw("NEVER") + " push"])["never"] == 1
    assert anchors.deontic([_fw("Nunca") + " subas"])["never"] == 1
    assert anchors.deontic([_fw("must not")])["must_not"] == 1
