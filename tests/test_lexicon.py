"""lexicon: comparison form, structural and safety lexicons, G5 secret scan (spec §5.2, §5.3, §8).

Synthetic text only. Secret-shaped values are assembled at run time by concatenation, so
the repository never holds a literal that looks like a real credential.
"""

from __future__ import annotations

import re
import unicodedata

import pytest

from declutter_context.reorg import lexicon as lx
from declutter_context.reorg.lexicon import (
    SAFETY_CATEGORIES,
    SECRET_PATTERNS,
    STRUCTURAL,
    SecretHit,
    build_lexicon,
    comparison_form,
    scan_secrets,
    zone_mentions,
)

LEX = build_lexicon({})


def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


# --------------------------------------------------------------------------- comparison form


def test_comparison_form_nfc_spaces_and_zero_width() -> None:
    raw = _nfd("clínica") + "\u00a0a\u2009b\u202fc" + "d\u200be\u200cf\u200dg\u2060h\ufeffi"
    out = comparison_form(raw)
    assert out == "clínica a b cdefghi"
    assert unicodedata.is_normalized("NFC", out)
    assert comparison_form(out) == out


def test_comparison_form_zero_width_inside_combining_sequence_is_composed() -> None:
    # A zero-width char between a base letter and its combining accent: the result is NFC.
    raw = "cli\u200b́nica"
    assert comparison_form(raw) == "clínica"


def test_comparison_form_keeps_other_whitespace() -> None:
    assert comparison_form("a\tb\r") == "a\tb\r"


# --------------------------------------------------------------------------- safety lexicon


SAFETY_VARIANTS: list[tuple[str, str]] = [
    ("clinical", _nfd("historia clínica")),
    ("clinical", "Ley\u00a019.628"),
    ("secrets", _nfd("la contraseña")),
    ("secrets", "clave\u2009privada"),
    ("publishable", _nfd("barrera de regresión")),
    ("publishable", "cifras\u00a0publicables"),
    ("device", "equipo\u202fmédico"),
    ("device", _nfd("dispositivo médico")),
    ("irreversible", "borrado\u00a0permanente"),
    ("irreversible", "elim\u200binar mis datos"),
]


@pytest.mark.parametrize(("category", "text"), SAFETY_VARIANTS)
def test_safety_categories_match_nfd_and_nbsp(category: str, text: str) -> None:
    nfc = unicodedata.normalize("NFC", text)
    for ch in ("\u00a0", "\u2009", "\u202f"):
        nfc = nfc.replace(ch, " ")
    nfc = nfc.replace("\u200b", "")
    assert category in LEX.safety(nfc)
    assert LEX.safety(text) == LEX.safety(nfc)


def test_safety_categories_constant() -> None:
    assert SAFETY_CATEGORIES == ("clinical", "secrets", "publishable", "device", "irreversible")
    assert set(LEX.ci) == set(SAFETY_CATEGORIES)
    assert set(LEX.cs) == {"clinical", "secrets", "device", "irreversible"}


def test_safety_ci_and_cs_are_the_spec_strings() -> None:
    assert lx.SAFETY_CS == {
        "clinical": r"\bPHI\b",
        "secrets": r"\bRLS\b",
        "device": r"\bPB840\b",
        "irreversible": r"\bDROP\b|\bTRUNCATE\b",
    }
    assert lx.SAFETY_CI["device"] == (
        r"ventilador|ventilator|equipo m[eé]dico|dispositivo m[eé]dico|medical device"
        r"|alarma cl[ií]nica|clinical alarm|device safety"
    )


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("PHI", "clinical"),
        ("RLS", "secrets"),
        ("DROP", "irreversible"),
        ("TRUNCATE", "irreversible"),
        ("PB840", "device"),
    ],
)
def test_case_sensitive_acronyms(text: str, category: str) -> None:
    assert LEX.safety(f"see {text} here") == frozenset({category})


@pytest.mark.parametrize("text", ["phi", "rls", "drop table", "truncate", "pb840", "PHIL"])
def test_case_sensitive_acronyms_lowercase_do_not_match(text: str) -> None:
    assert LEX.safety(text) == frozenset()


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("git push", "irreversible"),
        ("delete", "irreversible"),
        ("borrar", "irreversible"),
        ("contraseña", "secrets"),
        ("contrasena", "secrets"),
        ("access-token", "secrets"),
        ("access_token", "secrets"),
        ("access token", "secrets"),
        ("force push", "irreversible"),
        ("rm -rf build", "irreversible"),
    ],
)
def test_word_boundaries_new_terms_match(text: str, category: str) -> None:
    assert category in LEX.safety(text)


@pytest.mark.parametrize(
    "text", ["pushed", "pushing", "deleted", "borrarlo", "token", "token budget", "equipo"]
)
def test_word_boundaries_new_terms_do_not_match(text: str) -> None:
    assert LEX.safety(text) == frozenset()


def test_safety_multiple_categories_and_casefold() -> None:
    assert LEX.safety("DATOS PERSONALES y PASSWORD") == frozenset({"clinical", "secrets"})
    # casefold, not lower: capital sharp s folds to "ss" ("ẞ".lower() is "ß").
    assert "secrets" in LEX.safety("PAẞWORD")


