"""classify: features, destination rules, safety passes, budget and overrides (spec §5).

Synthetic sources only, built in memory as bytes. No real project content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context.model import Decision, SourceDoc, Unit
from declutter_context.reorg import classify, mdblocks
from declutter_context.reorg.classify import (
    BlockOverride,
    ClassifyContext,
    CopySpec,
    Overrides,
    UnitOverride,
)
from declutter_context.reorg.lexicon import build_lexicon

LEX = build_lexicon({})


def doc(*sections: str) -> bytes:
    """Join sections (each already ending in a newline or not) into one UTF-8 source."""
    return "".join(sections).encode("utf-8")


def ctx_for(
    sources: Sequence[str], *, top_dirs: Sequence[str] = (), paths_lang: str = "es"
) -> ClassifyContext:
    return ClassifyContext(
        lexicon=LEX,
        top_dirs=frozenset(top_dirs),
        paths_lang=paths_lang,
        agents_source="AGENTS.md" if "AGENTS.md" in sources else None,
        claude_source="CLAUDE.md" if "CLAUDE.md" in sources else None,
    )


class Run:
    """Parse, compute features and decide for a set of sources (CLAUDE.md first)."""

    def __init__(
        self,
        sources: Mapping[str, bytes],
        *,
        top_dirs: Sequence[str] = (),
        paths_lang: str = "es",
    ) -> None:
        order = sorted(sources, key=lambda p: (p != "CLAUDE.md", p != "AGENTS.md", p))
        self.docs: dict[str, SourceDoc] = {p: mdblocks.parse(sources[p], p) for p in order}
        self.ctx = ctx_for(order, top_dirs=top_dirs, paths_lang=paths_lang)
        raw_units = mdblocks.parse_sources([self.docs[p] for p in order])
        self.units: tuple[Unit, ...] = tuple(
            classify.features(u, self.docs[u.source], self.ctx) for u in raw_units
        )
        self.decisions: tuple[Decision, ...] = tuple(
            classify.decide(u, self.ctx) for u in self.units
        )

    def unit(self, heading: str) -> Unit:
        return next(u for u in self.units if u.heading == heading or u.id == heading)

    def dec(self, heading: str) -> Decision:
        uid = self.unit(heading).id
        return next(d for d in self.decisions if d.unit == uid)

    def copies(self, ov: Overrides | None = None) -> tuple[CopySpec, ...]:
        return classify.copies(self.units, self.docs, self.decisions, ov or Overrides())


def one(text: str, **kw: object) -> Run:
    return Run({"CLAUDE.md": doc(text)}, **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- rule table


@pytest.mark.parametrize(
    ("source", "text", "heading", "dest", "reasons", "protected", "confidence"),
    [
        (
            "CLAUDE.md",
            "## Algo\n<!-- declutter:gen historial 1 -->\nx\n"
            "<!-- declutter:gen-end historial -->\n",
            "Algo",
            "L1c",
            {"generated", "rule:1"},
            False,
            "high",
        ),
        (
            "AGENTS.md",
            "## Algo\n\n<!-- declutter:gen invariants 1 -->\nx\n"
            "<!-- declutter:gen-end invariants -->\n",
            "Algo",
            "L1",
            {"generated", "rule:1"},
            False,
            "high",
        ),
        ("CLAUDE.md", "intro\n## Otra\nx\n", "U00", "L1", {"rule:2"}, False, "high"),
        (
            "CLAUDE.md",
            "## Datos de pacientes\nx\n",
            "Datos de pacientes",
            "L1",
            {"G2:clinical", "rule:3"},
            True,
            "high",
        ),
        (
            "CLAUDE.md",
            "## Notas\nver @docs/x.md\n",
            "Notas",
            "L1",
            {"imports", "rule:3b"},
            False,
            "high",
        ),
        (
            "CLAUDE.md",
            "## Trampas conocidas\n- x\n",
            "Trampas conocidas",
            "L5t",
            {"rule:4"},
            False,
            "high",
        ),
        ("CLAUDE.md", "## Mapa del repo\n- x\n", "Mapa del repo", "L5m", {"rule:5"}, False, "high"),
        (
            "CLAUDE.md",
            "## Regla de costo (2026-10-01)\nx\n",
            "Regla de costo (2026-10-01)",
            "L1",
            {"dated_rule", "rule:6", "D:date=2026-10-01", "R:title"},
            False,
            "high",
        ),
        (
            "CLAUDE.md",
            "## Sala fase 1 (2026-10-05)\nx\n",
            "Sala fase 1 (2026-10-05)",
            "L4",
            {"rule:7", "D:date=2026-10-05", "K=0", "S=0"},
            False,
            "high",
        ),
        (
            "CLAUDE.md",
            "## Uso de hooks\n- configura los hooks\n",
            "Uso de hooks",
            "L1c",
            {"rule:8"},
            False,
            "high",
        ),
        (
            "CLAUDE.md",
            "## Cómo correrlo\n- npm test\n",
            "Cómo correrlo",
            "L1",
            {"rule:9"},
            False,
            "high",
        ),
        (
            "CLAUDE.md",
            "## Frontend\n" + "".join(f"- ver src/a{i}.py\n" for i in range(5)),
            "Frontend",
            "L2:src",
            {"rule:10"},
            False,
            "medium",
        ),
        ("CLAUDE.md", "## Varios\nalgo\n", "Varios", "L1", {"rule:11", "review"}, False, "low"),
    ],
)
def test_rule_table(
    source: str,
    text: str,
    heading: str,
    dest: str,
    reasons: set[str],
    protected: bool,
    confidence: str,
) -> None:
    r = Run({source: doc(text)}, top_dirs=("src",))
    d = r.dec(heading)
    assert d.dest == dest
    assert reasons <= set(d.reasons)
    assert d.protected is protected
    assert d.confidence == confidence


def test_rule_files_by_paths_lang() -> None:
    text = (
        "## Trampas\n- x\n## Mapa\n- x\n## Fase (2026-10-05)\nx\n## Hito (rama `x`)\nx\n"
        "## Frontend\n" + "".join(f"- ver src/a{i}.py\n" for i in range(5))
    )
    es = one(text, top_dirs=("src",))
    en = one(text, top_dirs=("src",), paths_lang="en")
    files = [(d.dest, d.file) for d in es.decisions]
    assert files == [
        ("L5t", "docs/trampas.md"),
        ("L5m", "docs/mapa.md"),
        ("L4", "docs/historial/2026-10.md"),
        ("L4", "docs/historial/sin-fecha.md"),
        ("L2:src", "src/AGENTS.md"),
    ]
    assert [d.file for d in en.decisions] == [
        "docs/pitfalls.md",
        "docs/map.md",
        "docs/history/2026-10.md",
        "docs/history/undated.md",
        "src/AGENTS.md",
    ]


def test_claude_preamble_generated_is_rule1() -> None:
    text = (
        "@AGENTS.md\n\n<!-- declutter:gen claude 1 -->\nfoo\n"
        "<!-- declutter:gen-end claude -->\n## X\na\n"
    )
    r = Run({"CLAUDE.md": doc(text), "AGENTS.md": doc("## Y\nb\n")})
    d = r.dec("U00")
    assert (d.dest, d.file) == ("L1c", "CLAUDE.md")
    assert "generated" in d.reasons and not d.needs_confirmation


def test_claude_preamble_with_user_text_is_rule2() -> None:
    text = "@AGENTS.md\nmi nota\n## X\na\n"
    r = Run({"CLAUDE.md": doc(text), "AGENTS.md": doc("## Y\nb\n")})
    assert r.dec("U00").dest == "L1"
    assert "rule:2" in r.dec("U00").reasons


def test_rule_ties() -> None:
    r = one(
        "## Trampas del mapa\n- x\n"
        "## Datos de pacientes (2026-10-01)\n- x\n"
        "## Fase 2 (2026-10-02)\n@docs/x.md\n"
        "## Regla de fase (2026-10-03)\nx\n"
        "## Uso de hooks\n- configura los hooks\n- la contraseña va en hooks\n"
    )
    assert r.dec("Trampas del mapa").dest == "L5t"
    g2 = r.dec("Datos de pacientes (2026-10-01)")
    assert (g2.dest, g2.protected) == ("L1", True)
    assert "rule:3b" in r.dec("Fase 2 (2026-10-02)").reasons
    assert r.dec("Fase 2 (2026-10-02)").dest == "L1"
    assert "rule:6" in r.dec("Regla de fase (2026-10-03)").reasons
    q = r.dec("Uso de hooks")
    assert q.dest == "L1"
    assert "claude_only_with_safety" in q.reasons


def test_q_ratio_border_49_50() -> None:
    half = one("## Uso\n- configura los hooks\n- otra cosa\n")
    assert half.unit("Uso").features.q_ratio == 0.5
    assert half.dec("Uso").dest == "L1c"
    items = "".join("- usa hooks\n" for _ in range(49)) + "".join(
        f"- otra cosa {i}\n" for i in range(51)
    )
    below = one("## Uso\n" + items)
    assert below.unit("Uso").features.q_ratio == 0.49
    assert below.dec("Uso").dest == "L1"


def _mentions(src: int, other: int) -> str:
    return " ".join(["src/x"] * src + ["tests/y"] * other) + "\n"


@pytest.mark.parametrize(
    ("src", "other", "pointer"),
    [(59, 41, None), (60, 40, "zone:src"), (2, 0, None), (3, 0, "zone:src")],
)
def test_zone_pointer_border_59_60_and_2_3_mentions(
    src: int, other: int, pointer: str | None
) -> None:
    r = one(
        "## Fase (2026-10-05)\nNunca toques esto.\n" + _mentions(src, other),
        top_dirs=("src", "tests"),
    )
    d = r.dec("Fase (2026-10-05)")
    assert (d.dest, d.card) == ("L4", True)
    assert d.pointer == pointer


@pytest.mark.parametrize(
    ("src", "other", "dest"),
    [(69, 31, "L1"), (70, 30, "L2:src"), (4, 0, "L1"), (5, 0, "L2:src")],
)
def test_l2_border_69_70_and_4_5_mentions(src: int, other: int, dest: str) -> None:
    r = one("## Frontend\n" + _mentions(src, other), top_dirs=("src", "tests"))
    assert r.dec("Frontend").dest == dest


def test_zone_counts_only_top_dirs_and_ties_alphabetical() -> None:
    r = one("## X\nver b/1 a/2 nope/3 .hidden/4\n", top_dirs=("a", "b"))
    f = r.unit("X").features
    assert (f.zone, f.zone_ratio, f.zone_mentions) == ("a", 0.5, 1)


def test_unit_without_blocks_has_zero_q_and_no_zone() -> None:
    r = one("## Vacía\n\n\n## Otra\nx\n")
    f = r.unit("Vacía").features
    assert r.unit("Vacía").blocks == ()
    assert (f.q_ratio, f.zone, f.zone_ratio, f.zone_mentions) == (0.0, None, 0.0, 0)


def test_block_flags_and_counts() -> None:
    r = one(
        "## X\nNunca borrar datos personales.\n\nSe decidió usar A en lugar de B.\n\n"
        "No es un bug.\n\nUsa hooks.\n"
    )
    u = r.unit("X")
    b1, b2, b3, b4 = u.blocks
    assert b1.imperative and "clinical" in b1.categories and "irreversible" in b1.categories
    assert b2.decision and not b2.imperative
    assert b3.trap
    assert b4.claude_only
    f = u.features
    assert (f.k, f.s, f.t, f.q_ratio) == (1, 1, 1, 0.25)


def test_imports_ignore_fences_and_comments() -> None:
    r = one("## X\n```\n@a.md\n```\n<!--\n@b.md\n-->\nver @c.md y @d.md\n")
    assert r.unit("X").features.imports == 1


def test_branch_feature() -> None:
    r = one("## Hito de la rama `feat/x`\nx\n")
    f = r.unit("Hito de la rama `feat/x`").features
    assert (f.branch, f.dated) == ("feat/x", "undated")


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Hito 2026-10-01", "2026-10-01"),
        ("Hito 01-10-2026", "2026-10-01"),
        ("Hito 10-13-2026", "2026-10-13"),
        ("Hito 31-02-2026", "undated"),
        ("Hito 13-13-2026", "undated"),
        ("Hito 2026-13-01", "undated"),
        ("Hito del 5 de octubre", "undated"),
        ("Hito de la rama `x`", "undated"),
        ("Hito sin fecha", None),
        ("Dos fechas 03-10-2026 y 2026-09-01", "2026-10-03"),
    ],
)
def test_normalize_date(heading: str, expected: str | None) -> None:
    assert classify.normalize_date(heading) == expected


def test_agents_preamble_is_U00b_rule2() -> None:
    r = Run({"CLAUDE.md": doc("## A\nx\n"), "AGENTS.md": doc("intro\n## B\ny\n")})
    u = r.unit("U00b")
    assert u.source == "AGENTS.md"
    d = r.dec("U00b")
    assert (d.dest, d.file) == ("L1", "AGENTS.md")
    assert "rule:2" in d.reasons


@pytest.mark.parametrize(
    "text",
    [
        "## Uso de hooks\n- configura los hooks\n",
        "## Frontend\n" + "".join(f"- ver src/a{i}.py\n" for i in range(5)),
    ],
)
def test_agents_unit_matching_rule8_or_10_stays_l1(text: str) -> None:
    r = Run({"AGENTS.md": doc(text)}, top_dirs=("src",))
    d = r.decisions[0]
    assert (d.dest, d.file) == ("L1", "AGENTS.md")


def test_inline_triple_backticks_then_safety_h2_is_g2() -> None:
    r = one("## Intro\n```npm run dev``` arranca el servidor\n## Datos de pacientes\n- uno\n")
    d = r.dec("Datos de pacientes")
    assert (d.dest, d.protected) == ("L1", True)


# --------------------------------------------------------------------------- card and pointer


def _override_dest(r: Run, heading: str, dest: str) -> Decision:
    ov = Overrides(units=(UnitOverride(r.unit(heading).heading_key, "dest", dest, "motivo"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert issues == ()
    uid = r.unit(heading).id
    return next(d for d in decs if d.unit == uid)


def test_card_and_pointer_rules() -> None:
    zone = "".join(f"- ver src/a{i}.py\n" for i in range(3))
    r = one(
        "## Regla A (2026-10-01)\nx\n"
        "## Regla B (2026-10-02)\n"
        + zone
        + "## Fase C (2026-10-03)\nNunca X.\n"
        + zone
        + "## Fase D (2026-10-04)\nNunca X.\n"
        "## Fase E (2026-10-05)\nnada\n" + zone + "## Trampas\n- x\n## Mapa\n- x\n",
        top_dirs=("src",),
    )
    a = _override_dest(r, "Regla A (2026-10-01)", "L4")
    assert (a.card, a.pointer) == (True, "root")
    b = _override_dest(r, "Regla B (2026-10-02)", "L4")
    assert (b.card, b.pointer) == (True, "root+zone:src")
    c = r.dec("Fase C (2026-10-03)")
    assert (c.card, c.pointer) == (True, "zone:src")
    d = r.dec("Fase D (2026-10-04)")
    assert (d.card, d.pointer) == (True, None)
    e = r.dec("Fase E (2026-10-05)")
    assert (e.card, e.pointer) == (False, None)
    assert r.dec("Trampas").pointer == "root"
    assert r.dec("Mapa").pointer == "root"
    assert r.dec("Trampas").card is False


# --------------------------------------------------------------------------- G1 and passes


def _inv(copies: Sequence[CopySpec]) -> list[tuple[tuple[int, int], str]]:
    return [(c.src, c.pin) for c in copies if c.target == "invariants"]


def test_g1_copy_whole_block() -> None:
    r = one("## Fase (2026-10-05)\nTexto con datos\npersonales y datos personales.\n\nnada\n")
    cs = r.copies()
    assert _inv(cs) == [((2, 3), "block")]
    c = cs[0]
    assert (c.unit, c.block, c.source) == ("U01", "U01.b1", "CLAUDE.md")
    assert "clinical" in c.categories


def test_g1_heading_copies_subsection() -> None:
    r = one("## Fase (2026-10-05)\n### Datos de pacientes\n- uno\n\n- dos\n### Otra\n- tres\n")
    assert _inv(r.copies()) == [((2, 5), "subsection")]


def test_g1_subsection_runs_to_unit_end_and_stops_only_at_same_or_higher() -> None:
    r = one("## Fase (2026-10-05)\n### Datos de pacientes\n#### Sub\n- uno\n## Otra\nx\n")
    assert _inv(r.copies()) == [((2, 4), "subsection")]


def test_g1_list_group_copied_whole() -> None:
    r = one("## Fase (2026-10-05)\nReglas para datos de pacientes:\n- uno\n\n- dos\n\nfin\n")
    assert _inv(r.copies()) == [((2, 5), "list_group")]


def test_g1_fence_copied_whole_when_match_on_opening() -> None:
    r = one("## Fase (2026-10-05)\nantes\n\n```sql -- RLS\nselect 1;\nselect 2;\n```\n")
    assert _inv(r.copies()) == [((4, 7), "block")]


def test_g1_contained_copy_not_emitted() -> None:
    r = one("## Fase (2026-10-05)\n### Datos de pacientes\n- paciente uno\n- dos\n")
    assert _inv(r.copies()) == [((2, 4), "subsection")]


def test_g1_guard_ack_on_heading_disables_expansion() -> None:
    r = one("## Fase (2026-10-05)\n### Datos de pacientes\n- paciente uno\n- dos\n")
    head = r.unit("Fase (2026-10-05)").blocks[0]
    ov = Overrides(blocks=(BlockOverride(head.key, "falso positivo del título"),))
    assert _inv(r.copies(ov)) == [((3, 3), "block")]


def test_identical_blocks_single_ack_covers_only_its_own() -> None:
    r = one("## Fase (2026-10-05)\nUsa la contraseña X.\n\nUsa la contraseña X.\n")
    b1, b2 = r.unit("Fase (2026-10-05)").blocks
    assert b2.key == b1.key + "~2"
    ov = Overrides(blocks=(BlockOverride(b1.key, "campo de formulario"),))
    assert _inv(r.copies(ov)) == [((4, 4), "block")]


def test_g1_not_applied_to_l1_units() -> None:
    r = one("## Cómo correrlo\nusa la contraseña\n")
    assert r.copies() == ()


def test_g1_applies_to_l5t_without_trap_copy() -> None:
    r = one("## Trampas\n- la contraseña no es un bug\n")
    assert [(c.target, c.src) for c in r.copies()] == [("invariants", (2, 2))]


def test_trap_pass_copies_to_trampas_except_l5t() -> None:
    r = one("## Fase (2026-10-05)\nEsto no es un bug.\n## Trampas\n- no es un bug\n")
    traps = [(c.unit, c.src, c.pin) for c in r.copies() if c.target == "trampas"]
    assert traps == [("U01", (2, 2), "block")]


def test_card_excerpt_pass_imperative_or_decision() -> None:
    r = one("## Fase (2026-10-05)\nNunca X.\n\nSe decidió Y.\n\nOtro.\n")
    cards = [c.src for c in r.copies() if c.target == "card"]
    assert cards == [(2, 2), (4, 4)]


def test_no_card_excerpts_without_card() -> None:
    r = one("## Fase (2026-10-05)\nSe decidió Y.\n")
    assert r.dec("Fase (2026-10-05)").card is False
    assert [c for c in r.copies() if c.target == "card"] == []


# --------------------------------------------------------------------------- overrides


def _ov_run() -> Run:
    return one(
        "## Regla de costo (2026-10-01)\nx\n"
        "## Datos de pacientes\nx\n"
        "## Fase (2026-10-05)\nNunca X.\n"
        "## Varios\nalgo\n"
    )


def test_overrides_dest_counts_as_acceptance() -> None:
    r = _ov_run()
    key = r.unit("Fase (2026-10-05)").heading_key
    assert "U03" in classify.pending_confirmations(r.units, r.decisions, Overrides())
    ov = Overrides(units=(UnitOverride(key, "dest", "L4", "lo revisé"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert issues == ()
    assert "U03" not in classify.pending_confirmations(r.units, decs, ov)


def test_overrides_accept_marks_acceptance() -> None:
    r = _ov_run()
    key = r.unit("Fase (2026-10-05)").heading_key
    ov = Overrides(units=(UnitOverride(key, "accept", True, "lo revisé"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert issues == ()
    assert "override:accept" in next(d for d in decs if d.unit == "U03").reasons
    assert "U03" not in classify.pending_confirmations(r.units, decs, ov)


def test_overrides_dest_l1_on_dated_rule_prevents_demotion() -> None:
    r = _ov_run()
    key = r.unit("Regla de costo (2026-10-01)").heading_key
    ov = Overrides(units=(UnitOverride(key, "dest", "L1", "sigue vigente"),))
    decs, _ = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    out = classify.apply_budget(
        r.units, r.docs, decs, ov, ctx=r.ctx, l1_max_bytes=1, agents_size=lambda _d: 10_000
    )
    assert next(d for d in out if d.unit == "U01").dest == "L1"


def test_overrides_card_outside_l4_is_error() -> None:
    r = _ov_run()
    key = r.unit("Varios").heading_key
    ov = Overrides(units=(UnitOverride(key, "card", True, "motivo"),))
    _, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert [(i.code, i.key, i.unit) for i in issues] == [(4, "card_not_l4", "U04")]


def test_overrides_card_on_l4_sets_card() -> None:
    r = _ov_run()
    key = r.unit("Fase (2026-10-05)").heading_key
    ov = Overrides(units=(UnitOverride(key, "card", False, "sin ficha"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert issues == ()
    assert next(d for d in decs if d.unit == "U03").card is False


def test_overrides_dest_on_g2_rejected() -> None:
    r = _ov_run()
    key = r.unit("Datos de pacientes").heading_key
    ov = Overrides(units=(UnitOverride(key, "dest", "L4", "motivo"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert [(i.code, i.key) for i in issues] == [(4, "check.4")]
    assert next(d for d in decs if d.unit == "U02").dest == "L1"
    same = Overrides(units=(UnitOverride(key, "dest", "L1", "motivo"),))
    assert classify.apply_overrides(r.units, r.decisions, same, r.ctx)[1] == ()


def test_overrides_empty_reason_rejected() -> None:
    r = _ov_run()
    key = r.unit("Varios").heading_key
    ov = Overrides(units=(UnitOverride(key, "accept", True, "  "),))
    _, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert [(i.code, i.key) for i in issues] == [(4, "check.8")]


@pytest.mark.parametrize(("reason", "ok"), [("123456789", False), ("1234567890", True)])
def test_overrides_guard_ack_length(reason: str, ok: bool) -> None:
    r = one("## Fase (2026-10-05)\nusa la contraseña\n")
    block = r.unit("Fase (2026-10-05)").blocks[0]
    ov = Overrides(blocks=(BlockOverride(block.key, reason),))
    _, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert (issues == ()) is ok
    if not ok:
        assert [(i.code, i.key, i.block) for i in issues] == [(4, "check.8", block.id)]
        # An invalid ack never removes a pin.
        assert _inv(r.copies(ov)) == [((2, 2), "block")]
    else:
        assert _inv(r.copies(ov)) == []


def test_overrides_ambiguous_key() -> None:
    r = one("## A\nx\n")
    u = r.units[0]
    twin = replace(u, id="U09")
    units = (u, twin)
    decisions = (r.decisions[0], replace(r.decisions[0], unit="U09"))
    ov = Overrides(units=(UnitOverride(u.heading_key, "accept", True, "motivo"),))
    _, issues = classify.apply_overrides(units, decisions, ov, r.ctx)
    assert [(i.code, i.key) for i in issues] == [(4, "ambiguous_override")]


def test_overrides_keep_and_dest_not_l1_conflict() -> None:
    r = _ov_run()
    key = r.unit("Regla de costo (2026-10-01)").heading_key
    ov = Overrides(
        units=(
            UnitOverride(key, "keep", True, "motivo"),
            UnitOverride(key, "dest", "L4", "motivo"),
        )
    )
    _, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert [(i.code, i.key) for i in issues] == [(4, "check.12")]


@pytest.mark.parametrize("dest", ["L3", "L2:nope", "L2:"])
def test_overrides_invalid_dest(dest: str) -> None:
    r = _ov_run()
    key = r.unit("Varios").heading_key
    ov = Overrides(units=(UnitOverride(key, "dest", dest, "motivo"),))
    _, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert [(i.code, i.key) for i in issues] == [(4, "check.3")]


def test_overrides_dest_l2_recomputes_file() -> None:
    r = one("## Varios\nalgo\n", top_dirs=("src",))
    d = _override_dest(r, "Varios", "L2:src")
    assert (d.dest, d.file, d.card, d.pointer) == ("L2:src", "src/AGENTS.md", False, None)


def test_overrides_keep_moves_to_l1() -> None:
    r = _ov_run()
    key = r.unit("Fase (2026-10-05)").heading_key
    ov = Overrides(units=(UnitOverride(key, "keep", True, "sigue vigente"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert issues == ()
    d = next(d for d in decs if d.unit == "U03")
    assert (d.dest, d.file, d.card, d.pointer) == ("L1", "AGENTS.md", False, None)


def test_overrides_unknown_key_is_ignored() -> None:
    r = _ov_run()
    ov = Overrides(units=(UnitOverride("ffffffffffff", "accept", True, "motivo"),))
    decs, issues = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    assert (decs, issues) == (r.decisions, ())


def test_overrides_for_unit_and_ack() -> None:
    ov = Overrides(
        units=(UnitOverride("a", "accept", True, "x"), UnitOverride("b", "keep", True, "y")),
        blocks=(BlockOverride("a:1", "suficientemente largo"), BlockOverride("a:2", "corto")),
    )
    assert [o.field for o in ov.for_unit("a")] == ["accept"]
    assert ov.ack("a:1") == "suficientemente largo"
    assert ov.ack("a:2") == ""
    assert ov.ack("zz") == ""


# --------------------------------------------------------------------------- budget


def _budget_run() -> Run:
    return one(
        "## Regla A (2026-10-01)\n" + "a" * 100 + "\n"
        "## Regla B (2026-10-02)\n" + "b" * 300 + "\n"
        "## Regla C (2026-10-03)\n" + "c" * 200 + "\n"
        "## Cómo correrlo\nx\n"
    )


def _size(r: Run) -> classify.SizeFn:
    def size(decisions: Sequence[Decision]) -> int:
        by_id = {d.unit: d for d in decisions}
        total = 0
        for u in r.units:
            if by_id[u.id].dest == "L1":
                total += classify.unit_bytes(r.docs[u.source], u.start, u.end)
        return total

    return size


def test_unit_bytes() -> None:
    d = mdblocks.parse(b"ab\r\ncd\n", "CLAUDE.md")
    assert classify.unit_bytes(d, 1, 2) == 7
    assert classify.unit_bytes(d, 2, 2) == 3


def test_budget_demotes_largest_first() -> None:
    r = _budget_run()
    size = _size(r)
    total = size(r.decisions)
    out = classify.apply_budget(
        r.units,
        r.docs,
        r.decisions,
        Overrides(),
        ctx=r.ctx,
        l1_max_bytes=total - 50,
        agents_size=size,
    )
    demoted = [d.unit for d in out if "budget:demoted" in d.reasons]
    assert demoted == ["U02"]
    d = next(d for d in out if d.unit == "U02")
    assert (d.dest, d.file, d.card, d.pointer) == ("L4", "docs/historial/2026-10.md", True, "root")


def test_budget_stops_when_agents_fits() -> None:
    r = _budget_run()
    size = _size(r)
    total = size(r.decisions)
    out = classify.apply_budget(
        r.units,
        r.docs,
        r.decisions,
        Overrides(),
        ctx=r.ctx,
        l1_max_bytes=total - 350,
        agents_size=size,
    )
    assert [d.unit for d in out if "budget:demoted" in d.reasons] == ["U02", "U03"]
    out2 = classify.apply_budget(
        r.units, r.docs, r.decisions, Overrides(), ctx=r.ctx, l1_max_bytes=total, agents_size=size
    )
    assert out2 == r.decisions
    out3 = classify.apply_budget(
        r.units, r.docs, r.decisions, Overrides(), ctx=r.ctx, l1_max_bytes=0, agents_size=size
    )
    assert [d.unit for d in out3 if "budget:demoted" in d.reasons] == ["U01", "U02", "U03"]


def test_budget_skips_nonpositive_net_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _budget_run()
    size = _size(r)
    real = classify.copies

    def fake(
        units: Sequence[Unit],
        docs: Mapping[str, SourceDoc],
        decisions: Sequence[Decision],
        ov: Overrides,
    ) -> tuple[CopySpec, ...]:
        out = real(units, docs, decisions, ov)
        if any(d.unit == "U02" and d.dest == "L4" for d in decisions):
            cat = frozenset({"secrets"})
            out += (CopySpec("U02", "U02.b1", "CLAUDE.md", (3, 4), "block", "invariants", cat),)
        return out

    monkeypatch.setattr(classify, "copies", fake)
    out = classify.apply_budget(
        r.units, r.docs, r.decisions, Overrides(), ctx=r.ctx, l1_max_bytes=0, agents_size=size
    )
    assert [d.unit for d in out if "budget:demoted" in d.reasons] == ["U01", "U03"]


@pytest.mark.parametrize(
    ("text", "keep"),
    [
        ("## Regla de pacientes (2026-10-01)\n" + "x" * 50 + "\n", False),
        ("## Regla A (2026-10-01)\n" + "x" * 50 + "\n", True),
        ("## Regla A (2026-10-01)\n@docs/x.md\n" + "x" * 50 + "\n", False),
        ("## Regla de la rama `x`\n" + "x" * 50 + "\n", False),
        ("## Regla A (2026-13-40)\n" + "x" * 50 + "\n", False),
    ],
    ids=["g2", "keep", "imports", "undated_branch", "undated_invalid"],
)
def test_budget_never_demotes_g2_keep_explicit_l1_imports_or_undated(text: str, keep: bool) -> None:
    r = one(text)
    ov = Overrides()
    decisions = r.decisions
    if keep:
        ov = Overrides(units=(UnitOverride(r.units[0].heading_key, "keep", True, "vigente"),))
        decisions, issues = classify.apply_overrides(r.units, decisions, ov, r.ctx)
        assert issues == ()
    out = classify.apply_budget(
        r.units, r.docs, decisions, ov, ctx=r.ctx, l1_max_bytes=0, agents_size=lambda _d: 10_000
    )
    assert out[0].dest == "L1"
    assert "budget:demoted" not in out[0].reasons


def test_budget_keep_explicit_dest_l1_param() -> None:
    r = one("## Regla A (2026-10-01)\nx\n")
    ov = Overrides(units=(UnitOverride(r.units[0].heading_key, "dest", "L1", "vigente"),))
    decisions, _ = classify.apply_overrides(r.units, r.decisions, ov, r.ctx)
    out = classify.apply_budget(
        r.units, r.docs, decisions, ov, ctx=r.ctx, l1_max_bytes=0, agents_size=lambda _d: 10_000
    )
    assert out[0].dest == "L1"


def test_budget_demoted_needs_confirmation() -> None:
    r = _budget_run()
    out = classify.apply_budget(
        r.units, r.docs, r.decisions, Overrides(), ctx=r.ctx, l1_max_bytes=0, agents_size=_size(r)
    )
    fin = classify.finalize(r.units, out, Overrides())
    for uid in ("U01", "U02", "U03"):
        d = next(d for d in fin if d.unit == uid)
        assert d.needs_confirmation and d.confidence == "medium"
    assert set(classify.pending_confirmations(r.units, fin, Overrides())) == {"U01", "U02", "U03"}


# --------------------------------------------------------------------------- confidence, order


def test_confidence_levels() -> None:
    r = one(
        "## Varios\nalgo\n"
        "## Fase (2026-10-05)\nNunca X.\n"
        "## Fase 2 (2026-10-06)\nnada\n"
        "## Frontend\n" + "".join(f"- ver src/a{i}.py\n" for i in range(5)),
        top_dirs=("src",),
    )
    fin = classify.finalize(r.units, r.decisions, Overrides())
    assert [d.confidence for d in fin] == ["low", "medium", "high", "medium"]
    assert [d.needs_confirmation for d in fin] == [False, True, False, False]


def test_review_order() -> None:
    r = one(
        "## Cómo correrlo\nx\n"  # U01 high
        "## Fase S (2026-10-01)\nNunca borres datos personales.\n"  # U02 medium, K>=1, S>=1
        "## Varios\nalgo\n"  # U03 low
        "## Fase K (2026-10-02)\nNunca X.\n"  # U04 medium, K>=1, S=0 -> group 2
        "## Frontend\n"
        + "".join(f"- ver src/a{i}.py\n" for i in range(5))  # U05 medium
        + "## Otra cosa\nmás\n",  # U06 low
        top_dirs=("src",),
    )
    fin = classify.finalize(r.units, r.decisions, Overrides())
    assert classify.review_order(r.units, fin) == ("U03", "U06", "U04", "U02", "U05", "U01")


def test_pending_confirmations_l1c_requires_dest_override_not_accept() -> None:
    r = one("## Uso de hooks\n- configura los hooks\n")
    key = r.units[0].heading_key
    assert r.decisions[0].needs_confirmation
    assert classify.pending_confirmations(r.units, r.decisions, Overrides()) == ("U01",)
    accept = Overrides(units=(UnitOverride(key, "accept", True, "ok"),))
    decs, _ = classify.apply_overrides(r.units, r.decisions, accept, r.ctx)
    assert classify.pending_confirmations(r.units, decs, accept) == ("U01",)
    confirm = Overrides(units=(UnitOverride(key, "dest", "L1c", "solo Claude"),))
    decs, _ = classify.apply_overrides(r.units, r.decisions, confirm, r.ctx)
    assert classify.pending_confirmations(r.units, decs, confirm) == ()


def test_generated_l1c_needs_no_confirmation() -> None:
    r = one(
        "## Algo\n<!-- declutter:gen claude-only 1 -->\nusa hooks\n"
        "<!-- declutter:gen-end claude-only -->\n"
    )
    assert r.decisions[0].dest == "L1c"
    assert not r.decisions[0].needs_confirmation
    assert classify.pending_confirmations(r.units, r.decisions, Overrides()) == ()


# --------------------------------------------------------------------------- top-level dirs


def test_top_level_dirs(make_project: MakeProject) -> None:
    p = make_project(
        {
            "src/a.py": "",
            "tests/t.py": "",
            ".hidden/x": "",
            "node_modules/x": "",
            "file.md": "",
        }
    )
    assert classify.top_level_dirs(Path(p.root)) == frozenset({"src", "tests"})
