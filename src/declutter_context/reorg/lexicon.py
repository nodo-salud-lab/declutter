"""Structural and safety lexicons, comparison form and the G5 secret scan (spec §5.2, §5.3, §8).

Every lexicon is applied to the *comparison form* of a decoded line or block, never to the
bytes: NFC, the space-likes NBSP / U+2009 / U+202F turned into a plain space, and the
zero-width characters removed. Safety categories have a case-insensitive part (applied to
the casefolded comparison form) and a case-sensitive part (acronyms such as ``PHI`` or
``DROP``, applied to the unfolded comparison form).

The secret scan reports path, line number and category only; the matched value is never
returned, stored or printed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..config import _valid_lexicon_expr

SAFETY_CATEGORIES: tuple[str, ...] = (
    "clinical", "secrets", "publishable", "device", "irreversible",
)  # fmt: skip

# Spec §5.2, verbatim. All IGNORECASE, applied to the comparison form.
_STRUCTURAL_SRC: dict[str, str] = {
    "rule": (
        r"\b(reglas?|contratos?|guardias?|barreras?|invariantes?|pol[ií]ticas?|normas?"
        r"|obligatori\w*|rules?|polic(y|ies)|contracts?|guards?|invariants?)\b"
    ),
    "exec": (
        r"c[oó]mo (correr|ejecutar|instalar|probar)\w*|\b(instalaci[oó]n|comandos"
        r"|how to (run|build|test|install)|setup|getting started|commands)\b"
    ),
    "trap": r"trampas?|parecen bugs|no (es|son) (un )?bugs?|gotchas?|pitfalls?|known issues",
    "map": r"\bmapa\b|estructura del repositorio|repository map|repo map|project structure",
    "claude_only": (
        r"CLAUDE\.md|Claude Code|\.claude/|settings\.json|\bhooks?\b|\bskills?\b|subagentes?"
        r"|subagents?|\bMCP\b|statusline|barra de estado|slash command|comando de barra"
    ),
    "imperative": (
        r"\b(nunca|siempre|jam[aá]s|no debe\w*|debe\w*|prohibid\w*|obligatori\w*|never"
        r"|always|must|do not|don't|shall)\b"
    ),
    "decision": (
        r"\b(decid\w*|decisi[oó]n|eligi[oó]|descartad\w*|en lugar de|decided|instead of"
        r"|rejected)\b"
    ),
}

STRUCTURAL: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {key: re.compile(src, re.IGNORECASE) for key, src in _STRUCTURAL_SRC.items()}
)

# Path mention (Z, spec §5.2): the capture is the first path segment.
ZONE_RE: re.Pattern[str] = re.compile(r"(?<![\w/.`-])`?([A-Za-z0-9_.-]+)/")

# Spec §5.3, verbatim.
SAFETY_CI: Mapping[str, str] = MappingProxyType(
    {
        "clinical": (
            r"paciente|cl[ií]nic|datos personales|Ley 19\.628|Ley 20\.584|Ley 20\.120"
            r"|anonimiz|seudonim|consentimiento|patient|personal data|de-?identif"
            r"|informed consent"
        ),
        "secrets": (
            r"secret|credencial|credential|api[ _-]?key|\.env\b|service[_ ]?role|privilegi"
            r"|clave privada|private key|contrase[ñn]a|password|passwd|access[ _-]?token"
            r"|token de acceso|bearer"
        ),
        "publishable": (
            r"barrera de regresi[oó]n|regression barrier|cifras? publicab|publishable"
            r"|benchmark|recall|sensibilidad|sensitivity|especificidad|specificity|κ|kappa"
            r"|no inventar|do not fabricate"
        ),
        "device": (
            r"ventilador|ventilator|equipo m[eé]dico|dispositivo m[eé]dico|medical device"
            r"|alarma cl[ií]nica|clinical alarm|device safety"
        ),
        "irreversible": (
            r"irreversib|sin borrado|eliminar mis datos|delete my data|force[ -]push"
            r"|delete permanently|borrado permanente|\bpush\b|reset --hard|rm -rf|\bborrar\b"
            r"|\beliminar\b|\bdelete\b|merge a main"
        ),
    }
)
SAFETY_CS: Mapping[str, str] = MappingProxyType(
    {
        "clinical": r"\bPHI\b",
        "secrets": r"\bRLS\b",
        "device": r"\bPB840\b",
        "irreversible": r"\bDROP\b|\bTRUNCATE\b",
    }
)

# Spec §8 G5, verbatim, in table order.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(src))
    for name, src in (
        ("openai_like", r"sk-[A-Za-z0-9_-]{20,}"),
        ("stripe", r"sk_(live|test)_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}"),
        ("github", r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}"),
        ("slack", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        ("google_api", r"AIza[0-9A-Za-z_-]{35}"),
        ("supabase", r"sbp_[0-9a-f]{40}"),
        ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
        ("jwt", r"eyJ[\w-]{10,}\.[\w-]{10,}\."),
        ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        ("url_credentials", r"[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@]+@"),
        (
            "assignment",
            r"(?i)(?:^|[^A-Za-z0-9])[A-Z0-9_]*(password|passwd|secret|service_role|api_?key"
            r"|token)[A-Z0-9_]*\s*[:=]\s*[\"']?[^\s\"'`]{8,}",
        ),
    )
)

# Linear-time equivalents used by ``scan_secrets``. ``SECRET_PATTERNS`` stays the spec text;
# three of its rows restart a scan of a whole character run from every start position, so a
# long single-line file (minified, base64) costs O(n^2). Each replacement below matches a
# line if and only if the spec pattern does (tests/test_lexicon.py checks this against the
# spec strings on random lines), but tries each character run once:
# - jwt: a run of ``[\w-]`` holding ``eyJ`` matches iff its FIRST ``eyJ`` does (every later
#   one has fewer characters before the same run end), so the prefix is atomic.
# - assignment: the trailing ``[A-Z0-9_]*`` must reach the end of the run, and the character
#   before a run is always ``^`` or ``[^A-Za-z0-9]``; so the spec pattern matches iff some
#   whole run contains a keyword and is followed by the value part. Starting only at run
#   starts and testing the keyword with a lookahead is that condition.
# - url_credentials: see ``_has_url_credentials``.
# Possessive quantifiers and atomic groups need Python 3.11, the project's minimum.
_JWT_FAST = re.compile(r"(?<![\w-])(?>[\w-]*?eyJ)[\w-]{10,}+\.[\w-]{10,}+\.")
_ASSIGNMENT_FAST = re.compile(
    r"(?i)(?<![A-Z0-9_])(?=[A-Z0-9_]*?(?:password|passwd|secret|service_role|api_?key|token))"
    r"[A-Z0-9_]*+\s*[:=]\s*[\"']?[^\s\"'`]{8}"
)
# Scheme run start (any earlier start in the same run implies this one), ``://``, user, ``:``.
_URL_HEAD = re.compile(r"(?<![a-z0-9+.-])[0-9+.-]*+[a-z][a-z0-9+.-]*+://[^\s:/@]++:")
_URL_STOP = re.compile(r"[\s@]")


def _has_url_credentials(line: str) -> bool:
    r"""Same answer as the spec ``url_credentials`` pattern, in linear time.

    After a head ``scheme://user:`` the spec needs ``[^\s@]+@``: the first whitespace or
    ``@`` after the head must be an ``@`` with at least one character before it. Heads come
    in increasing order, so the stop found for one head is reused while it lies ahead.
    """
    stop = -1
    for head in _URL_HEAD.finditer(line):
        end = head.end()
        if stop < end:
            found = _URL_STOP.search(line, end)
            stop = found.start() if found else len(line)
        if end < stop < len(line) and line[stop] == "@":
            return True
    return False


def _searcher(pattern: re.Pattern[str]) -> Callable[[str], bool]:
    return lambda line: pattern.search(line) is not None


_FAST: dict[str, Callable[[str], bool]] = {
    "jwt": _searcher(_JWT_FAST),
    "url_credentials": _has_url_credentials,
    "assignment": _searcher(_ASSIGNMENT_FAST),
}
_SECRET_CHECKS: tuple[tuple[str, Callable[[str], bool]], ...] = tuple(
    (name, _FAST.get(name) or _searcher(pattern)) for name, pattern in SECRET_PATTERNS
)

_SPACE_LIKE = {"\u00a0": " ", "\u2009": " ", "\u202f": " "}
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_TRANSLATE: dict[int, str | None] = {
    **{ord(ch): rep for ch, rep in _SPACE_LIKE.items()},
    **{ord(ch): None for ch in _ZERO_WIDTH},
}


def comparison_form(text: str) -> str:
    """Return the comparison form of ``text`` (spec §5.3).

    Space-likes become a plain space and zero-width characters are removed *before* NFC,
    so a zero-width character between a letter and its combining accent cannot keep the
    result decomposed. NFC never produces those characters, so the result is NFC and the
    function is idempotent.
    """
    return unicodedata.normalize("NFC", text.translate(_TRANSLATE))


@dataclass(frozen=True)
class Lexicon:
    """Compiled safety lexicon: built-in terms plus project extensions (ci only)."""

    ci: Mapping[str, re.Pattern[str]]
    cs: Mapping[str, re.Pattern[str]]

    def safety(self, text: str) -> frozenset[str]:
        """Return the safety categories that match ``text`` (a line or a joined block)."""
        form = comparison_form(text)
        folded = form.casefold()
        found = {cat for cat, pat in self.ci.items() if pat.search(folded)}
        found.update(cat for cat, pat in self.cs.items() if pat.search(form))
        return frozenset(found)

    def structural(self, key: str, text: str) -> bool:
        """Return True if the structural lexicon ``key`` (spec §5.2) matches ``text``."""
        return STRUCTURAL[key].search(comparison_form(text)) is not None


def build_lexicon(extra: Mapping[str, tuple[str, ...]]) -> Lexicon:
    """Compile the safety lexicon, joining each extension to its category's ``ci`` part.

    Extensions only ADD terms; there is no way to remove a built-in one. ``extra`` comes
    from :mod:`declutter_context.config`, which already dropped invalid expressions with a
    warning; anything invalid here is a programming error and raises ``ValueError``.

    Extensions are trusted project configuration: they are only checked to compile, not
    for catastrophic backtracking, and they run on every line and block. Keep them to
    plain terms and bounded character classes.
    """
    ci: dict[str, re.Pattern[str]] = {}
    for cat in SAFETY_CATEGORIES:
        ci[cat] = re.compile(SAFETY_CI[cat], re.IGNORECASE)
    for cat, exprs in extra.items():
        if cat not in SAFETY_CATEGORIES:
            raise ValueError(f"unknown lexicon category: {cat!r}")
        terms = [expr for expr in exprs if expr]
        for index, expr in enumerate(terms):
            if not _valid_lexicon_expr(expr):
                raise ValueError(f"invalid lexicon expression #{index + 1} for {cat!r}")
        if terms:
            ci[cat] = re.compile("|".join((SAFETY_CI[cat], *terms)), re.IGNORECASE)
    cs = {cat: re.compile(src) for cat, src in SAFETY_CS.items()}
    return Lexicon(ci=MappingProxyType(ci), cs=MappingProxyType(cs))


def zone_mentions(text: str) -> list[str]:
    """Return the first path segment of every ``dir/`` mention in ``text``, in order.

    No filtering is done here: the classifier keeps only segments that are existing,
    non-hidden top-level directories outside ``fsutil.SKIP_DIRS`` (spec §5.1 Z).
    """
    return [m.group(1) for m in ZONE_RE.finditer(comparison_form(text))]


@dataclass(frozen=True)
class SecretHit:
    """One G5 match: where and which pattern. Never the value."""

    path: str
    line: int  # 1-based
    category: str


def scan_secrets(path: str, raw: bytes) -> tuple[SecretHit, ...]:
    """Run every G5 pattern on every line of ``raw`` (spec §8 G5).

    The bytes are decoded with ``errors="replace"`` only to search them; one hit is
    reported per (line, pattern), in line order and then table order. The decoded text
    and the matched values are never returned. Lines follow the ``mdblocks`` model: split
    on ``\n`` only, so a lone ``\r`` stays inside its line and a BOM belongs to line 1.
    The cost is linear in the length of each line (see ``_SECRET_CHECKS``).
    """
    text = raw.decode("utf-8", errors="replace")
    hits: list[SecretHit] = []
    for number, line in enumerate(text.split("\n"), start=1):
        for category, matches in _SECRET_CHECKS:
            if matches(line):
                hits.append(SecretHit(path=path, line=number, category=category))
    return tuple(hits)