# --------------------------------------------------------------------------- structural lexicons


STRUCTURAL_CASES: list[tuple[str, str, str]] = [
    ("rule", "Reglas del repositorio", "Reglamento interno"),
    ("rule", "Data policies", "Polish notes"),
    ("exec", "Cómo correr las pruebas", "Correcto uso"),
    ("exec", "Getting started", "Get started later"),
    ("trap", "Trampas conocidas", "Arquitectura"),
    ("trap", "Known issues", "Issues"),
    ("map", "Mapa del código", "Mapas antiguos"),
    ("map", "Repository map", "Repository"),
    ("claude_only", "Hooks de Claude Code", "Skillset growth"),
    ("claude_only", "configura .claude/settings.json", "configuración general"),
    ("imperative", "Nunca subas esto", "A veces conviene"),
    ("imperative", "You must not", "Mustard"),
    ("decision", "Se decidió usar SQLite", "Una decena"),
    ("decision", "chosen instead of Redis", "insteadof"),
]


@pytest.mark.parametrize(("key", "positive", "negative"), STRUCTURAL_CASES)
def test_structural_lexicons_table(key: str, positive: str, negative: str) -> None:
    assert LEX.structural(key, positive)
    assert not LEX.structural(key, negative)


def test_structural_keys_and_flags() -> None:
    assert set(STRUCTURAL) == {
        "rule", "exec", "trap", "map", "claude_only", "imperative", "decision",
    }  # fmt: skip
    for pattern in STRUCTURAL.values():
        assert pattern.flags & re.IGNORECASE


def test_structural_uses_comparison_form() -> None:
    assert LEX.structural("exec", _nfd("cómo ejecutar"))
    assert LEX.structural("exec", "how\u00a0to run")
    assert LEX.structural("claude_only", "Claude\u202fCode")


def test_structural_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        LEX.structural("nope", "text")


# --------------------------------------------------------------------------- extension


def test_extra_lexicon_adds_terms() -> None:
    term = "monitor multiparam\\w*"
    ext = build_lexicon({"device": (term,), "clinical": ("ficha cl[ií]nica",)})
    assert "device" not in LEX.safety("el monitor multiparamétrico")
    assert "device" in ext.safety("el monitor multiparamétrico")
    assert "clinical" in ext.safety("ficha clinica")
    for cat in ("secrets", "publishable", "irreversible"):
        assert ext.ci[cat].pattern == LEX.ci[cat].pattern
    assert {k: p.pattern for k, p in ext.cs.items()} == {k: p.pattern for k, p in LEX.cs.items()}
    assert ext.ci["device"].pattern == lx.SAFETY_CI["device"] + "|" + term


def test_extra_lexicon_never_removes_builtin_terms() -> None:
    ext = build_lexicon({"secrets": ("cofre",)})
    assert "secrets" in ext.safety("password")
    assert "secrets" in ext.safety("el cofre")


def test_extra_lexicon_rejects_unknown_category_and_bad_expression() -> None:
    with pytest.raises(ValueError):
        build_lexicon({"other": ("x",)})
    with pytest.raises(ValueError):
        build_lexicon({"clinical": ("(unclosed",)})


# --------------------------------------------------------------------------- zones


def test_zone_mentions() -> None:
    assert zone_mentions("edita `src/app.py` y tests/x") == ["src", "tests"]
    assert zone_mentions("http://a/b") == []
    assert "src" not in zone_mentions("./src/")
    assert zone_mentions("a/b/c y a/d") == ["a", "a"]
    assert zone_mentions("src\u200b/x") == ["src"]


# --------------------------------------------------------------------------- G5 secret scan


def _secret_cases() -> list[tuple[str, str]]:
    return [
        ("openai_like", "sk" + "-" + "a1B2" * 6),
        ("stripe", "sk_" + "test_" + "Ab12" * 4),
        ("stripe", "rk_" + "live_" + "Zz90" * 4),
        ("github", "gh" + "p_" + "Q" * 30),
        ("github", "github_" + "pat_" + "x_" * 11),
        ("slack", "xox" + "b-" + "1234-abcde-f"),
        ("google_api", "AI" + "za" + "Sy" + "k" * 33),
        ("supabase", "sbp_" + "0a" * 20),
        ("aws_access_key", "AK" + "IA" + "ABCDEFGH12345678"),
        ("jwt", "ey" + "J" + "h" * 12 + "." + "p" * 12 + ".sig"),
        ("private_key", "-----" + "BEGIN RSA PRIVATE " + "KEY-----"),
        ("url_credentials", "postgres" + "://u:p@h"),
        ("assignment", "DB_PASS" + "WORD=" + "abcdefgh123"),
        ("assignment", "SUPABASE_SERVICE_ROLE_" + "KEY=" + "abcdefghij"),
        ("assignment", 'api_key: "' + "Zx9" * 4 + '"'),
    ]


