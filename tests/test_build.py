"""build: destination contents in memory, placements and control files (spec §9, §4.3, §12.1).

Synthetic sources only, written with ``write_bytes`` through ``make_project``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import fsutil, ratchet, scaffold
from declutter_context.model import Config, Decision, Placement, Project, SourceDoc, Unit
from declutter_context.reorg import ReorgError, build, classify, mdblocks, projection
from declutter_context.reorg.build import BuildInput, BuildResult
from declutter_context.reorg.classify import ClassifyContext, Overrides, UnitOverride
from declutter_context.reorg.lexicon import build_lexicon

LEX = build_lexicon({})
RID = "20261104-1530"
RID2 = "20261104-1531"
TODAY = date(2026, 11, 4)
BOM = b"\xef\xbb\xbf"

FULL = (
    "---\n"
    "title: demo\n"
    "---\n"
    "# Proyecto demo\n"
    "Intro del proyecto.\n"
    "\n"
    "## Cómo correr\n"
    "Ejecuta `make test`.\n"
    "\n"
    "## Cambio 2026-10-01 rama `x`\n"
    "Se agregó algo.\n"
    "\n"
    "- Nunca uses datos de pacientes reales.\n"
    "- Gotcha: el reloj es local. Luego cambia.\n"
    "\n"
    "## Nota 2026-10-03\n"
    "Solo registro.\n"
    "\n"
    "## Trampas conocidas\n"
    "- El cache tarda. Luego se arregla.\n"
    "- Segundo ítem\n"
    "\n"
    "## Mapa\n"
    "Carpetas principales.\n"
    "\n"
    "## Hooks de Claude Code\n"
    "Configura los hooks y skills en settings.json.\n"
    "\n"
    "## Datos de pacientes\n"
    "Todo dato es confidencial.\n"
    "\n"
    "## Reglas del cache (2026-10-02)\n"
    "- Usa TTL corto.\n"
).encode()

FULL_OV = Overrides(
    units=(
        UnitOverride(
            heading_key=mdblocks.heading_key("Reglas del cache (2026-10-02)"),
            field="dest",
            value="L4",
            reason="motivo de prueba",
        ),
    )
)


@dataclass
class Built:
    project: Project
    inp: BuildInput
    res: BuildResult

    def lines(self, path: str) -> list[str]:
        return [mdblocks.decode_line(x) for x in mdblocks.split_lines(self.res.files[path]).lines]

    def places(self, path: str) -> list[Placement]:
        return sorted((p for p in self.res.placements if p.dest == path), key=lambda p: p.at)

    def unit(self, heading: str) -> Unit:
        return next(u for u in self.inp.units if u.heading == heading or u.id == heading)

    def dec(self, heading: str) -> Decision:
        uid = self.unit(heading).id
        return next(d for d in self.inp.decisions if d.unit == uid)


def _existing(root: Path):  # type: ignore[no-untyped-def]
    def read(rel: str) -> bytes | None:
        try:
            return fsutil.safe_read_bytes(root / rel, root)
        except fsutil.UnsafeReadError:
            return None

    return read


def run(
    make_project: MakeProject,
    sources: Mapping[str, bytes],
    disk: Mapping[str, bytes] | None = None,
    *,
    lang: str = "es",
    paths_lang: str = "es",
    ov: Overrides | None = None,
    dirs: Sequence[str] = (),
    rid: str = RID,
) -> Built:
    project = make_project({})
    root = project.root
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel, data in {**(disk or {}), **sources}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return run_on(project, sources, lang=lang, paths_lang=paths_lang, ov=ov, rid=rid)


def run_on(
    project: Project,
    sources: Mapping[str, bytes],
    *,
    lang: str = "es",
    paths_lang: str = "es",
    ov: Overrides | None = None,
    rid: str = RID,
) -> Built:
    root = project.root
    overrides = ov or Overrides()
    order = sorted(sources, key=lambda s: (s != "CLAUDE.md", s != "AGENTS.md", s))
    docs: dict[str, SourceDoc] = {s: mdblocks.parse(sources[s], s) for s in order}
    ctx = ClassifyContext(
        lexicon=LEX,
        top_dirs=classify.top_level_dirs(root),
        paths_lang=paths_lang,
        agents_source="AGENTS.md" if "AGENTS.md" in docs else None,
        claude_source="CLAUDE.md" if "CLAUDE.md" in docs else None,
    )
    units = tuple(
        classify.features(u, docs[u.source], ctx)
        for u in mdblocks.parse_sources([docs[s] for s in order])
    )
    decs = tuple(classify.decide(u, ctx) for u in units)
    decs, issues = classify.apply_overrides(units, decs, overrides, ctx)
    assert not issues
    inp = BuildInput(
        root=root,
        reorg_id=rid,
        today=TODAY,
        lang=lang,
        paths_lang=paths_lang,
        project_name="demo",
        docs=docs,
        units=units,
        decisions=decs,
        copies=classify.copies(units, docs, decs, overrides),
        overrides=overrides,
        existing=_existing(root),
    )
    return Built(project, inp, build.build_content(inp))


def full(make_project: MakeProject, **kw: object) -> Built:
    return run(make_project, {"CLAUDE.md": FULL}, ov=FULL_OV, **kw)  # type: ignore[arg-type]


def idx(lines: Sequence[str], text: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if lines[i] == text or lines[i].startswith(text):
            return i
    raise AssertionError(f"line not found: {text!r}")


def check_partition(b: Built, path: str) -> None:
    """Placements of a non-control file partition 1..M and each matches its sha."""
    data = b.res.files[path]
    lines = mdblocks.split_lines(data).lines
    ps = b.places(path)
    assert ps, path
    nxt = 1
    for p in ps:
        assert p.at[0] == nxt, (path, p)
        assert p.at[1] >= p.at[0]
        nxt = p.at[1] + 1
        span = lines[p.at[0] - 1 : p.at[1]]
        assert mdblocks.lines_sha(span) == p.sha256, (path, p)
        if p.role in ("primary", "copy"):
            assert p.source is not None and p.src is not None
            src_doc = b.inp.docs[p.source]
            assert list(span) == list(src_doc.lines[p.src[0] - 1 : p.src[1]]), (path, p)
    assert nxt == len(lines) + 1, path


# --------------------------------------------------------------------------- helpers


def test_slug_anchor_and_card_number() -> None:
    assert build.slug("Árbol ñandú — Ünïcode!!  x") == "arbol-nandu-unicode-x"
    assert len(build.slug("palabra " * 20)) <= 40
    assert not build.slug("palabra " * 20).endswith("-")
    assert build.anchor(RID, "U10") == "r20261104-1530-u10"
    assert build.anchor(RID, "U13", "U13.b3") == "r20261104-1530-u13-b3"
    assert build.anchor(RID, "U00b") == "r20261104-1530-u00b"
    names = ["D-004-x.md", "D-000-plantilla.md", "README.md", "D-12-bad.md", "INDICE.md"]
    assert build.next_card_number(names) == 5
    assert build.next_card_number([]) == 1


def test_pitfall_index_prefix_rules() -> None:
    assert build.pitfall_prefix("- Uno. Dos") == "Uno."
    assert build.pitfall_prefix("1. Uno sin punto") == "Uno sin punto"
    long = "x" * 150
    assert build.pitfall_prefix(f"- {long}") == "x" * 100 + "…"
    assert build.pitfall_prefix("- a <!-- b") == "a"
    assert build.pitfall_prefix("```py") == ""
    assert build.pitfall_prefix("  ~~~") == ""
    assert build.pitfall_prefix("Párrafo simple") == "Párrafo simple"


# --------------------------------------------------------------------------- AGENTS.md


def test_agents_md_section_order(make_project: MakeProject) -> None:
    b = full(make_project)
    assert b.dec("Cómo correr").dest == "L1"
    assert b.dec("Datos de pacientes").protected
    assert b.dec("Reglas del cache (2026-10-02)").pointer == "root"
    lines = b.lines("AGENTS.md")
    assert lines[:3] == ["---", "title: demo", "---"]
    fm = b.places("AGENTS.md")[0]
    assert fm.role == "primary" and fm.kind == "front_matter" and fm.src == (1, 3)
    order = [
        idx(lines, f"<!-- declutter:gen agents-header {RID} -->"),
        idx(lines, "# Proyecto demo"),
        idx(lines, "## Invariantes de seguridad (literal, pendiente de destilar)"),
        idx(lines, "## Cómo correr"),
        idx(lines, "## Datos de pacientes"),
        idx(lines, "## Reglas vigentes de origen fechado"),
        idx(lines, "## Trampas (índice)"),
        idx(lines, "## Mapa y zonas"),
        idx(lines, "## Dónde vive cada cosa"),
    ]
    assert order == sorted(order)
    for kind in ("invariants", "dated-rules", "pitfall-index", "map", "where"):
        title = idx(lines, f"<!-- declutter:gen {kind} {RID} -->")
        assert lines[title - 1].startswith("## ")
        assert idx(lines, f"<!-- declutter:gen-end {kind} -->", title) > title
    assert b.res.modes["AGENTS.md"] == "created"
    dated = idx(lines, "## Reglas vigentes de origen fechado")
    assert lines[dated + 2] == (
        "- D-002 Reglas del cache (2026-10-02) → "
        "docs/decisiones/D-002-reglas-del-cache-2026-10-02.md (pendiente de destilar)"
    )
    where = idx(lines, "## Dónde vive cada cosa")
    body = lines[where + 2 : idx(lines, "<!-- declutter:gen-end where -->")]
    for path in ("docs/historial/INDICE.md", "docs/decisiones/INDICE.md", "docs/trampas.md"):
        assert any(path in x for x in body)


def test_g7_header_paths_follow_paths_lang(make_project: MakeProject) -> None:
    es = full(make_project)
    head = "\n".join(es.lines("AGENTS.md")[:8])
    assert "`ESTADO.md`" in head and "`docs/decisiones/INDICE.md`" in head
    assert "Antes de trabajar, lee" in head


@pytest.mark.parametrize("lang", ["es", "en"])
def test_g7_header_paths_en(make_project: MakeProject, lang: str) -> None:
    b = run(make_project, {"CLAUDE.md": FULL}, lang=lang, paths_lang="en", ov=FULL_OV)
    head = "\n".join(b.lines("AGENTS.md")[:8])
    assert "`STATE.md`" in head and "`docs/decisions/INDEX.md`" in head
    assert "ESTADO.md" not in head
    assert "STATE.md" in b.res.files
    assert "docs/history/2026-10.md" in b.res.files
    assert "docs/decisions/INDEX.md" in b.res.files


def test_copy_header_and_blank_after_each_copy(make_project: MakeProject) -> None:
    b = full(make_project)
    lines = b.lines("AGENTS.md")
    copies = [p for p in b.places("AGENTS.md") if p.role == "copy"]
    assert len(copies) == 1
    c = copies[0]
    assert (c.unit, c.block) == ("U02", "U02.b2")
    header = lines[c.at[0] - 2]
    assert header == "### ⟨U02.b2⟩ → docs/historial/2026-10.md#r20261104-1530-u02 · clinical"
    assert lines[c.at[1]] == ""
    by_at = {p.at[0]: p for p in b.places("AGENTS.md")}
    assert by_at[c.at[0] - 1].role == "generated"
    assert by_at[c.at[1] + 1].role == "generated"
    assert lines[c.at[0] - 3] == f"<!-- declutter:gen invariants {RID} -->"


# --------------------------------------------------------------------------- CLAUDE.md


def test_claude_md_rewrite_layout(make_project: MakeProject) -> None:
    b = full(make_project)
    assert b.dec("Hooks de Claude Code").dest == "L1c"
    assert b.res.modes["CLAUDE.md"] == "rewritten"
    lines = b.lines("CLAUDE.md")
    assert lines[0] == "@AGENTS.md"
    assert lines[1] == f"<!-- declutter:gen claude {RID} -->"
    end = idx(lines, "<!-- declutter:gen-end claude -->")
    only = idx(lines, "## Exclusivo de Claude Code", end)
    assert lines[only + 1] == f"<!-- declutter:gen claude-only {RID} -->"
    assert lines[only + 2] == "## Hooks de Claude Code"
    assert lines[-1] == "<!-- declutter:gen-end claude-only -->"
    prim = [p for p in b.places("CLAUDE.md") if p.role == "primary"]
    assert [p.unit for p in prim] == ["U06"]
    check_partition(b, "CLAUDE.md")


def test_claude_md_created_when_only_agents_is_source(make_project: MakeProject) -> None:
    b = run(make_project, {"AGENTS.md": b"# A\n\n## Reglas\n- r\n"})
    assert b.res.modes["CLAUDE.md"] == "created"
    assert b.lines("CLAUDE.md")[0] == "@AGENTS.md"
    assert b.res.modes["AGENTS.md"] == "rewritten"


# --------------------------------------------------------------------------- history


def test_historial_markers_anchor_and_blank_lines(make_project: MakeProject) -> None:
    b = full(make_project)
    path = "docs/historial/2026-10.md"
    assert b.res.modes[path] == "created"
    lines = b.lines(path)
    assert lines[0] == "# Historial 2026-10"
    assert lines[1] == "estado: historico"
    assert lines[2] == f"<!-- declutter:gen historial {RID} -->"
    u = b.unit("Cambio 2026-10-01 rama `x`")
    doc = b.inp.docs["CLAUDE.md"]
    sha = mdblocks.lines_sha(doc.lines[u.start - 1 : u.end])
    a = idx(lines, '<a id="r20261104-1530-u02"></a>')
    assert lines[a + 1] == (
        f"<!-- declutter:src CLAUDE.md:{u.start}-{u.end} sha256={sha} unit=U02 plan={RID} -->"
    )
    end = a + 2 + (u.end - u.start + 1)
    assert lines[end] == "<!-- declutter:end U02 -->"
    assert lines[end + 1] == ""
    assert lines[end + 2] == '<a id="r20261104-1530-u03"></a>'
    units = [p.unit for p in b.places(path) if p.role == "primary"]
    assert units == ["U02", "U03", "U08"]
    markers = [
        (p.unit, p.kind, p.at[1] - p.at[0] + 1) for p in b.places(path) if p.role == "marker"
    ]
    assert markers[:2] == [("U02", "src", 2), ("U02", "end", 1)]
    check_partition(b, path)


def test_last_line_without_newline_then_end_marker(make_project: MakeProject) -> None:
    src = b"# P\n\n## Nota 2026-10-03\nultima linea sin salto"
    b = run(make_project, {"CLAUDE.md": src})
    data = b.res.files["docs/historial/2026-10.md"]
    assert b"ultima linea sin salto\n<!-- declutter:end U01 -->\n" in data
    check_partition(b, "docs/historial/2026-10.md")


def test_history_index_one_row_per_l4_unit(make_project: MakeProject) -> None:
    b = full(make_project)
    lines = b.lines("docs/historial/INDICE.md")
    sec = idx(lines, f"## Añadido por declutter reorg {RID}")
    rows = [x for x in lines[sec:] if x.startswith("| 2026-10 |")]
    assert rows == [
        "| 2026-10 | U02 | Cambio 2026-10-01 rama `x` | [ver](2026-10.md#r20261104-1530-u02) |",
        "| 2026-10 | U03 | Nota 2026-10-03 | [ver](2026-10.md#r20261104-1530-u03) |",
        "| 2026-10 | U08 | Reglas del cache (2026-10-02) | [ver](2026-10.md#r20261104-1530-u08) |",
    ]


# --------------------------------------------------------------------------- decisions


def test_card_layout_numbering_and_slug(make_project: MakeProject) -> None:
    title = "Migración de índices con un título bastante largo (2026-10-01)"
    src = f"# P\n\n## {title}\nTexto.\n\n- Nunca borres la tabla vieja.\n".encode()
    b = run(make_project, {"CLAUDE.md": src}, disk={"docs/decisiones/D-004-x.md": b"# D-004\n"})
    assert b.res.card_ids == {"U01": "D-005"}
    slug = build.slug(title)
    assert slug == "migracion-de-indices-con-un-titulo-basta"
    path = f"docs/decisiones/D-005-{slug}.md"
    assert b.res.modes[path] == "created"
    lines = b.lines(path)
    u = b.unit(title)
    sha = mdblocks.lines_sha(b.inp.docs["CLAUDE.md"].lines[u.start - 1 : u.end])
    assert lines[0] == f"# D-005 {title}"
    assert lines[1] == "estado: pendiente-destilar"
    assert lines[2] == f"<!-- declutter:gen card {RID} -->"
    assert lines[3] == "## Origen"
    assert lines[4] == "- Fecha: 2026-10-01 · Rama: no consta · Zona: no consta"
    assert lines[5] == (
        "- Literal: [docs/historial/2026-10.md#r20261104-1530-u01]"
        "(../historial/2026-10.md#r20261104-1530-u01) · "
        f"CLAUDE.md:{u.start}-{u.end} · sha256 {sha}"
    )
    ext = idx(lines, "## Extractos literales (copia verificada)")
    assert lines[ext + 1] == "- Nunca borres la tabla vieja."
    copies = [p for p in b.places(path) if p.role == "copy"]
    assert [(c.unit, c.block) for c in copies] == [("U01", "U01.b2")]
    check_partition(b, path)


def test_decisions_index_rows_and_registro_sin_ficha(make_project: MakeProject) -> None:
    b = full(make_project)
    path = "docs/decisiones/INDICE.md"
    assert b.res.modes[path] == "created"
    lines = b.lines(path)
    sec = idx(lines, f"## Añadido por declutter reorg {RID}")
    assert "| D-001 | Cambio 2026-10-01 rama `x` | pendiente-destilar | " in "\n".join(lines[sec:])
    row = idx(lines, "| D-001 |", sec)
    assert lines[row].endswith("[ficha](D-001-cambio-2026-10-01-rama-x.md) |")
    sub = idx(lines, "### Registro fechado sin ficha", sec)
    assert lines[sub + 1] == (
        "- U03 Nota 2026-10-03 → [historial](../historial/2026-10.md#r20261104-1530-u03)"
    )
    assert b.res.card_ids == {"U02": "D-001", "U08": "D-002"}


def test_indices_from_template_when_missing_extended_when_present(
    make_project: MakeProject,
) -> None:
    b = full(make_project)
    rendered = scaffold.render_template("es", "docs/historial/INDICE.md", TODAY, "demo")
    first = b.places("docs/historial/INDICE.md")[0]
    assert first.role == "generated"
    assert b.res.files["docs/historial/INDICE.md"].startswith(rendered)
    check_partition(b, "docs/historial/INDICE.md")


def test_index_present_is_extended(make_project: MakeProject) -> None:
    prev = b"# Mi indice\n| a | b |\n"
    b = run(make_project, {"CLAUDE.md": FULL}, disk={"docs/historial/INDICE.md": prev}, ov=FULL_OV)
    path = "docs/historial/INDICE.md"
    assert b.res.modes[path] == "extended"
    data = b.res.files[path]
    assert data.startswith(prev)
    first = b.places(path)[0]
    assert first.role == "extended_prefix"
    assert first.at == (1, 2)
    assert first.backup_sha256 == hashlib.sha256(prev).hexdigest()
    assert not first.prefix_newline_added
    assert b.lines(path)[2] == ""
    assert b.res.prev_sha[path] == hashlib.sha256(prev).hexdigest()
    check_partition(b, path)


def test_estado_created_only_if_missing(make_project: MakeProject) -> None:
    b = full(make_project)
    rendered = scaffold.render_template("es", "ESTADO.md", TODAY, "demo")
    assert b.res.files["ESTADO.md"].startswith(rendered)
    assert b.res.modes["ESTADO.md"] == "created"
    assert b.res.prev_sha["ESTADO.md"] == "absent"
    b2 = run(make_project, {"CLAUDE.md": FULL}, disk={"ESTADO.md": b"# mio\n"}, ov=FULL_OV)
    assert "ESTADO.md" not in b2.res.files


# --------------------------------------------------------------------------- trampas, mapa


def test_trampas_and_mapa_existing_are_extended_never_rewritten(make_project: MakeProject) -> None:
    prev_t = b"# Mis trampas\n- vieja"
    prev_m = b"# Mapa propio\n"
    b = run(
        make_project,
        {"CLAUDE.md": FULL},
        disk={"docs/trampas.md": prev_t, "docs/mapa.md": prev_m},
        ov=FULL_OV,
    )
    for path, prev, added in (("docs/trampas.md", prev_t, True), ("docs/mapa.md", prev_m, False)):
        assert b.res.modes[path] == "extended"
        data = b.res.files[path]
        assert data.startswith(prev + (b"\n" if added else b""))
        p0 = b.places(path)[0]
        assert p0.role == "extended_prefix"
        assert p0.prefix_newline_added is added
        assert p0.backup_sha256 == hashlib.sha256(prev).hexdigest()
        p1 = b.places(path)[1]
        assert p1.role == "generated" and p1.at[0] == p1.at[1]
        assert b.lines(path)[p1.at[0] - 1] == ""
        check_partition(b, path)
    assert b"# Trampas\n" not in b.res.files["docs/trampas.md"]
    assert b"# Mapa del repositorio\n" not in b.res.files["docs/mapa.md"]


def test_trampas_created_layout_and_pitfall_index(make_project: MakeProject) -> None:
    b = full(make_project)
    t = b.lines("docs/trampas.md")
    assert t[:2] == ["# Trampas", "estado: vigente"]
    det = idx(t, "## Detectadas en el historial")
    assert t[det + 1] == '<a id="r20261104-1530-u02-b3"></a>'
    assert t[det + 2] == "- Gotcha: el reloj es local. Luego cambia."
    lines = b.lines("AGENTS.md")
    pit = idx(lines, "## Trampas (índice)")
    body = lines[pit + 2 : idx(lines, "<!-- declutter:gen-end pitfall-index -->", pit)]
    assert body == [
        "- El cache tarda. → docs/trampas.md#r20261104-1530-u04 (ítem 1)",
        "- Segundo ítem → docs/trampas.md#r20261104-1530-u04 (ítem 2)",
        "- Gotcha: el reloj es local. → docs/trampas.md#r20261104-1530-u02-b3 "
        "(detectada en el historial)",
    ]
    m = b.lines("docs/mapa.md")
    assert m[:2] == ["# Mapa del repositorio", "estado: vigente"]
    assert '<a id="r20261104-1530-u05"></a>' in m


def test_extend_rejects_file_ending_in_open_fence(make_project: MakeProject) -> None:
    with pytest.raises(ReorgError) as ei:
        run(
            make_project,
            {"CLAUDE.md": FULL},
            disk={"docs/trampas.md": b"# T\n```\ncode\n"},
            ov=FULL_OV,
        )
    assert ei.value.code == 2
    issue = ei.value.issues[0]
    assert issue.key == "extend_open_structure"
    assert issue.dest == "docs/trampas.md"
    assert issue.at == (2, 2)


# --------------------------------------------------------------------------- zones


ZONE_SRC = (
    "# P\n"
    "\n"
    "## Detalles del código\n"
    "Ver src/a.py, src/b.py, src/c.py, src/d.py y src/e.py.\n"
    "\n"
    "## Cambio 2026-09-01\n"
    "- Siempre revisa src/a.py, src/b.py y src/c.py.\n"
).encode()


def test_zone_files_created_with_claude_import(make_project: MakeProject) -> None:
    b = run(make_project, {"CLAUDE.md": ZONE_SRC}, dirs=["src"])
    assert b.dec("Detalles del código").dest == "L2:src"
    assert b.dec("Cambio 2026-09-01").pointer == "zone:src"
    assert b.res.modes["src/AGENTS.md"] == "created"
    lines = b.lines("src/AGENTS.md")
    assert lines[0] == f"<!-- declutter:gen zone {RID} -->"
    assert lines[1] == "# Zona `src/`"
    dec = idx(lines, "## Decisiones de esta zona")
    assert lines[dec + 1] == (
        "- D-001 Cambio 2026-09-01 → ../docs/decisiones/D-001-cambio-2026-09-01.md "
        "(pendiente de destilar)"
    )
    assert idx(lines, "## Detalles del código", dec) > dec
    assert b.res.files["src/CLAUDE.md"] == b"@AGENTS.md\n"
    assert b.res.modes["src/CLAUDE.md"] == "created"
    check_partition(b, "src/AGENTS.md")
    check_partition(b, "src/CLAUDE.md")
    agents = b.lines("AGENTS.md")
    zm = idx(agents, "## Mapa y zonas")
    assert any("src/AGENTS.md" in x for x in agents[zm:])


def test_zone_existing_claude_md_is_not_touched(make_project: MakeProject) -> None:
    b = run(
        make_project,
        {"CLAUDE.md": ZONE_SRC},
        disk={"src/CLAUDE.md": b"notas\n", "src/AGENTS.md": b"# Zona previa\n"},
    )
    assert "src/CLAUDE.md" not in b.res.files
    assert b.res.modes["src/AGENTS.md"] == "extended"
    assert b"<!-- declutter:gen zone" not in b.res.files["src/AGENTS.md"]
    check_partition(b, "src/AGENTS.md")


# --------------------------------------------------------------------------- invariants


def _all_files_ok(b: Built) -> None:
    for path, data in b.res.files.items():
        mode = b.res.modes[path]
        if mode == "control":
            continue
        assert data.endswith(b"\n"), path
        if mode != "extended":
            assert not data.startswith(BOM), path
            mdblocks.parse(data, path)  # strict: no open structure
        check_partition(b, path)


def test_every_output_ends_with_newline_and_no_bom(make_project: MakeProject) -> None:
    src = BOM + FULL.replace(b"\n", b"\r\n")[:-2]  # CRLF, BOM, no final newline
    b = run(make_project, {"CLAUDE.md": src}, ov=FULL_OV)
    assert b.inp.docs["CLAUDE.md"].bom
    _all_files_ok(b)
    assert b.res.prev_sha["CLAUDE.md"] == b.inp.docs["CLAUDE.md"].sha256


def test_placements_partition_every_created_file(make_project: MakeProject) -> None:
    b = full(make_project)
    expected = {
        "AGENTS.md",
        "CLAUDE.md",
        "ESTADO.md",
        "docs/historial/2026-10.md",
        "docs/historial/INDICE.md",
        "docs/decisiones/INDICE.md",
        "docs/decisiones/D-001-cambio-2026-10-01-rama-x.md",
        "docs/decisiones/D-002-reglas-del-cache-2026-10-02.md",
        "docs/trampas.md",
        "docs/mapa.md",
    }
    assert set(b.res.files) == expected
    assert set(b.res.modes) == expected
    assert set(b.res.prev_sha) == expected
    _all_files_ok(b)
    # every source line has exactly one primary
    prim = sorted(
        (p.src for p in b.res.placements if p.role == "primary" and p.source == "CLAUDE.md"),
        key=lambda r: r or (0, 0),
    )
    nxt = 1
    for r in prim:
        assert r is not None and r[0] == nxt
        nxt = r[1] + 1
    assert nxt == len(b.inp.docs["CLAUDE.md"].lines) + 1


def test_generated_spans_are_balanced(make_project: MakeProject) -> None:
    b = full(make_project)
    for p in b.res.placements:
        if p.role in ("generated", "copy", "marker"):
            lines = mdblocks.split_lines(b.res.files[p.dest]).lines[p.at[0] - 1 : p.at[1]]
            assert mdblocks.is_balanced(lines, front_matter=False), p


def test_reorg_templates_have_no_safety_terms_outside_agents(make_project: MakeProject) -> None:
    subs = {
        k: "x"
        for k in (
            "id",
            "date",
            "unit",
            "title",
            "anchor",
            "range",
            "sha256",
            "card_id",
            "zone",
            "branch",
            "literal",
            "literal_link",
            "state",
            "decisions_index",
            "decisions_dir",
            "history_dir",
        )
    }
    for lang in ("es", "en"):
        for name in (
            "claude.md",
            "historial_header.md",
            "card.md",
            "trampas_header.md",
            "mapa_header.md",
            "zone_header.md",
        ):
            for line in build.load_template(lang, name, subs):
                assert LEX.safety(line.decode()) == frozenset(), (lang, name)
        titles = build.section_titles(lang)
        for key, text in titles.items():
            if key in build.AGENTS_ONLY_TITLES:
                continue
            assert LEX.safety(text) == frozenset(), (lang, key)
    # every generated line written outside AGENTS.md by a full build
    b = full(make_project)
    phase1 = ("template", "state")  # phase-1 templates rendered by scaffold, not by reorg
    for p in b.res.placements:
        if p.role in ("generated", "marker") and p.dest != "AGENTS.md" and p.kind not in phase1:
            for line in mdblocks.split_lines(b.res.files[p.dest]).lines[p.at[0] - 1 : p.at[1]]:
                assert LEX.safety(line.decode()) == frozenset(), (p.dest, p.kind)


def test_second_run_does_not_duplicate_agents_header_or_where(make_project: MakeProject) -> None:
    b1 = full(make_project)
    root = b1.project.root
    for rel, data in b1.res.files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    srcs = {"CLAUDE.md": b1.res.files["CLAUDE.md"], "AGENTS.md": b1.res.files["AGENTS.md"]}
    b2 = run_on(b1.project, srcs, rid=RID2)
    agents = b2.res.files["AGENTS.md"].decode()
    assert agents.count("<!-- declutter:gen agents-header ") == 1
    assert agents.count("<!-- declutter:gen where ") == 1
    claude = b2.lines("CLAUDE.md")
    assert claude.count("@AGENTS.md") == 1
    assert b2.res.modes["AGENTS.md"] == "rewritten"
    pre = b2.unit("U00")
    assert "generated" in b2.dec("U00").reasons and pre.source == "CLAUDE.md"
    _all_files_ok(b2)


def test_agents_md_previous_source_rewritten_in_same_order(make_project: MakeProject) -> None:
    claude = b"# C\nIntro C\n\n## Comandos\nmake\n"
    agents = b"# A\nIntro A\n\n## Reglas\n- r\n\n## Cambio 2026-09-01\ntexto\n"
    b = run(make_project, {"CLAUDE.md": claude, "AGENTS.md": agents})
    assert b.res.modes["AGENTS.md"] == "rewritten"
    lines = b.lines("AGENTS.md")
    order = [
        idx(lines, "<!-- declutter:gen agents-header"),
        idx(lines, "# C"),
        idx(lines, "# A"),
        idx(lines, "## Comandos"),
        idx(lines, "## Reglas"),
    ]
    assert order == sorted(order)
    prim = [p.unit for p in b.places("AGENTS.md") if p.role == "primary"]
    assert prim == ["U00", "U00b", "U01", "U02"]
    assert b.dec("Cambio 2026-09-01").dest == "L4"
    assert "docs/historial/2026-09.md" in b.res.files
    assert b.res.prev_sha["AGENTS.md"] == hashlib.sha256(agents).hexdigest()
    _all_files_ok(b)


# --------------------------------------------------------------------------- controls


def _controls(
    b: Built,
    *,
    prior_ratchet: ratchet.RatchetState | None = None,
    prior_log: bytes | None = None,
    prior_gitignore: bytes | None = None,
) -> tuple[BuildResult, tuple[str, ...]]:
    cfg = Config()
    proj = projection.project_after(b.project, cfg, b.res.files, ())
    return build.add_controls(
        b.res, b.inp, proj, cfg, prior_ratchet, (), prior_log, prior_gitignore
    )


def test_add_controls_gitignore_ratchet_pins_log(make_project: MakeProject) -> None:
    src = FULL + b"\n## Datos de pacientes 2026-10-05\n- clave privada fuera\n"
    b = run(make_project, {"CLAUDE.md": src}, ov=FULL_OV)
    res, warns = _controls(b)
    assert warns == ()
    for rel in (".declutter/.gitignore", ratchet.RATCHET_REL, ratchet.PINS_REL, ratchet.LOG_REL):
        assert res.modes[rel] == "control"
        ps = [p for p in res.placements if p.dest == rel]
        assert len(ps) == 1 and ps[0].role == "control"
        assert ps[0].sha256 == hashlib.sha256(res.files[rel]).hexdigest()
        assert res.prev_sha[rel] == "absent"
    assert res.files[".declutter/.gitignore"] == ratchet.GITIGNORE_FINAL
    state = ratchet.ratchet_loads(res.files[ratchet.RATCHET_REL])
    assert state.reorg == RID
    agents_text = res.files["AGENTS.md"].decode()
    assert state.s1_max == len(agents_text.splitlines()) + len(
        res.files["CLAUDE.md"].decode().splitlines()
    )
    g2 = next(u for u in b.inp.units if u.heading == "Datos de pacientes 2026-10-05")
    assert ratchet.title_key(g2.heading) in state.allowed_dated_headings
    pins = ratchet.pins_loads(res.files[ratchet.PINS_REL])
    assert pins == res.pins
    roles = {(p.role, p.unit) for p in pins}
    assert ("copy", "U02") in roles
    assert ("unit", "U07") in roles
    assert ("unit", g2.id) in roles
    assert ("l1_block", g2.id) in roles
    agents_lines = mdblocks.split_lines(res.files["AGENTS.md"]).lines
    for p in pins:
        assert ratchet.find_window(agents_lines, p.sha256, p.lines) is not None
    events = ratchet.log_events(res.files[ratchet.LOG_REL])
    assert [e["event"] for e in events] == ["baseline"]
    assert events[0]["reorg"] == RID
    # the content files are unchanged
    for rel, data in b.res.files.items():
        assert res.files[rel] == data


def test_add_controls_extends_prior_files_and_warns(make_project: MakeProject) -> None:
    b = full(make_project)
    prior = ratchet.RatchetState(s1_max=1, s3_max_pct=1, reorg="20261001-1000")
    old_log = ratchet.log_line(
        "baseline", date="2026-10-01", reorg="20261001-1000", old=None, new={}, reason="r"
    )
    res, warns = _controls(b, prior_ratchet=prior, prior_log=old_log, prior_gitignore=b"*\n")
    assert warns == ("ratchet_would_fail",)
    assert res.files[".declutter/.gitignore"] == ratchet.gitignore_final(b"*\n")
    assert res.files[ratchet.LOG_REL].startswith(old_log)
    assert len(ratchet.log_events(res.files[ratchet.LOG_REL])) == 2
    state = ratchet.ratchet_loads(res.files[ratchet.RATCHET_REL])
    assert state.s1_max == 1


def test_add_controls_rejects_gitignore_without_star(make_project: MakeProject) -> None:
    b = full(make_project)
    with pytest.raises(ReorgError) as ei:
        _controls(b, prior_gitignore=b"otro\n")
    assert ei.value.code == 1


def test_build_is_deterministic(make_project: MakeProject) -> None:
    b1 = full(make_project)
    b2 = run_on(b1.project, {"CLAUDE.md": FULL}, ov=FULL_OV)
    assert b1.res == b2.res
    assert json.dumps(sorted(b1.res.files)) == json.dumps(sorted(b2.res.files))
