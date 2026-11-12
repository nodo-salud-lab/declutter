"""Synthetic generators for the reorg tests.

Nothing here comes from a real project: every heading and line is invented. The random
documents are always *balanced* (every fence, comment and front matter is closed), so a
plan over them never ends with code 2.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from datetime import datetime

from declutter_context.model import Config, Project
from declutter_context.reorg import manifest as mf

# --------------------------------------------------------------------------- random documents

_WORDS = (
    "alfa", "beta", "gama", "delta", "épsilon", "zeta", "eta", "theta", "iota", "kappa-x",
    "lambda", "mu", "ñandú", "omicron", "pi", "rho", "sigma", "tau", "ípsilon", "fi",
)  # fmt: skip

# Body sentences that switch on block features: imperative (K), decision, trap, Claude-only
# (Q), safety (S, G1 copies) and path mentions (Z). None matches a G5 secret pattern.
_SENTENCES = (
    "Nunca uses la base sintética de prueba.",
    "Siempre corre make check antes de cerrar.",
    "Decidimos usar la cola local en lugar de la remota.",
    "Parecen bugs pero no lo son: es el redondeo sintético.",
    "Configura los hooks y skills en settings.json.",
    "El campo password del formulario sintético se valida aparte.",
    "Los datos de pacientes sintéticos son confidenciales.",
    "Ver src/alfa y docs/beta para el detalle.",
    "Mide el recall del clasificador sintético.",
    "Texto neutro de relleno sintético.",
    "Otra línea neutra con `código en línea`.",
    "Una línea con ```npm run dev``` en prosa.",
    "Escribe `<!--` para abrir un comentario en línea.",
)

_HEAD_BASES = ("Sección sintética", "Notas", "Bitácora", "Tema", "Apartado", "Registro")
_HEAD_DATES = ("2026-10-05", "(07-10-2026)", "05-09-2026", "2026-09-30", "(rama `exp-1`)")
_HEAD_TERMS = (
    "Reglas",
    "Cómo correr",
    "Trampas",
    "Mapa",
    "Hooks de Claude Code",
    "Datos de pacientes",
    "Contrato",
    "Decisiones",
)
# Lines safe inside a fence or a comment: never a closing line of any structure.
_INNER = (
    "make test",
    "## título dentro de una estructura",
    "echo sintético",
    "",
    "x = 1",
    "<!-- dentro de valla",
    "| no | es tabla |",
)


def _word(rng: random.Random) -> str:
    return rng.choice(_WORDS)


def _text(rng: random.Random) -> str:
    if rng.random() < 0.55:
        line = rng.choice(_SENTENCES)
    else:
        line = " ".join(_word(rng) for _ in range(rng.randint(1, 6)))
    if rng.random() < 0.15:
        line += " " * rng.randint(1, 3)  # trailing spaces
    if rng.random() < 0.05:
        line = line.replace(" ", "\r", 1)  # lone CR inside the line
    return line


def _heading(rng: random.Random) -> str:
    parts = [rng.choice(_HEAD_BASES), _word(rng)]
    if rng.random() < 0.35:
        parts.insert(0, rng.choice(_HEAD_TERMS))
    if rng.random() < 0.45:
        parts.append(rng.choice(_HEAD_DATES))
    return "## " + " ".join(parts)


def _fence(rng: random.Random, indent: str = "") -> list[str]:
    char = rng.choice("`~")
    n = rng.randint(3, 5)
    top = 3 - len(indent)  # an opening or closing line is a fence only with 0-3 spaces
    pad = " " * rng.randint(0, top)
    info = rng.choice(("", "sh", "sql", "text"))
    body: list[str] = []
    for _ in range(rng.randint(0, 4)):
        body.append(rng.choice(_INNER))
    if char == "~" and rng.random() < 0.5:
        body.append("```")  # a ``` line never closes a ~~~ fence
    if char == "`" and rng.random() < 0.3:
        body.append("~~~")
    close = char * (n + rng.randint(0, 1)) + " " * rng.randint(0, 2)
    return [
        indent + pad + char * n + info,
        *(indent + b if b else "" for b in body),
        indent + " " * rng.randint(0, top) + close,
    ]


def _table(rng: random.Random) -> list[str]:
    rows = [f"| {_word(rng)} | {_word(rng)} |", "|---|---|"]
    rows += [f"| {_word(rng)} | {_word(rng)} |" for _ in range(rng.randint(1, 3))]
    return rows


def _item(rng: random.Random) -> list[str]:
    marker = rng.choice(("-", "*", "+", "1.", "2)"))
    out = [f"{marker} {_text(rng)}"]
    for _ in range(rng.randint(0, 2)):
        kind = rng.random()
        if kind < 0.4:
            out.append("  " + _text(rng))
        elif kind < 0.75:
            out.append(f"  - {_text(rng)}")
        else:
            out += _fence(rng, indent="  ")
    return out


def _comment(rng: random.Random) -> list[str]:
    if rng.random() < 0.4:
        return [f"<!-- {_word(rng)} -->"]
    inner = [w for w in (rng.choice(_INNER) for _ in range(rng.randint(0, 3))) if "<!--" not in w]
    return [f"<!-- {_word(rng)}", *inner, "-->"]


def _paragraph(rng: random.Random) -> list[str]:
    lines = [_text(rng) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.2:
        lines[-1] = lines[-1].rstrip() + ":"
    return lines


def _block(rng: random.Random) -> list[str]:
    r = rng.random()
    if r < 0.35:
        return _paragraph(rng)
    if r < 0.6:
        return _item(rng)
    if r < 0.72:
        return _fence(rng)
    if r < 0.82:
        return _table(rng)
    if r < 0.9:
        return _comment(rng)
    return [f"### {_word(rng)} {_word(rng)}"]


def _body(rng: random.Random, history: list[list[str]]) -> list[str]:
    out: list[str] = []
    for _ in range(rng.randint(0, 4)):
        if history and rng.random() < 0.15:
            block = list(rng.choice(history))  # duplicated block
        else:
            block = _block(rng)
            history.append(block)
        out += block
        out += [""] * rng.choice((0, 1, 1, 1, 2))
    return out


def random_lines(rng: random.Random) -> list[str]:
    """The lines (without terminators) of one balanced synthetic document."""
    lines: list[str] = []
    history: list[list[str]] = []
    if rng.random() < 0.08:
        lines += ["---", f"title: {_word(rng)}", "---"]
    if rng.random() < 0.75:
        lines.append(f"# Demo {_word(rng)}")
        lines += _body(rng, history)
    headings: list[str] = []
    for _ in range(rng.randint(0, 6)):
        if headings and rng.random() < 0.15:
            head = rng.choice(headings)  # duplicated heading (key suffix ~N)
        else:
            head = _heading(rng)
            headings.append(head)
        lines.append(head)
        lines += _body(rng, history)
    if not any(x.strip() for x in lines):
        lines.append(_text(rng) or "texto")
    return lines


def random_document(rng: random.Random) -> bytes:
    """A balanced synthetic Markdown document as raw bytes.

    Mixes duplicates, blank lines, trailing spaces, CRLF, lone CR, BOM, missing final
    newline, fences (``` and ~~~, 3-5 characters, indented 0-3), tables, items with
    continuations and ``##`` headings with and without dates and lexicon terms.
    """
    lines = random_lines(rng)
    style = rng.random()
    out: list[bytes] = []
    for line in lines:
        data = line.encode("utf-8")
        if style < 0.6:
            eol = b"\n"
        elif style < 0.8:
            eol = b"\r\n"
        else:
            eol = rng.choice((b"\n", b"\r\n"))  # mixed endings
        out.append(data + eol)
    raw = b"".join(out)
    if rng.random() < 0.2:
        raw = raw.rstrip(b"\n")
        if rng.random() < 0.5:
            raw = raw.rstrip(b"\r")
    if rng.random() < 0.1:
        raw = b"\xef\xbb\xbf" + raw
    return raw


# ------------------------------------------------------------------------ large synthetic project

# Top-level folders of the large synthetic project.
LARGE_DIRS: tuple[str, ...] = (
    "api", "archivo-local", "benchmarks", "docs", "edge", "eval", "functions", "netlify",
    "public", "scripts", "server", "src", "supabase", "test-results", "tests",
)  # fmt: skip

# Bytes of the 20 one-line G1 copies outside U10 and U16 (22 copies, 107 lines, 15,438 B in all;
# U16 copies 72 lines / 5,162 B and U10 15 lines / 1,649 B, so the other 20 copies hold
# 8,627 B), split as 13 of 431 B and 7 of 432 B.
_COPY_SIZES: tuple[int, ...] = (431,) * 13 + (432,) * 7
U10_GROUP_BYTES = 1_649
U16_FENCE_BYTES = 5_162

# A piece of a unit body: "fill" (neutral paragraph line), "blank", "k" (imperative item),
# "s" (one-line safety item copied by G1, fixed size), "sv" (safety line kept in L1),
# "m:<text>" (a line with path mentions), "item" (neutral item), "group" (U10 list group),
# "fence" (U16 fence) and "rest" (neutral lines up to the unit's line count).
Piece = str

# One row per unit: (heading, first line, last line, bytes, body pieces).
# ``None`` is the preamble. Headings are synthetic; they only switch on classifier features
# (date, R, C, T, M and the G2 categories).
LARGE_ROWS: tuple[tuple[str | None, int, int, int, tuple[Piece, ...]], ...] = (
    (None, 1, 10, 865, ("rest",)),
    ("Cómo correr el sistema sintético", 11, 49, 3_619, ("rest",)),
    ("Las seis reglas sintéticas", 50, 69, 2_406, ("rest",)),
    ("Guardia sintética A (07-10-2026)", 70, 77, 1_328, ("k", "blank", "rest")),
    ("Ahorro sintético B (06-10-2026)", 78, 82, 906, ("rest",)),
    ("Simulacro sintético C (05-10-2026)", 83, 86, 910, ("rest",)),
    ("Fase sintética D (08-10-2026)", 87, 97, 3_806, ("k", "blank", "s", "blank", "rest")),
    ("Contrato de trabajo sintético", 98, 109, 1_030, ("rest",)),
    ("Barrera de regresión sintética E (01-10-2026)", 110, 134, 1_403, ("rest",)),
    ("Barrera de regresión sintética F (02-10-2026)", 135, 166, 2_614, ("rest",)),
    ("Contrato sintético G (09-10-2026)", 167, 184, 1_714, ("blank", "group", "blank")),
    (
        "Costo sintético H (10-10-2026)",
        185,
        197,
        2_919,
        (
            "k",
            "blank",
            "m:Ver functions/f1.",
            "m:Ver functions/f2.",
            "m:Ver functions/f3.",
            "blank",
            "rest",
        ),
    ),
    ("Experimentos sintéticos I (11-10-2026)", 198, 201, 1_180, ("s", "blank", "fill")),
    (
        "Regla sintética J (30-09-2026)",
        202,
        260,
        6_012,
        ("k", "blank", "s", "blank", "s", "blank", "s", "blank", "s", "blank", "rest"),
    ),
    ("Privilegios sintéticos de la base (03-10-2026)", 261, 272, 2_226, ("rest",)),
    ("Dos modos sintéticos de auditar", 273, 285, 768, ("rest",)),
    ("Mapa sintético", 286, 362, 5_193, ("blank", "fence", "blank", "fill", "blank")),
    ("Fuentes sintéticas de búsqueda", 363, 379, 1_149, ("sv", "blank", "rest")),
    (
        "Cosas sintéticas que parecen bugs",
        380,
        390,
        2_586,
        ("fill", "blank", "k", "s", "s", "s", "item", "item", "blank", "fill"),
    ),
    (
        "Sala sintética K (12-10-2026)",
        391,
        405,
        4_779,
        (
            "k",
            "blank",
            "s",
            "blank",
            "s",
            "blank",
            "m:Ver src/k1.",
            "m:Ver src/k2.",
            "m:Ver src/k3.",
            "blank",
            "rest",
        ),
    ),
    (
        "Sala sintética L (13-10-2026)",
        406,
        419,
        4_722,
        (
            "k",
            "blank",
            "s",
            "blank",
            "s",
            "blank",
            "m:Ver src/l1.",
            "m:Ver src/l2.",
            "m:Ver src/l3.",
            "blank",
            "rest",
        ),
    ),
    ("Fase sintética M (14-10-2026)", 420, 431, 3_567, ("k", "blank", "s", "blank", "rest")),
    ("Fase sintética N (15-10-2026)", 432, 440, 2_836, ("k", "blank", "s", "blank", "rest")),
    (
        "Fase sintética O, retención sin borrado y anonimización (16-10-2026)",
        441,
        451,
        5_758,
        ("rest",),
    ),
    ("Fase sintética P (17-10-2026)", 452, 462, 3_408, ("k", "blank", "s", "blank", "rest")),
    ("Fase sintética Q (18-10-2026)", 463, 472, 2_888, ("rest",)),
    (
        "Integración sintética R (19-10-2026)",
        473,
        488,
        4_525,
        ("k", "blank", "s", "blank", "s", "blank", "rest"),
    ),
    ("Modo sintético S (20-10-2026)", 489, 501, 3_562, ("s", "blank", "rest")),
    ("Cuenta/eliminar sintética (21-10-2026)", 502, 505, 1_141, ("rest",)),
    (
        "Cliente sintético T (22-10-2026)",
        506,
        517,
        3_084,
        (
            "k",
            "blank",
            "s",
            "blank",
            "m:Ver docs/a docs/b docs/c docs/d src/a src/b src/c functions/a functions/b"
            " api/a api/b.",
            "blank",
            "rest",
        ),
    ),
    ("Pool sintético U (23-10-2026)", 518, 527, 2_768, ("k", "blank", "rest")),
    ("Tamaño sintético V (24-10-2026)", 528, 531, 1_090, ("k", "blank", "fill")),
    ("Estructura sintética W (25-10-2026)", 532, 535, 832, ("k", "blank", "fill")),
    ("Registro sintético X (26-10-2026)", 536, 544, 1_989, ("k", "blank", "rest")),
    ("Marca sintética Y (27-10-2026)", 545, 547, 917, ("k", "fill")),
)


def _utf8(text: str) -> int:
    return len(text.encode("utf-8")) + 1  # the line and its "\n"


def _padding(n: int) -> str:
    """``n`` bytes of neutral padding (no lexicon term, no trailing space)."""
    if n <= 0:
        return ""
    words = " relleno" * (n // 8)
    return words + "z" * (n - len(words))


def _sized(text: str, size: int, *, suffix: str = "") -> str:
    """``text`` + padding + ``suffix`` with exactly ``size`` bytes (newline included)."""
    pad = size - _utf8(text + suffix)
    assert pad >= 0, (text, size)
    return text + _padding(pad) + suffix


class _Line:
    def __init__(self, text: str, variable: bool) -> None:
        self.text = text
        self.variable = variable


def _pieces(uid: int, pieces: Sequence[Piece], n: int, copies: list[int]) -> list[_Line]:
    out: list[_Line] = []
    fixed = sum(1 for p in pieces if p != "rest") + (14 if "group" in pieces else 0)
    fixed += 71 if "fence" in pieces else 0
    for p in pieces:
        if p == "blank":
            out.append(_Line("", False))
        elif p == "fill":
            out.append(_Line(f"Nota {uid}.", True))
        elif p == "rest":
            out += [_Line(f"Nota {uid}.{i}.", True) for i in range(n - fixed)]
        elif p == "k":
            out.append(_Line(f"- Nunca omitas el paso sintético {uid}.", True))
        elif p == "item":
            out.append(_Line(f"- Caso sintético {uid}.{len(out)}.", True))
        elif p == "s":
            text = f"- El campo password sintético {uid}.{len(out)} se valida aparte"
            out.append(_Line(_sized(text, copies.pop(0), suffix="."), False))
        elif p == "sv":
            out.append(_Line(f"El campo password sintético {uid} se valida aparte.", True))
        elif p.startswith("m:"):
            out.append(_Line(p[2:], True))
        elif p == "group":
            head = _sized("Nunca guardes el password sintético fuera del gestor", 300, suffix=":")
            items = [96] * 9 + [97] * 5
            assert 300 + sum(items) == U10_GROUP_BYTES
            out.append(_Line(head, False))
            out += [
                _Line(_sized(f"- Ítem sintético {i}", b, suffix="."), False)
                for i, b in enumerate(items)
            ]
        elif p == "fence":
            sizes = [74] * 40 + [73] * 30
            assert _utf8("```text") + _utf8("```") + sum(sizes) == U16_FENCE_BYTES
            body = [_sized("benchmarks/ benchmark sintético", sizes[0])]
            body += [_sized(f"carpeta-sintética-{i:02d}/", b) for i, b in enumerate(sizes[1:], 1)]
            out += [_Line(x, False) for x in ("```text", *body, "```")]
        else:
            raise ValueError(p)
    assert len(out) == n, (uid, len(out), n)
    return out


def _unit_lines(
    uid: int, heading: str | None, n: int, size: int, pieces: Sequence[Piece], copies: list[int]
) -> list[str]:
    head = _Line("# Proyecto sintético" if heading is None else f"## {heading}", False)
    body = _pieces(uid, pieces, n - 1, copies)
    lines = [head, *body]
    missing = size - sum(_utf8(x.text) for x in lines)
    variable = [x for x in body if x.variable] or [head]
    assert missing >= 0, (uid, missing)
    share, extra = divmod(missing, len(variable))
    for i, x in enumerate(variable):
        add = share + (1 if i < extra else 0)
        if x.text.endswith("."):
            x.text = x.text[:-1] + _padding(add) + "."  # keep the sentence end
        else:
            x.text += _padding(add)
    return [x.text for x in lines]


def large_synthetic_lines() -> list[str]:
    """547 synthetic lines with fixed unit ranges and bytes."""
    lines: list[str] = []
    copies = list(_COPY_SIZES)
    for uid, (heading, first, last, size, pieces) in enumerate(LARGE_ROWS):
        assert len(lines) + 1 == first, (uid, len(lines), first)
        unit = _unit_lines(uid, heading, last - first + 1, size, pieces, copies)
        assert sum(_utf8(x) for x in unit) == size, uid
        lines += unit
    assert not copies
    assert len(lines) == 547
    return lines


def large_synthetic_project(make_project: Callable[..., Project]) -> Project:
    """The large synthetic project: ``CLAUDE.md`` with 547 lines and its top-level folders."""
    project = make_project()
    for d in LARGE_DIRS:
        (project.root / d).mkdir(parents=True, exist_ok=True)
    raw = "".join(x + "\n" for x in large_synthetic_lines()).encode("utf-8")
    (project.root / "CLAUDE.md").write_bytes(raw)
    return project


# --------------------------------------------------------------------------- plan helpers


def accept_pending(project: Project, cfg: Config, reorg_id: str, now: datetime) -> None:
    """Record every pending confirmation of a planned run, as a human would with ``set``:
    ``dest: L1c`` for an L1c unit, ``accept`` for the rest (spec §5.8)."""
    m, _ = mf.load_run(project.root, reorg_id)
    for uid in mf.pending_units(m):
        unit = next(u for u in m.units if u.id == uid)
        if unit.dest == "L1c":
            mf.set_override(project, cfg, reorg_id, uid, dest="L1c", reason="sintético", now=now)
        else:
            mf.set_override(project, cfg, reorg_id, uid, accept=True, reason="sintético", now=now)
