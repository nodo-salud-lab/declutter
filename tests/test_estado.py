from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from declutter_context.estado import STATE_FILENAMES, find_estado, load_estado, parse_estado
from declutter_context.model import EstadoItem, Project

ES = """# Estado — demo
Actualizado: 2026-11-04
Fase: Implementación del medidor

## Siguiente (IA)          <!-- alias: "Next (AI)" -->
- [ ] Escribir estado.py — spec 4
- [x] Escribir model.py — spec 3
- Tarea sin casilla

## Decide el humano        <!-- alias: "Human decides" -->
- [ ] Elegir umbral S7 — spec 5

## Bloqueado               <!-- alias: "Blocked" -->
- Publicación — falta cuenta

## Dónde retomar           <!-- alias: "Resume at" -->
- rama feat/estado
- docs/specs/x.md
"""

EN = """# State — demo
Updated: 2026-01-31
Phase: Build

## Next (AI)
- [ ] Write parser
- [X] Write model

## Human decides
- [ ] Pick license

## Blocked
- [ ] Release — waiting

## Resume at
- branch main
"""

MakeProject = Callable[..., Project]
P = Path("/x/ESTADO.md")


def test_full_es_template() -> None:
    e = parse_estado(ES, P)
    assert e.path == P
    assert e.updated == date(2026, 11, 4)
    assert e.phase == "Implementación del medidor"
    assert e.next_ai == (
        EstadoItem("Escribir estado.py — spec 4", False),
        EstadoItem("Escribir model.py — spec 3", True),
        EstadoItem("Tarea sin casilla", False),
    )
    assert e.human == (EstadoItem("Elegir umbral S7 — spec 5", False),)
    assert e.blocked == ("Publicación — falta cuenta",)
    assert e.resume == ("rama feat/estado", "docs/specs/x.md")
    assert e.errors == ()
    assert len(e.open_next_ai) == 2
    assert len(e.open_human) == 1


def test_full_en_template_aliases() -> None:
    e = parse_estado(EN, P)
    assert e.updated == date(2026, 1, 31)
    assert e.phase == "Build"
    assert e.next_ai == (EstadoItem("Write parser", False), EstadoItem("Write model", True))
    assert e.human == (EstadoItem("Pick license", False),)
    assert e.blocked == ("Release — waiting",)
    assert e.resume == ("branch main",)
    assert e.errors == ()


def test_accent_and_case_variants() -> None:
    text = (
        "ACTUALIZADO: 2026-02-02\nfase: x\n\n"
        "## SIGUIENTE (IA)\n- a\n\n"
        "## decide EL humano\n- b\n\n"
        "## BLOQUEADO\n- c\n\n"
        "## DONDE RETOMAR\n- d\n"
    )
    e = parse_estado(text, P)
    assert e.updated == date(2026, 2, 2)
    assert e.phase == "x"
    assert [i.text for i in e.next_ai] == ["a"]
    assert [i.text for i in e.human] == ["b"]
    assert e.blocked == ("c",)
    assert e.resume == ("d",)
    e2 = parse_estado("## Dónde  retomar\n- z\n## Siguiente (IÁ)\n- q\n", P)
    assert e2.resume == ("z",)
    assert [i.text for i in e2.next_ai] == ["q"]


def test_missing_sections() -> None:
    e = parse_estado("# T\nActualizado: 2026-11-04\n\n## Bloqueado\n- algo\n", P)
    assert e.updated == date(2026, 11, 4)
    assert e.phase is None
    assert e.next_ai == () and e.human == () and e.resume == ()
    assert e.blocked == ("algo",)
    assert e.errors == ()


def test_invalid_date() -> None:
    for bad in ("04/11/2026", "2026-13-40", "ayer", "", "20261104"):
        e = parse_estado(f"Actualizado: {bad}\n", P)
        assert e.updated is None
        assert len(e.errors) == 1


def test_no_date_is_not_an_error() -> None:
    e = parse_estado("# T\n\n## Siguiente (IA)\n- [ ] a\n", P)
    assert e.updated is None
    assert e.errors == ()


def test_done_items_and_checkbox_forms() -> None:
    e = parse_estado("## Next (AI)\n- [x] a\n- [X] b\n- [ ] c\n- d\n- [ ]\n-\n- [x]\n", P)
    assert [(i.text, i.done) for i in e.next_ai] == [
        ("a", True),
        ("b", True),
        ("c", False),
        ("d", False),
    ]
    assert e.open_next_ai == (EstadoItem("c"), EstadoItem("d"))


def test_comments_removed() -> None:
    text = (
        "Actualizado: 2026-03-03 <!-- x -->\n"
        "<!--\n## Bloqueado\n- oculto\n-->\n"
        "## Bloqueado <!-- note -->\n- visible <!-- c -->\n"
    )
    e = parse_estado(text, P)
    assert e.updated == date(2026, 3, 3)
    assert e.blocked == ("visible",)


def test_unknown_sections_ignored_and_header_scope() -> None:
    text = "Fase: uno\n## Notas\n- no cuenta\nFase: dos\n## Bloqueado\n- b\nUpdated: 2026-01-01\n"
    e = parse_estado(text, P)
    assert e.phase == "uno"
    assert e.updated is None
    assert e.blocked == ("b",)


def test_empty_text() -> None:
    e = parse_estado("", P)
    assert e.updated is None and e.phase is None
    assert e.next_ai == () and e.errors == ()


def test_crlf() -> None:
    e = parse_estado("Actualizado: 2026-11-04\r\n## Blocked\r\n- a\r\n", P)
    assert e.updated == date(2026, 11, 4)
    assert e.blocked == ("a",)


def test_find_and_load(make_project: MakeProject) -> None:
    assert STATE_FILENAMES == ("ESTADO.md", "STATE.md")
    proj = make_project({"STATE.md": EN})
    assert find_estado(proj.root) == proj.root / "STATE.md"
    e = load_estado(proj.root)
    assert e is not None and e.phase == "Build"
    proj2 = make_project({"ESTADO.md": ES})
    # ESTADO.md has priority over STATE.md
    (proj2.root / "STATE.md").write_text(EN, encoding="utf-8")
    e2 = load_estado(proj2.root)
    assert e2 is not None and e2.path.name == "ESTADO.md"


def test_load_absent_and_empty(make_project: MakeProject) -> None:
    proj = make_project({})
    assert find_estado(proj.root) is None
    assert load_estado(proj.root) is None
    proj2 = make_project({"ESTADO.md": ""})
    e = load_estado(proj2.root)
    assert e is not None and e.errors == () and e.next_ai == ()
