"""proof.verify: lossless proof C0-C9, exact and posterior modes (spec §7, §8, §11.4, §12.2).

Every plan is built in memory from synthetic sources; ``read`` is a map path -> bytes.
Mutations carry the unique marker ``ZQX-SEED-7731`` in the mutated line, and no failure
text may contain it (G6).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace

import pytest

from _proof_helpers import LEX, RID, Plan, make_plan, with_places
from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.model import Decision, Placement, ProofResult
from declutter_context.reorg import EXIT_OK, EXIT_PROOF, mdblocks, proof
from declutter_context.reorg.classify import BlockOverride, Overrides
from declutter_context.reorg.manifest import Output

SEED = "ZQX-SEED-7731"
BOM = b"\xef\xbb\xbf"
HIST = "docs/historial/2026-10.md"

CLAUDE = (
    BOM
    + (
        "---\n"
        "title: demo\n"
        "---\n"
        "# Proyecto demo\n"
        "Intro del proyecto.\n"
        "\n"
        "| col | val |\n"
        "|---|---|\n"
        "| a | 1 |\n"
        "\n"
        "## Cómo correr\n"
        f"Ejecuta `make test` {SEED}.\n"
        "\n"
        "```sh\n"
        "make test\n"
        "## no es titulo\n"
        "```\n"
        "\n"
        "## Cambio 2026-10-01 rama `x`\n"
        "Se agregó algo.\n"
        "\n"
        f"- Nunca uses datos de pacientes reales {SEED}.\n"
        "- Gotcha: el reloj es local. Luego cambia.\n"
        "\n"
        "## Nota 2026-10-03\n"
        f"Solo registro {SEED}.  \n"
        "Otra linea.\n"
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
        f"Todo dato es confidencial {SEED}.\n"
        "\n"
        "## Notas de la carpeta\n"
        "Ver src/a, src/b, src/c, src/d y src/e.\n"
        f"Sin cambios {SEED}"
    ).encode()
)
AGENTS = (
    b"# Agentes\r\nReglas base.\r\n\r\n"
    b"## Convenciones\r\nUsa ruff. a\rb\r\n\r\n"
    b"## Registro 2026-09-01\r\nCambio viejo.\r\n"
)
SOURCES = {"CLAUDE.md": CLAUDE, "AGENTS.md": AGENTS}


def plan_full(make_project: MakeProject, **kw: object) -> Plan:
    return make_plan(make_project({}), SOURCES, dirs=["src"], **kw)  # type: ignore[arg-type]


def run(p: Plan, checks: frozenset[str] = proof.CHECKS_STAGE, **kw: object) -> ProofResult:
    return proof.verify(
        p.manifest,
        p.res.placements,
        p.read,
        p.original,
        checks=checks,
        lexicon=LEX,
        **kw,  # type: ignore[arg-type]
    )


def disk_kw(p: Plan) -> dict[str, object]:
    return {"written": frozenset(p.files), "new_status": frozenset()}


def failed(r: ProofResult) -> set[str]:
    return {f.check for f in r.failures}


def assert_clean_output(r: ProofResult) -> None:
    text = repr(r) + "".join(proof.describe(f) for f in r.failures)
    assert SEED not in text


def line_no(p: Plan, path: str, needle: str) -> int:
    for i, ln in enumerate(p.lines(path), 1):
        if needle.encode() in ln:
            return i
    raise AssertionError(f"not found: {needle!r}")


def place_at(p: Plan, path: str, line: int) -> Placement:
    return next(x for x in p.places(path) if x.at[0] <= line <= x.at[1])


def resha(p: Plan, target: Placement) -> Plan:
    """Recompute the sha of ``target`` from the (mutated) file, keeping the rest."""
    span = p.lines(target.dest)[target.at[0] - 1 : target.at[1]]
    new = replace(target, sha256=mdblocks.lines_sha(span))
    return with_places(p, [new if x == target else x for x in p.res.placements])


def resha_control(p: Plan, path: str) -> Plan:
    sha = hashlib.sha256(p.files[path]).hexdigest()
    return with_places(
        p,
        [
            replace(x, sha256=sha) if x.dest == path and x.role == "control" else x
            for x in p.res.placements
        ],
    )


def force(p_dest: str, file: str) -> Callable[[Decision], Decision]:
    def f(d: Decision) -> Decision:
        return replace(d, dest=p_dest, file=file, card=False, pointer=None)

    return f


# --------------------------------------------------------------------------- passing plans


def test_verify_passes_on_built_result(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    assert {o.mode for o in p.manifest.outputs} >= {"created", "rewritten", "control"}
    r = run(p, proof.CHECKS_DISK, **disk_kw(p))
    assert r.ok, r.failures
    assert r.mode == "exact" and r.failures == () and r.states == ()
    assert proof.exit_code(r) == EXIT_OK


def test_verify_passes_with_extended_files(make_project: MakeProject) -> None:
    disk = {"docs/trampas.md": b"# Mis trampas\n- vieja", "docs/mapa.md": b"# Mapa previo\n"}
    p = make_plan(make_project({}), SOURCES, disk, dirs=["src"])
    assert p.res.modes["docs/trampas.md"] == "extended"
    r = run(p, proof.CHECKS_DISK, backup=disk.get, **disk_kw(p))
    assert r.ok, r.failures


def test_verify_passes_crlf_only_and_single_source(make_project: MakeProject) -> None:
    src = (
        b"# T\r\n\r\n## Reglas\r\n- nunca borrar datos\r\n\r\n"
        b"## Cambio 2026-01-02\r\n- No uses la clave privada del servidor.\r\n"
    )
    p = make_plan(make_project({}), {"CLAUDE.md": src})
    r = run(p, proof.CHECKS_DISK, **disk_kw(p))
    assert r.ok, r.failures


def test_last_line_without_newline_before_end_marker_passes_c2_c3_c5(
    make_project: MakeProject,
) -> None:
    src = b"# T\n\n## Nota 2026-10-04\nultima linea sin salto"
    p = make_plan(make_project({}), {"CLAUDE.md": src})
    assert p.files[HIST].endswith(b"ultima linea sin salto\n<!-- declutter:end U01 -->\n")
    r = run(p, frozenset({"C2", "C3", "C5"}))
    assert r.ok, r.failures
    assert run(p, proof.CHECKS_DISK, **disk_kw(p)).ok


def test_empty_source_and_no_h2_source(make_project: MakeProject) -> None:
    p = make_plan(make_project({}), {"CLAUDE.md": b""})
    assert run(p, proof.CHECKS_DISK, **disk_kw(p)).ok
    p2 = make_plan(make_project({}), {"CLAUDE.md": BOM})
    assert run(p2, proof.CHECKS_DISK, **disk_kw(p2)).ok
    p3 = make_plan(make_project({}), {"CLAUDE.md": b"Solo texto\nsin titulos"})
    assert [u.dest for u in p3.manifest.units] == ["L1"]
    assert run(p3, proof.CHECKS_DISK, **disk_kw(p3)).ok


# --------------------------------------------------------------------------- mutations

Mut = Callable[[Plan], tuple[Plan, dict[str, object]]]


def _edit_line(path: str, needle: str, fn: Callable[[bytes], bytes]) -> Mut:
    def m(p: Plan) -> tuple[Plan, dict[str, object]]:
        lines = p.lines(path)
        i = line_no(p, path, needle) - 1
        lines[i] = fn(lines[i])
        p.set_lines(path, lines)
        return p, {}

    return m


def _delete_line(path: str, needle: str) -> Mut:
    def m(p: Plan) -> tuple[Plan, dict[str, object]]:
        lines = p.lines(path)
        del lines[line_no(p, path, needle) - 1]
        p.set_lines(path, lines)
        return p, {}

    return m


def _lf_to_crlf(p: Plan) -> tuple[Plan, dict[str, object]]:
    p.files[HIST] = p.files[HIST].replace(b"\n", b"\r\n")
    return p, {}


def _drop_final_newline(p: Plan) -> tuple[Plan, dict[str, object]]:
    path = "src/AGENTS.md"
    assert p.places(path)[-1].role == "primary"
    assert SEED.encode() in p.lines(path)[-1]
    p.files[path] = p.files[path][:-1]
    return p, {}


def _prepend_bom(p: Plan) -> tuple[Plan, dict[str, object]]:
    p.files["docs/mapa.md"] = BOM + p.files["docs/mapa.md"]
    return p, {}


def _reorder(p: Plan) -> tuple[Plan, dict[str, object]]:
    lines = p.lines(HIST)
    i = line_no(p, HIST, SEED) - 1
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    p.set_lines(HIST, lines)
    return p, {}


def _src_shift(d0: int) -> Mut:
    def m(p: Plan) -> tuple[Plan, dict[str, object]]:
        target = place_at(p, HIST, line_no(p, HIST, SEED))
        assert target.src is not None
        new = replace(target, src=(target.src[0] + d0, target.src[1]))
        return with_places(p, [new if x == target else x for x in p.res.placements]), {}

    return m


def _manifest_sha(p: Plan) -> tuple[Plan, dict[str, object]]:
    s0 = replace(p.manifest.sources[0], sha256="0" * 64)
    return replace(p, manifest=replace(p.manifest, sources=(s0, *p.manifest.sources[1:]))), {}


def _placement_sha(p: Plan) -> tuple[Plan, dict[str, object]]:
    target = place_at(p, HIST, line_no(p, HIST, SEED))
    new = replace(target, sha256="0" * 64)
    return with_places(p, [new if x == target else x for x in p.res.placements]), {}


def _inject(p: Plan) -> tuple[Plan, dict[str, object]]:
    p.set_lines("AGENTS.md", [*p.lines("AGENTS.md"), f"inyectada {SEED}".encode()])
    return p, {}


def _gen_opens_fence(p: Plan) -> tuple[Plan, dict[str, object]]:
    path = "docs/mapa.md"
    lines = p.lines(path)
    blank = next(x for x in p.places(path) if x.kind == "blank")
    lines[blank.at[0] - 1] = f"```{SEED}".replace("`", "~").encode()
    p.set_lines(path, lines)
    return resha(p, blank), {}


def _gen_opens_comment(p: Plan) -> tuple[Plan, dict[str, object]]:
    path = "docs/mapa.md"
    lines = p.lines(path)
    blank = next(x for x in p.places(path) if x.kind == "blank")
    lines[blank.at[0] - 1] = f"<!-- {SEED}".encode()
    p.set_lines(path, lines)
    return resha(p, blank), {}


def _rewritten_non_source(p: Plan) -> tuple[Plan, dict[str, object]]:
    outs = tuple(
        Output(o.path, "rewritten") if o.path == "docs/mapa.md" else o for o in p.manifest.outputs
    )
    return replace(p, manifest=replace(p.manifest, outputs=outs)), {}


def _outside_outputs(p: Plan) -> tuple[Plan, dict[str, object]]:
    return p, {"written": frozenset(p.files) | {"otro.md"}}


def _pin_deleted(p: Plan) -> tuple[Plan, dict[str, object]]:
    pins = ratchet.pins_loads(p.files[ratchet.PINS_REL])
    p.files[ratchet.PINS_REL] = ratchet.pins_dumps([x for x in pins if x.role != "copy"])
    return resha_control(p, ratchet.PINS_REL), {}


def _g2_altered(p: Plan) -> tuple[Plan, dict[str, object]]:
    return _edit_line("AGENTS.md", "Todo dato", lambda b: b.replace(b"Todo", b"Cada"))(p)


MUTATIONS: dict[str, tuple[Mut, str]] = {
    "delete_line": (_delete_line(HIST, SEED), "C2"),
    "delete_line_c3": (_delete_line(HIST, SEED), "C3"),
    "change_byte": (_edit_line(HIST, "Solo registro", lambda b: b.replace(b"Solo", b"Sole")), "C2"),
    "trim_trailing_space": (_edit_line(HIST, "Solo registro", lambda b: b.rstrip(b" ")), "C2"),
    "lf_to_crlf": (_lf_to_crlf, "C2"),
    "lf_to_crlf_c3": (_lf_to_crlf, "C3"),
    "drop_final_newline": (_drop_final_newline, "C5"),
    "prepend_bom": (_prepend_bom, "C5"),
    "reorder_lines": (_reorder, "C2"),
    "src_gap": (_src_shift(1), "C1"),
    "src_overlap": (_src_shift(-1), "C1"),
    "manifest_sha": (_manifest_sha, "C3"),
    "placement_sha": (_placement_sha, "C2"),
    "copy_altered": (
        _edit_line("AGENTS.md", SEED, lambda b: b.replace(b"Nunca", b"Nunka")),
        "C2",
    ),
    "injected_line": (_inject, "C5"),
    "generated_opens_fence": (_gen_opens_fence, "C5"),
    "generated_opens_comment": (_gen_opens_comment, "C5"),
    "rewritten_non_source": (_rewritten_non_source, "C5"),
    "rewritten_non_source_c8": (_rewritten_non_source, "C8a"),
    "outside_outputs": (_outside_outputs, "C8a"),
    "pin_deleted": (_pin_deleted, "C9"),
    "g2_altered": (_g2_altered, "C9"),
}


FILE_LEVEL = frozenset(
    {"outside_outputs", "rewritten_non_source", "rewritten_non_source_c8"}
)  # a file, not a range


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutations_fail_with_code_4(make_project: MakeProject, name: str) -> None:
    mut, check = MUTATIONS[name]
    p = plan_full(make_project)
    assert run(p, proof.CHECKS_DISK, **disk_kw(p)).ok
    p, extra = mut(p)
    kw = {**disk_kw(p), **extra}
    r = run(p, proof.CHECKS_DISK, **kw)
    assert not r.ok
    assert proof.exit_code(r) == EXIT_PROOF
    assert check in failed(r), r.failures
    assert_clean_output(r)
    hits = [f for f in r.failures if f.check == check]
    assert any(f.unit or f.dest or f.source for f in hits), hits
    if name not in FILE_LEVEL:
        assert any(f.src or f.at for f in hits), hits
    for f in hits:
        assert proof.describe(f).startswith(check + " ")


def test_generated_balance_reason(make_project: MakeProject) -> None:
    p, _ = _gen_opens_fence(plan_full(make_project))
    r = run(p)
    assert {f.reason for f in r.failures if f.check == "C5"} & {
        "generated span not balanced",
        "destination has an open structure",
    }


def test_describe_format(make_project: MakeProject) -> None:
    p, _ = _edit_line(HIST, SEED, lambda b: b + b"x")(plan_full(make_project))
    r = run(p, frozenset({"C2"}))
    f = next(x for x in r.failures if x.check == "C2")
    text = proof.describe(f)
    assert text.startswith(f"C2 {f.unit} CLAUDE.md:")
    assert f"→ {HIST}:" in text
    assert "B vs" in text


# --------------------------------------------------------------------------- guards


def test_c6_safety_only_in_l1c_fails(make_project: MakeProject) -> None:
    src = b"# T\n\n## Hooks de Claude Code\nConfigura hooks.\n- Nunca subas la clave privada.\n"
    p = make_plan(
        make_project({}),
        {"CLAUDE.md": src},
        force={"Hooks de Claude Code": force("L1c", "CLAUDE.md")},
        drop_invariants=True,
    )
    r = run(p)
    assert "C6" in failed(r)
    f = next(x for x in r.failures if x.check == "C6")
    assert f.source == "CLAUDE.md" and f.src == (5, 5) and f.dest == "CLAUDE.md"


def test_c6_passes_with_guard_ack(make_project: MakeProject) -> None:
    src = b"# T\n\n## Cambio 2026-01-02\nTexto.\n\n- Nunca subas la clave privada.\n"
    p0 = make_plan(make_project({}), {"CLAUDE.md": src})
    blk = next(b for u in p0.manifest.units for b in u.blocks if b.guard)
    ov = Overrides(blocks=(BlockOverride(block_key=blk.key, guard_ack="motivo largo ok"),))
    p = make_plan(make_project({}), {"CLAUDE.md": src}, ov=ov)
    assert not [x for x in p.res.placements if x.role == "copy" and x.dest == "AGENTS.md"]
    assert run(p).ok


def test_c6_h2_with_category_inside_comment_fails(make_project: MakeProject) -> None:
    src = b"# T\n\n## Nota 2026-10-05\nTexto.\n<!--\n## pacientes ocultos\n-->\n"
    p = make_plan(make_project({}), {"CLAUDE.md": src})
    assert p.manifest.units[1].dest == "L4"
    r = run(p)
    hits = [f for f in r.failures if f.check == "C6"]
    assert hits and all(f.src == (6, 6) for f in hits)
    assert any("##" in f.reason for f in hits)


def test_c6_h2_in_fence_is_ignored(make_project: MakeProject) -> None:
    src = b"# T\n\n## Nota 2026-10-05\nTexto.\n```\n## pacientes en codigo\n```\n"
    p = make_plan(make_project({}), {"CLAUDE.md": src})
    assert run(p).ok


def test_g3_publishable_in_l2_fails_even_with_guard_ack(make_project: MakeProject) -> None:
    src = b"# T\n\n## Notas de src\nVer src/a y src/b.\n\nEl benchmark de src/c.\n"
    p0 = make_plan(make_project({}), {"CLAUDE.md": src}, dirs=["src"])
    blk = next(b for u in p0.manifest.units for b in u.blocks if "publishable" in b.guard)
    ov = Overrides(blocks=(BlockOverride(block_key=blk.key, guard_ack="motivo largo ok"),))
    p = make_plan(
        make_project({}),
        {"CLAUDE.md": src},
        ov=ov,
        dirs=["src"],
        force={"Notas de src": force("L2:src", "src/AGENTS.md")},
    )
    r = run(p)
    assert "G3" in failed(r)
    assert "C6" not in failed(r)


def test_g3_publishable_in_l1c_fails(make_project: MakeProject) -> None:
    src = b"# T\n\n## Hooks de Claude Code\nConfigura hooks.\n\nUn ventilador en hooks.\n"
    p0 = make_plan(make_project({}), {"CLAUDE.md": src})
    blk = next(b for u in p0.manifest.units for b in u.blocks if "device" in b.guard)
    ov = Overrides(blocks=(BlockOverride(block_key=blk.key, guard_ack="motivo largo ok"),))
    p = make_plan(
        make_project({}),
        {"CLAUDE.md": src},
        ov=ov,
        force={"Hooks de Claude Code": force("L1c", "CLAUDE.md")},
    )
    assert "G3" in failed(run(p))


def test_g2_unit_outside_l1_fails(make_project: MakeProject) -> None:
    src = b"# T\n\n## Datos de pacientes\nTexto.\n"
    p = make_plan(
        make_project({}),
        {"CLAUDE.md": src},
        force={"Datos de pacientes": force("L4", "docs/historial/sin-fecha.md")},
    )
    r = run(p)
    assert "G2" in failed(r)
    f = next(x for x in r.failures if x.check == "G2")
    assert f.unit == "U01" and f.src == (3, 3)


def test_c7_order_violation(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    a = next(x for x in p.places("AGENTS.md") if x.role == "primary" and x.unit == "U07")
    b = next(x for x in p.places("AGENTS.md") if x.role == "primary" and x.unit == "U09")
    lines = p.lines("AGENTS.md")
    assert a.at[1] + 1 == b.at[0]
    span_a = lines[a.at[0] - 1 : a.at[1]]
    span_b = lines[b.at[0] - 1 : b.at[1]]
    lines[a.at[0] - 1 : b.at[1]] = span_b + span_a
    p.set_lines("AGENTS.md", lines)
    na = replace(a, at=(a.at[0] + len(span_b), b.at[1]))
    nb = replace(b, at=(a.at[0], a.at[0] + len(span_b) - 1))
    p = with_places(p, [na if x == a else nb if x == b else x for x in p.res.placements])
    p = resha_control(p, ratchet.PINS_REL)
    r = run(p)
    assert failed(r) == {"C7"}, r.failures


def test_c5_extended_backup_prefix_mismatch(make_project: MakeProject) -> None:
    disk = {"docs/trampas.md": b"# Mis trampas\n- vieja\n"}
    p = make_plan(make_project({}), SOURCES, disk, dirs=["src"])
    assert run(p, backup=disk.get).ok
    r = run(p, backup={"docs/trampas.md": b"# Mis trampas\n- otra\n"}.get)
    assert failed(r) == {"C5"}
    assert all(f.dest == "docs/trampas.md" for f in r.failures)
    r2 = run(p, backup={}.get)
    assert failed(r2) == {"C5"}


def test_c5_trampas_copy_without_index_line(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    n = line_no(p, "AGENTS.md", "-u02-b3 (")
    lines = p.lines("AGENTS.md")
    lines[n - 1] = lines[n - 1].replace(b"-u02-b3", b"-u02-b9")
    p.set_lines("AGENTS.md", lines)
    p = resha(p, place_at(p, "AGENTS.md", n))
    r = run(p)
    assert failed(r) == {"C5"}, r.failures
    f = r.failures[0]
    assert f.unit == "U02" and f.dest == "docs/trampas.md"


def test_c8b_diff_names_mismatch(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    names = frozenset(o.path for o in p.manifest.outputs)
    assert run(p, proof.CHECKS_REV, diff_names=names).ok
    r = run(p, proof.CHECKS_REV, diff_names=names - {"ESTADO.md"} | {"otro.md"})
    assert failed(r) == {"C8b"}
    assert {f.dest for f in r.failures} == {"ESTADO.md", "otro.md"}
    assert "C8b" in failed(run(p, proof.CHECKS_REV))


def test_c8a_without_written_fails_and_new_status_outside(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    assert "C8a" in failed(run(p, proof.CHECKS_DISK))
    kw = {"written": frozenset(p.files), "new_status": frozenset({"ajeno.txt"})}
    r = run(p, proof.CHECKS_DISK, **kw)
    assert failed(r) == {"C8a"}


def test_c0_source_changed(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    p.raw["AGENTS.md"] = AGENTS + b"x"
    r = run(p, frozenset({"C0"}))
    assert failed(r) == {"C0"}
    assert r.failures[0].source == "AGENTS.md"


def test_checks_selects_subset(make_project: MakeProject) -> None:
    p, _ = _prepend_bom(plan_full(make_project))
    assert run(p, frozenset({"C1", "C2", "C3"})).ok
    assert not run(p, frozenset({"C5"})).ok


def test_issues_from_failures(make_project: MakeProject) -> None:
    p, _ = _prepend_bom(plan_full(make_project))
    issues = proof.to_issues(run(p))
    assert issues and all(i.code == EXIT_PROOF and i.key == "C5" for i in issues)


# --------------------------------------------------------------------------- posterior


def _unpin_event(pin_id: str) -> bytes:
    return ratchet.log_line(
        "unpin",
        date="2026-11-05",
        reorg=RID,
        old=None,
        new=None,
        reason="motivo de prueba",
        pin=pin_id,
    )


def _post_edit_l1(p: Plan) -> Plan:
    return _edit_line("AGENTS.md", "Ejecuta", lambda b: b.replace(b"Ejecuta", b"Corre"))(p)[0]


def _post_lines_above(p: Plan) -> Plan:
    p.set_lines(HIST, [b"# nuevo arriba", b"", *p.lines(HIST)])
    return p


def _post_history_byte(p: Plan) -> Plan:
    return _edit_line(HIST, "Solo registro", lambda b: b.replace(b"Solo", b"Sole"))(p)[0]


def _post_marker_missing(p: Plan) -> Plan:
    return _delete_line(HIST, "<!-- declutter:end U03 -->")(p)[0]


def _post_pin_deleted(p: Plan) -> Plan:
    return _delete_line("AGENTS.md", "- Nunca uses datos")(p)[0]


def _post_g2_title_deleted(p: Plan) -> Plan:
    return _delete_line("AGENTS.md", "## Datos de pacientes")(p)[0]


def _post_unpinned(p: Plan) -> Plan:
    p = _post_pin_deleted(p)
    p.files[ratchet.LOG_REL] += _unpin_event("P-001")
    return p


def _post_g2_unpinned(p: Plan) -> Plan:
    p = _post_g2_title_deleted(p)
    pin = next(x for x in ratchet.pins_loads(p.files[ratchet.PINS_REL]) if x.role == "unit")
    p.files[ratchet.LOG_REL] += _unpin_event(pin.id)
    return p


def _post_generated(p: Plan) -> Plan:
    return _edit_line("AGENTS.md", "Antes de trabajar", lambda b: b + b" (editado)")(p)[0]


def _post_excerpt(p: Plan) -> Plan:
    path = "docs/trampas.md"
    return _edit_line(path, "Gotcha", lambda b: b.replace(b"reloj", b"relox"))(p)[0]


def _post_appended(p: Plan) -> Plan:
    p.set_lines("AGENTS.md", [*p.lines("AGENTS.md"), b"", b"## Nueva seccion", b"texto"])
    return p


POSTERIOR: dict[str, tuple[Callable[[Plan], Plan], str, str, bool, bool]] = {
    # name: (mutation, unit or path, state, ok, ok with strict)
    "edit_l1": (_post_edit_l1, "U01", "edited_after", True, False),
    "lines_above_history": (_post_lines_above, "U03", "literal_intact", True, True),
    "history_byte": (_post_history_byte, "U03", "history_altered", False, False),
    "marker_missing": (_post_marker_missing, "U03", "marker_missing", False, False),
    "pin_deleted": (_post_pin_deleted, "U02", "pin_missing", False, False),
    "g2_title_deleted": (_post_g2_title_deleted, "U07", "protected_altered", False, False),
    "unpinned": (_post_unpinned, "U02", "released", True, False),
    "g2_unpinned": (_post_g2_unpinned, "U07", "released", True, False),
    "generated": (_post_generated, "AGENTS.md", "generated_edited", True, False),
    "excerpt": (_post_excerpt, "U02", "excerpt_altered", False, False),
    # An appended line has no class (C5): informative, but 4 with --strict (§11.4, §15.1.7).
    "appended": (_post_appended, "AGENTS.md", "file_edited", True, False),
}


@pytest.mark.parametrize("name", sorted(POSTERIOR))
def test_posterior_states(make_project: MakeProject, name: str) -> None:
    mut, key, state, ok, ok_strict = POSTERIOR[name]
    p = mut(plan_full(make_project))
    r = run(p, proof.CHECKS_DISK, posterior=True, **disk_kw(p))
    assert r.mode == "posterior"
    assert (key, state) in r.states, r.states
    assert r.ok is ok, r.failures
    assert proof.exit_code(r) == (EXIT_OK if ok else EXIT_PROOF)
    rs = run(p, proof.CHECKS_DISK, posterior=True, strict=True, **disk_kw(p))
    assert rs.ok is ok_strict, rs.failures
    assert_clean_output(r)
    assert_clean_output(rs)
    for f in r.failures:
        assert state in f.reason or f.check == "C5"


def test_posterior_untouched_is_exact(make_project: MakeProject) -> None:
    p = plan_full(make_project)
    r = run(p, proof.CHECKS_DISK, posterior=True, **disk_kw(p))
    assert r.ok and r.mode == "exact" and r.states == ()


def test_posterior_positional_but_bom_still_exact_failure(make_project: MakeProject) -> None:
    p, _ = _prepend_bom(plan_full(make_project))
    r = run(p, proof.CHECKS_DISK, posterior=True, **disk_kw(p))
    assert r.mode == "exact" and failed(r) == {"C5"}


def test_posterior_history_intact_after_other_edits(make_project: MakeProject) -> None:
    p = _post_lines_above(_post_edit_l1(plan_full(make_project)))
    r = run(p, proof.CHECKS_DISK, posterior=True, **disk_kw(p))
    states = dict(r.states)
    assert states["U02"] in ("literal_intact", "excerpt_intact", "pin_intact")
    assert ("U03", "literal_intact") in r.states
    assert ("U10", "literal_intact") in r.states
    assert r.ok


def test_posterior_c0_still_checked(make_project: MakeProject) -> None:
    p = _post_edit_l1(plan_full(make_project))
    p.raw["CLAUDE.md"] = b"otro"
    r = run(p, proof.CHECKS_DISK, posterior=True, **disk_kw(p))
    assert r.mode == "posterior" and "C0" in failed(r) and not r.ok


def test_every_posterior_state_has_a_label_in_both_languages() -> None:
    """Regression (T19): ``verify`` printed raw keys such as ``reorg.intact``."""
    from declutter_context.i18n import MESSAGES

    for state in proof.POSTERIOR_STATES:
        for lang in ("es", "en"):
            assert f"reorg.{state}" in MESSAGES[lang], (lang, state)


def test_every_check_has_a_label_in_both_languages() -> None:
    """Regression (T19): ``C8a``/``C8b`` failures printed the raw check id as reason."""
    from declutter_context.i18n import MESSAGES

    checks = {f"C{i}" for i in range(10)} | {"C8a", "C8b", "G2", "G3"}
    for check in checks:
        for lang in ("es", "en"):
            assert f"reorg.{check}" in MESSAGES[lang], (lang, check)