def test_secret_patterns_table_order() -> None:
    assert [name for name, _ in SECRET_PATTERNS] == [
        "openai_like", "stripe", "github", "slack", "google_api", "supabase",
        "aws_access_key", "jwt", "private_key", "url_credentials", "assignment",
    ]  # fmt: skip


@pytest.mark.parametrize(("category", "value"), _secret_cases())
def test_scan_secrets_each_pattern(category: str, value: str) -> None:
    raw = f"# Title\n\nsome prose\nvalue {value} end\nmore\n".encode()
    hits = scan_secrets("CLAUDE.md", raw)
    assert hits == (SecretHit(path="CLAUDE.md", line=4, category=category),)
    assert value not in repr(hits)
    assert value not in str(hits[0])
    assert set(vars(hits[0])) == {"path", "line", "category"}


def test_scan_secrets_every_line_and_pattern() -> None:
    aws = "AK" + "IA" + "ABCDEFGH12345678"
    url = "postgres" + "://u:p@h"
    raw = f"{aws}\r\nok\n{url} {aws}\n".encode()
    hits = scan_secrets("AGENTS.md", raw)
    assert [(h.line, h.category) for h in hits] == [
        (1, "aws_access_key"),
        (3, "aws_access_key"),
        (3, "url_credentials"),
    ]


def test_scan_secrets_invalid_utf8_is_scanned_with_replacement() -> None:
    aws = ("AK" + "IA" + "ABCDEFGH12345678").encode()
    hits = scan_secrets("x.md", b"\xff\xfe bad\n" + aws + b"\n")
    assert [(h.line, h.category) for h in hits] == [(2, "aws_access_key")]


@pytest.mark.parametrize(
    "text",
    [
        "the password field",
        "rotate the api key",
        "token budget",
        "set the token: short",
        "see https://example.com/path",
        "skip the sk-short value",
    ],
)
def test_scan_secrets_no_false_positive_on_prose(text: str) -> None:
    assert scan_secrets("CLAUDE.md", text.encode()) == ()


# --------------------------------------------------------------------------- G5 linear cost

_FUZZ_ALPHABET = (
    "a", "b", "Z", "1", "_", "-", ".", "+", ":", "/", "@", " ", "\t", "=", "'", '"', "`",
    "ey" + "J", "token", "TOKEN", "pass" + "word", "api_" + "key", "service_role",
    "x://", "postgres://", "\u0131", "\u212a", "é", "Abc_de-fgh", ".",
)  # fmt: skip


def test_scan_secrets_fast_checks_agree_with_spec_patterns() -> None:
    import random

    rng = random.Random(20261104)
    spec = dict(SECRET_PATTERNS)
    fast = dict(lx._SECRET_CHECKS)
    assert list(fast) == [name for name, _ in SECRET_PATTERNS]
    positives = dict.fromkeys(fast, 0)
    # A second, narrower alphabet makes jwt-shaped lines frequent enough to count.
    jwt_alphabet = ("ey" + "J", "Abc_de-fgh", "a", "-", ".", " ", "é", "/")
    for index in range(60_000):
        alphabet = jwt_alphabet if index % 3 == 0 else _FUZZ_ALPHABET
        line = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
        for name in ("jwt", "url_credentials", "assignment"):
            expected = spec[name].search(line) is not None
            assert fast[name](line) is expected, (name, line)
            positives[name] += expected
    # The fuzz must exercise both answers of every rewritten pattern.
    assert all(positives[name] > 50 for name in ("jwt", "url_credentials", "assignment"))


@pytest.mark.parametrize(
    "line",
    [
        "_" * 1_000_000,
        "a" * 1_000_000,
        ("ey" + "J") * 300_000,
        "ey" + "J" + "a" * 1_000_000,
        "token" * 200_000,
        "a://b:" * 150_000,
        "x" * 500_000 + "://" + "u" * 500_000,
    ],
    ids=["underscores", "letters", "jwt_heads", "jwt_long", "keywords", "url_heads", "url_long"],
)
def test_scan_secrets_long_line_is_linear(line: str) -> None:
    import time

    start = time.perf_counter()
    assert scan_secrets("min.js", line.encode()) == ()
    # Quadratic matching took tens of seconds on 50 000 characters; linear takes well
    # under a second on a million.
    assert time.perf_counter() - start < 5.0


def test_scan_secrets_long_line_still_finds_secret_at_end() -> None:
    aws = "AK" + "IA" + "ABCDEFGH12345678"
    url = "postgres" + "://u:p@h"
    raw = ("_" * 500_000 + " " + aws + " " + url).encode()
    hits = scan_secrets("min.js", raw)
    assert [(h.line, h.category) for h in hits] == [(1, "aws_access_key"), (1, "url_credentials")]


def test_scan_secrets_lines_follow_lf_only() -> None:
    aws = "AK" + "IA" + "ABCDEFGH12345678"
    raw = b"\xef\xbb\xbfok\rstill line one\r" + aws.encode() + b"\nline two\n" + aws.encode()
    hits = scan_secrets("x.md", raw)
    assert [(h.line, h.category) for h in hits] == [(1, "aws_access_key"), (3, "aws_access_key")]
