"""Anchor tokens and deontic classes of the distillation check (fase 2b spec §5.3, §5.4).

Pure, deterministic functions over decoded lines. Both sides of every comparison (the
literal and the invariant) go through the same functions, so a rule that over- or
under-matches treats them alike.

* :func:`token_spans` / :func:`tokens`: the anchor tokenizer of condition (b). Each line is
  taken to the comparison form (``lexicon.comparison_form``: NFC, space-likes to a space,
  zero-width characters removed) **without** casefold, and the classes ``code``, ``url``,
  ``path``, ``date``, ``ident`` and ``number`` are extracted in that order; every match is
  masked with spaces before the next class runs, so one piece of text gives one token.
* :func:`deontic`: the multiset of deontic classes of condition (c) (``must_not``,
  ``never``, ``always``, ``must``) over the casefolded comparison form, left to right,
  without overlaps, longest alternatives first.

Three additions to the spec's table, both conservative and symmetric (decided while
implementing; see the spec's §16 defaults): letter-dot abbreviations such as ``e.g.`` or
``i.e.`` are masked before ``path`` (the spec states they are not paths, and its path
expression alone would match them), a dot-file such as ``.env`` is a ``path`` (the spec's
example, which its expression alone misses), and a ``path`` match made only of digits,
slashes, dots and hyphens (``07/10/2026``) is left to ``date``/``number``.

Tokens carry the matched text, so callers must never print them (G6): reports give the
class, the line, the column and the length only.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from .lexicon import comparison_form

__all__ = [
    "DEONTIC_CLASSES",
    "TOKEN_CLASSES",
    "Token",
    "deontic",
    "token_spans",
    "tokens",
]

TOKEN_CLASSES: tuple[str, ...] = ("code", "url", "path", "date", "ident", "number")
DEONTIC_CLASSES: tuple[str, ...] = ("must_not", "never", "always", "must")


@dataclass(frozen=True)
class Token:
    """One anchor token: class, text (never printed), 0-based line, 1-based column, length."""

    cls: str
    text: str
    line: int
    col: int
    length: int


# --------------------------------------------------------------------------- tokenizer

_CODE_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s<>()\[\]`]+")
_ABBREV_RE = re.compile(r"(?<![\w.])(?:[A-Za-z]\.){2,}")
_PATH_RE = re.compile(
    r"(?<![\w/.-])(?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]*"
    r"|(?<![\w/.-])\.?[\w-]+(?:\.[\w-]+)*\.(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,8}(?![\w/-])"
    r"|(?<![\w/.-])\.(?=[\w-]*[A-Za-z])[\w-]+(?:\.[\w-]+)*(?![\w/-])"
)
_PATH_NUMERIC_RE = re.compile(r"[\d/.-]+")
_DATE_RE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?!\d)")
_IDENT_DIGIT_RE = re.compile(r"(?<![\w-])[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*(?![\w-])")
_IDENT_SNAKE_RE = re.compile(r"(?<![\w-])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w-])")
_IDENT_CAMEL_RE = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+(?![\w-])")
_IDENT_UPPER_RE = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]{2,}(?![\w-])")
_IDENT_DIGIT_FIRST_RE = re.compile(r"(?<![\w-])\d[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*(?![\w-])")
_NUMBER_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*%?(?![\w])")
_DIGIT_RE = re.compile(r"\d")
# Upper-case words that are deontic markers (or part of one, such as NOT in MUST NOT):
# they count in condition (c), never as an ``ident`` anchor.
_DEONTIC_WORD_RE = re.compile(
    r"(?:nunca|jam[aá]s|never|siempre|always|must|shall|required|forbidden|prohibited"
    r"|deb\w*|obligatori\w*|prohibid\w*|not|no|do)"
)

# A class extractor: (match) -> (token text, span start, span end) or None to skip the
# match (a skipped match is not masked).
_Pick = Callable[[re.Match[str]], tuple[str, int, int] | None]


def _whole(m: re.Match[str]) -> tuple[str, int, int] | None:
    return m.group(0), m.start(), m.end()


def _code(m: re.Match[str]) -> tuple[str, int, int] | None:
    return m.group(2), m.start(2), m.end(2)


def _stripped(chars: str) -> _Pick:
    def pick(m: re.Match[str]) -> tuple[str, int, int] | None:
        text = m.group(0).rstrip(chars)
        if not text:
            return None
        return text, m.start(), m.start() + len(text)

    return pick


_url = _stripped(".,;:!?")
_path_strip = _stripped(".,;:")


def _path(m: re.Match[str]) -> tuple[str, int, int] | None:
    got = _path_strip(m)
    if got is None or _PATH_NUMERIC_RE.fullmatch(got[0]):
        return None
    return got


def _with_digit(m: re.Match[str]) -> tuple[str, int, int] | None:
    return _whole(m) if _DIGIT_RE.search(m.group(0)) else None


def _upper(m: re.Match[str]) -> tuple[str, int, int] | None:
    return None if _DEONTIC_WORD_RE.fullmatch(m.group(0).casefold()) else _whole(m)


# (class, pattern, extractor, mask whole match). ``code`` masks the backticks too; the
# abbreviation step masks without producing a token.
_STEPS: tuple[tuple[str | None, re.Pattern[str], _Pick, bool], ...] = (
    ("code", _CODE_RE, _code, True),
    ("url", _URL_RE, _url, False),
    (None, _ABBREV_RE, _whole, True),
    ("path", _PATH_RE, _path, False),
    ("date", _DATE_RE, _whole, False),
    ("ident", _IDENT_DIGIT_RE, _with_digit, False),
    ("ident", _IDENT_SNAKE_RE, _whole, False),
    ("ident", _IDENT_CAMEL_RE, _whole, False),
    ("ident", _IDENT_UPPER_RE, _upper, False),
    ("ident", _IDENT_DIGIT_FIRST_RE, _whole, False),
    ("number", _NUMBER_RE, _whole, False),
)


def _line_tokens(index: int, line: str) -> Iterator[Token]:
    text = comparison_form(line)
    found: list[Token] = []
    for cls, pattern, pick, mask_whole in _STEPS:
        spans: list[tuple[int, int]] = []
        for m in pattern.finditer(text):
            got = pick(m)
            if got is None:
                continue
            token, a, b = got
            spans.append((m.start(), m.end()) if mask_whole else (a, b))
            if cls is not None:
                found.append(Token(cls, token, index, a + 1, b - a))
        for a, b in spans:
            text = text[:a] + " " * (b - a) + text[b:]
    found.sort(key=lambda t: t.col)
    yield from found


def token_spans(lines: Sequence[str]) -> tuple[Token, ...]:
    """Every anchor token of ``lines`` (decoded, without line terminators), in line order
    and then column order. Columns and lengths are in characters of the comparison form."""
    return tuple(tok for i, line in enumerate(lines) for tok in _line_tokens(i, line))


def tokens(lines: Sequence[str]) -> Counter[tuple[str, str]]:
    """The multiset of ``(class, text)`` anchor tokens of ``lines`` (spec §5.3)."""
    return Counter((t.cls, t.text) for t in token_spans(lines))


# --------------------------------------------------------------------------- deontic

_DEONTIC_RE = re.compile(
    r"\b(?:"
    r"(?P<must_not>no se debe\w*|no deb\w*|prohibid\w*|must not|mustn['\u2019]t|shall not"
    r"|do not|don['\u2019]t|forbidden|prohibited)"
    r"|(?P<never>nunca|jam[aá]s|never)"
    r"|(?P<always>siempre|always)"
    r"|(?P<must>deb\w*|obligatori\w*|must|shall|required)"
    r")\b"
)
_NOT_MUST_RE = re.compile(r"debajo|debate\w*|debil\w*|d[eé]bit\w*")
_DEB_WORD_RE = re.compile(r"deb\w*$")


def _excluded(cls: str, match: str) -> bool:
    """``debajo``, ``debate…``, ``debil…`` and ``débit…`` are not obligations, alone or
    after ``no``."""
    if cls not in ("must", "must_not"):
        return False
    word = _DEB_WORD_RE.search(match)
    return word is not None and _NOT_MUST_RE.fullmatch(word.group(0)) is not None


def deontic(lines: Sequence[str]) -> Counter[str]:
    """The multiset of deontic classes of ``lines`` (spec §5.4), code spans included."""
    out: Counter[str] = Counter()
    for line in lines:
        # NFKC (not only NFC): a fullwidth or compatibility «NEVER» is an obligation too,
        # so the invariant can neither drop nor invent one that way (fail closed).
        text = unicodedata.normalize("NFKC", comparison_form(line)).casefold()
        for m in _DEONTIC_RE.finditer(text):
            cls = m.lastgroup
            if cls is None or _excluded(cls, m.group(0)):
                continue
            out[cls] += 1
    return out
