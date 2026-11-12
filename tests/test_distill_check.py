"""distill.check: ``declutter reorg distill-check`` conditions over an applied run (fase 2b §4-§5,
§15.1 items 3-11 and 22).

Every project is synthetic (``_distill_helpers``). The seeded identifier ``ZQX7731`` lives
in the literal of D-001: no report, reason or text line may contain it (G6).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from _distill_helpers import (
    INV_ES,
    RID,
    SEED,
    TEXT_ES,
    Fixture,
    applied,
    fill,
    fill_all,
    git,
    invariant_title,
    replace_in,
)
from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.reorg import ReorgError, distill
from declutter_context.reorg.lexicon import build_lexicon

LEX = build_lexicon({})


def run(fx: Fixture, card: str | None = None) -> distill.CheckReport:
    return distill.check(fx.root, RID, card=card, lexicon=LEX)


def card_of(rep: distill.CheckReport, card_id: str) -> distill.CardCheck:
    return next(c for c in rep.cards if c.card_id == card_id)


def keys(rep: distill.CheckReport, card_id: str) -> set[str]:
    return {r.key for r in card_of(rep, card_id).reasons}


def reasons(rep: distill.CheckReport, card_id: str, key: str) -> list[distill.Reason]:
    return [r for r in card_of(rep, card_id).reasons if r.key == key]


def clean(rep: distill.CheckReport) -> None:
    """Neither the JSON nor any text line carries the seeded literal (G6)."""
    text = distill.report_json(rep).decode() + "\n".join(distill.report_lines(rep, "es"))
    text += "\n".join(distill.report_lines(rep, "en")) + repr(rep.cards)
    assert SEED not in text


def tree_sha(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts or not p.is_file():
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def fx(make_project: MakeProject) -> Fixture:
    f = applied(make_project)
    fill_all(f)
    return f


# --------------------------------------------------------------------------- happy path


def test_all_filled_cards_are_ready_and_nothing_is_written(fx: Fixture) -> None:
    before = tree_sha(fx.root)
    rep = run(fx)
    assert [c.card_id for c in rep.cards] == ["D-001", "D-002", "D-003"]
    for c in rep.cards:
        assert c.state == "ready", (c.card_id, c.reasons)
        assert c.bytes_added is not None and c.bytes_removed is not None
        assert c.bytes_added < c.bytes_removed
    assert rep.code == 0
    assert [len(c.copies) for c in rep.cards] == [1, 3, 1]
    assert tree_sha(fx.root) == before
    clean(rep)


def test_pending_cards_are_not_ready(make_project: MakeProject) -> None:
    f = applied(make_project)
    rep = run(f)
    for c in rep.cards:
        assert c.state == "not_ready"
        assert {"pending_what", "pending_invariant"} <= {r.key for r in c.reasons}
        assert {w.key for w in c.warnings} == {"pending_optional"}
    assert rep.code == 4


def test_no_card_inventory_does_not_change_the_code(fx: Fixture) -> None:
    rep = run(fx)
    by = {n.block: n for n in rep.no_card}
    assert set(by) == {fx.unit("Mapa del repositorio").id + ".b2", "U06.b1"}
    assert by["U06.b1"].dest == "L4"
    assert by[fx.unit("Mapa del repositorio").id + ".b2"].dest == "L5m"
    for n in rep.no_card:
        assert n.pin is not None and n.bytes is not None and n.bytes > 0
    assert rep.code == 0
    lines = distill.report_lines(rep, "es")
    assert any("no_card:L5m" in x for x in lines)
    assert any("no_card:L4" in x for x in lines)


def test_card_option_selects_one_card(fx: Fixture) -> None:
    rep = run(fx, "D-002")
    assert [c.card_id for c in rep.cards] == ["D-002"]


def test_unknown_card_is_usage_error(fx: Fixture) -> None:
    with pytest.raises(ReorgError) as exc:
        run(fx, "D-099")
    assert exc.value.code == 1


def test_missing_run_folder_is_usage_error(make_project: MakeProject) -> None:
    project = make_project()
    with pytest.raises(ReorgError) as exc:
        distill.check(project.root, RID, card=None, lexicon=LEX)
    assert exc.value.code == 1


def test_planned_run_is_usage_error(make_project: MakeProject, fx: Fixture) -> None:
    journal = fx.root / ".declutter" / "reorg" / RID / "journal.json"
    journal.unlink()
    with pytest.raises(ReorgError) as exc:
        run(fx)
    assert exc.value.code == 1


def test_written_run_without_commit_is_checked(make_project: MakeProject) -> None:
    f = applied(make_project, commit=False)
    fill_all(f)
    assert run(f).code == 0


def test_invalid_pins_json_is_code_2(fx: Fixture) -> None:
    (fx.root / ratchet.PINS_REL).write_bytes(b"{}")
    with pytest.raises(ReorgError) as exc:
        run(fx)
    assert exc.value.code == 2


def test_non_utf8_card_is_code_2(fx: Fixture) -> None:
    path = fx.card_path("D-003")
    path.write_bytes(path.read_bytes() + b"\xff\n")
    rep = run(fx)
    assert keys(rep, "D-003") == {"card_unreadable"}
    assert rep.code == 2


# --------------------------------------------------------------------------- (a)


def test_pending_in_what(fx: Fixture) -> None:
    fill(fx, "D-001", what="Algo PENDIENTE aquí.")
    assert keys(run(fx), "D-001") == {"pending_what"}


def test_pending_in_invariant(fx: Fixture) -> None:
    fill(fx, "D-001", [*INV_ES["D-001"], "PENDIENTE"])
    assert "pending_invariant" in keys(run(fx), "D-001")


def test_pending_word_is_case_sensitive(fx: Fixture) -> None:
    fill(fx, "D-001", what="Nada pendiente.")
    assert card_of(run(fx), "D-001").state == "ready"


def test_invariant_not_stated(fx: Fixture) -> None:
    fill(fx, "D-001", ["No consta."])
    assert "invariant_not_stated" in keys(run(fx), "D-001")


def test_invariant_empty(fx: Fixture) -> None:
    fill(fx, "D-001", ["", ""])
    assert keys(run(fx), "D-001") == {"invariant_empty"}


def test_pending_only_in_pitfall_is_a_warning(fx: Fixture) -> None:
    fill(fx, "D-001", pitfall="PENDIENTE")
    c = card_of(run(fx), "D-001")
    assert c.state == "ready"
    assert [(w.key, w.section) for w in c.warnings] == [("pending_optional", "pitfall")]


def test_help_line_may_be_removed(fx: Fixture) -> None:
    fill(fx, "D-001", keep_help=False)
    assert card_of(run(fx), "D-001").state == "ready"


# --------------------------------------------------------------------------- (b1) (b2)

_LITERAL_D001 = INV_ES["D-001"][0]


@pytest.mark.parametrize(
    ("drop", "cls"),
    [
        (" ni más de 3 veces", "number"),
        (" antes del 2026-10-09", "date"),
        (" a docs/pub/", "path"),
        (" de M13-05", "ident"),
        (" ni `docs/x.md`", "code"),
        (f" {SEED}", "ident"),
    ],
)
def test_token_missing_by_class(fx: Fixture, drop: str, cls: str) -> None:
    fill(fx, "D-001", [_LITERAL_D001.replace(drop, "")])
    rep = run(fx)
    missing = reasons(rep, "D-001", "token_missing")
    assert [r.cls for r in missing] == [cls]
    r = missing[0]
    u = fx.unit("Sesión 2026-10-05")
    assert r.path == "CLAUDE.md" and r.line == u.blocks[0].lines[0]
    assert r.col is not None and r.col > 1 and r.length is not None and r.length > 0
    clean(rep)


def test_token_missing_column_points_at_the_token(fx: Fixture) -> None:
    fill(fx, "D-001", [_LITERAL_D001.replace(" de M13-05", "")])
    r = reasons(run(fx), "D-001", "token_missing")[0]
    assert r.line is not None and r.col is not None and r.length is not None
    literal = TEXT_ES.split("\n")[r.line - 1]
    assert literal[r.col - 1 : r.col - 1 + r.length] == "M13-05"


def test_token_invented(fx: Fixture) -> None:
    fill(fx, "D-001", [_LITERAL_D001 + " Máximo 77."])
    rep = run(fx)
    inv = reasons(rep, "D-001", "token_invented")
    card_rel = fx.card_path("D-001").relative_to(fx.root).as_posix()
    assert [(r.cls, r.path) for r in inv] == [("number", card_rel)]
    lines = fx.card_path("D-001").read_text().split("\n")
    r = inv[0]
    assert r.line is not None and r.col is not None
    assert lines[r.line - 1][r.col - 1 : r.col + 1] == "77"


def test_token_of_the_unit_outside_the_copies_is_not_invented(fx: Fixture) -> None:
    fill(fx, "D-002", [*INV_ES["D-002"], "- Cola con 12 hilos desde 2026-10-06."])
    assert card_of(run(fx), "D-002").state == "ready"


# --------------------------------------------------------------------------- (c)


def test_one_never_less(fx: Fixture) -> None:
    inv = list(INV_ES["D-002"])
    inv[2] = "- Guarda el token tres fuera de .env."
    fill(fx, "D-002", inv)
    rep = run(fx)
    mismatch = reasons(rep, "D-002", "deontic_mismatch")
    assert [r.counts for r in mismatch] == [(("never", 2, 1),)]
    assert any("never 2≠1" in x for x in distill.report_lines(rep, "es"))


def test_one_always_more(fx: Fixture) -> None:
    fill(fx, "D-001", [_LITERAL_D001 + " Siempre."])
    assert "deontic_mismatch" in keys(run(fx), "D-001")


def test_nunca_rewritten_as_never(fx: Fixture) -> None:
    fill(fx, "D-001", [_LITERAL_D001.replace("Nunca subas", "Never push")])
    assert card_of(run(fx), "D-001").state == "ready"


# --------------------------------------------------------------------------- (d)


def _history(fx: Fixture) -> Path:
    return fx.root / fx.unit("Sesión 2026-10-05").file


def test_history_byte_changed(fx: Fixture) -> None:
    replace_in(_history(fx), "más de 3 veces", "más de 4 veces")
    assert "history_altered" in keys(run(fx), "D-001")


def test_history_end_marker_deleted(fx: Fixture) -> None:
    u = fx.unit("Sesión 2026-10-05")
    replace_in(_history(fx), f"<!-- declutter:end {u.id} -->\n", "")
    assert "marker_missing" in keys(run(fx), "D-001")


def test_literal_sha_of_card_changed(fx: Fixture) -> None:
    u = fx.unit("Sesión 2026-10-05")
    replace_in(fx.card_path("D-001"), u.sha256, "0" * 64)
    assert "origin_changed" in keys(run(fx), "D-001")


def test_date_line_of_card_changed(fx: Fixture) -> None:
    replace_in(fx.card_path("D-001"), "Zona: no consta", "Zona: otra")
    assert "origin_changed" in keys(run(fx), "D-001")


# --------------------------------------------------------------------------- structure


def test_card_marker_missing_or_duplicated(fx: Fixture) -> None:
    path = fx.card_path("D-001")
    replace_in(path, f"<!-- declutter:gen card {RID} -->\n", "")
    assert "card_markers" in keys(run(fx), "D-001")
    fill(fx, "D-002", [*INV_ES["D-002"], f"<!-- declutter:gen card {RID} -->"])
    assert "card_markers" in keys(run(fx), "D-002")


def test_section_renamed_or_reordered(fx: Fixture) -> None:
    replace_in(fx.card_path("D-001"), "## Trampa\n", "## Trampas\n")
    assert "card_structure" in keys(run(fx), "D-001")
    path = fx.card_path("D-002")
    replace_in(path, "## Por qué\n", "## TMP\n")
    replace_in(path, "## Qué\n", "## Por qué\n")
    replace_in(path, "## TMP\n", "## Qué\n")
    assert "card_structure" in keys(run(fx), "D-002")


def test_title_line_changed(fx: Fixture) -> None:
    replace_in(fx.card_path("D-001"), "# D-001 Sesión", "# D-001 Otra sesión")
    assert "card_structure" in keys(run(fx), "D-001")


def test_state_changed_to_another_word(fx: Fixture) -> None:
    replace_in(fx.card_path("D-001"), "estado: pendiente-destilar", "estado: lista")
    assert "card_state" in keys(run(fx), "D-001")


def test_card_with_open_fence(fx: Fixture) -> None:
    path = fx.card_path("D-001")
    path.write_bytes(path.read_bytes() + b"```\nabierta\n")
    assert keys(run(fx), "D-001") == {"card_unbalanced"}


@pytest.mark.parametrize(
    ("extra", "key"),
    [
        (["<!-- abierto"], "invariant_unbalanced"),
        (["## x"], "invariant_heading"),
        (["---"], "invariant_heading"),
        (["<!-- declutter:gen x -->"], "invariant_marker"),
        (["@docs/x.md"], "invariant_import"),
    ],
)
def test_invariant_structure(fx: Fixture, extra: list[str], key: str) -> None:
    fill(fx, "D-002", [*INV_ES["D-002"], *extra])
    assert key in keys(run(fx), "D-002")


def test_heading_inside_closed_fence_is_allowed(fx: Fixture) -> None:
    fill(fx, "D-002", [*INV_ES["D-002"], "```", "## dentro", "```"])
    assert "invariant_heading" not in keys(run(fx), "D-002")


def test_invariant_with_carriage_return(fx: Fixture) -> None:
    fill(fx, "D-002", [*INV_ES["D-002"][:2], INV_ES["D-002"][2] + "\r"])
    assert "invariant_cr" in keys(run(fx), "D-002")


def test_invariant_without_its_category(fx: Fixture) -> None:
    fill(fx, "D-003", ["Nunca subas la clave de src/a, src/b ni src/c."])
    assert keys(run(fx), "D-003") == {"categories_lost"}


def test_invariant_longer_than_the_copies(fx: Fixture) -> None:
    long = _LITERAL_D001 + " " + "relleno " * 60
    fill(fx, "D-001", [long])
    assert keys(run(fx), "D-001") == {"not_shorter"}


def test_invariant_with_more_lines_than_the_copies(fx: Fixture) -> None:
    """Enmienda 05-11: entry lines (header + invariant + blank) <= removed lines, even
    when the entry has fewer bytes."""
    inv = INV_ES["D-001"][0]
    words = inv.split(" ")
    half = len(words) // 2
    fill(fx, "D-001", [" ".join(words[:half]), " ".join(words[half:])])
    rs = reasons(run(fx), "D-001", "not_shorter")
    assert [r.detail for r in rs] == ["4 > 3 lines: shorten the invariant"]
    fill(fx, "D-001")  # one line again: ready
    assert card_of(run(fx), "D-001").state == "ready"


# --------------------------------------------------------------------------- secret (e5)


def test_secret_in_the_invariant_is_code_5(fx: Fixture) -> None:
    value = "AKIA" + "ABCDEFGHIJKLMNOP"
    fill(fx, "D-001", [_LITERAL_D001 + " " + value])
    rep = run(fx)
    hit = reasons(rep, "D-001", "invariant_secret")
    assert hit and hit[0].code == 5 and hit[0].line is not None
    assert rep.code == 5
    text = distill.report_json(rep).decode() + "\n".join(distill.report_lines(rep, "es"))
    assert value not in text


# --------------------------------------------------------------------------- localization


def _agents(fx: Fixture) -> Path:
    return fx.root / "AGENTS.md"


def test_copy_header_edited(fx: Fixture) -> None:
    replace_in(_agents(fx), "### ⟨U02.b1⟩ →", "### ⟨U02.b1⟩  →")
    assert keys(run(fx), "D-001") == {"header_changed"}


def test_blank_line_after_copy_removed(fx: Fixture) -> None:
    replace_in(_agents(fx), "\n\n### ⟨U03.b2⟩", "\n### ⟨U03.b2⟩")
    assert "header_changed" in keys(run(fx), "D-001")


def test_copy_duplicated_by_hand(fx: Fixture) -> None:
    lines = _agents(fx).read_bytes().split(b"\n")
    copy = next(x for x in lines if x.startswith(b"- Nunca subas el password uno"))
    _agents(fx).write_bytes(_agents(fx).read_bytes() + b"\n" + copy + b"\n")
    assert keys(run(fx), "D-002") == {"copy_ambiguous"}


def test_copy_removed(fx: Fixture) -> None:
    lines = _agents(fx).read_bytes().split(b"\n")
    kept = [x for x in lines if not x.startswith(b"- Nunca subas el password uno")]
    _agents(fx).write_bytes(b"\n".join(kept))
    assert "copy_not_located" in keys(run(fx), "D-002")


def _unpin(fx: Fixture, pin_id: str) -> None:
    line = ratchet.log_line(
        "unpin", date="2026-11-05", reorg=RID, old=None, new=None, reason="prueba", pin=pin_id
    )
    log = fx.root / ratchet.LOG_REL
    log.write_bytes(log.read_bytes() + line)


def test_copy_with_previous_unpin(fx: Fixture) -> None:
    rep = run(fx)
    pin = card_of(rep, "D-001").copies[0].pin
    assert pin is not None
    _unpin(fx, pin)
    r = reasons(run(fx), "D-001", "pin_released")
    assert [x.pin for x in r] == [pin]


def test_pin_missing(fx: Fixture) -> None:
    pins = ratchet.pins_loads((fx.root / ratchet.PINS_REL).read_bytes())
    kept = [p for p in pins if p.block != "U02.b1"]
    (fx.root / ratchet.PINS_REL).write_bytes(ratchet.pins_dumps(kept))
    assert "pin_missing" in keys(run(fx), "D-001")


def test_index_row_edited(fx: Fixture) -> None:
    index = fx.root / "docs/decisiones/INDICE.md"
    replace_in(index, "| D-002 | Sesión 2026-10-06 |", "| D-002 | Sesión editada |")
    assert keys(run(fx), "D-002") == {"index_row_changed"}


def test_root_and_zone_pointers_edited(fx: Fixture) -> None:
    replace_in(_agents(fx), "2026-10-08.md (pendiente de destilar)", "2026-10-08.md (otra)")
    assert keys(run(fx), "D-003") == {"pointer_changed"}
    replace_in(_agents(fx), "2026-10-08.md (otra)", "2026-10-08.md (pendiente de destilar)")
    assert card_of(run(fx), "D-003").state == "ready"
    zone = fx.root / "src" / "AGENTS.md"
    replace_in(zone, "(pendiente de destilar)", "(x)")
    assert keys(run(fx), "D-003") == {"pointer_changed"}


def test_lines_added_above_do_not_matter(fx: Fixture) -> None:
    _agents(fx).write_bytes(b"Linea nueva arriba.\n\n" + _agents(fx).read_bytes())
    assert run(fx).code == 0


# --------------------------------------------------------------------------- no copies


def test_card_without_copies(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    real: Callable[..., distill.CheckReport] = distill.check_run

    def without_copies(m, placements, read, **kw):  # type: ignore[no-untyped-def]
        kept = tuple(p for p in placements if not (p.role == "copy" and p.unit == "U02"))
        return real(m, kept, read, **kw)

    monkeypatch.setattr(distill, "check_run", without_copies)
    assert [c.card_id for c in run(fx).cards] == ["D-002", "D-003"]
    assert keys(run(fx, "D-001"), "D-001") == {"no_copies"}


# --------------------------------------------------------------------------- entry


def test_entry_format(fx: Fixture) -> None:
    rep = run(fx, "D-002")
    c = card_of(rep, "D-002")
    assert c.entry is not None
    u = fx.unit("Sesión 2026-10-06")
    path = fx.card_path("D-002").relative_to(fx.root).as_posix()
    expected = (
        f"### ⟨U03.b2, U03.b3, U03.b4⟩ D-002 → {u.file}#r{RID}-u03 · secrets · "
        f"[D-002]({path}) · CLAUDE.md:{u.lines[0]}-{u.lines[1]} · sha256 {u.sha256[:12]}\n"
        + "".join(x + "\n" for x in INV_ES["D-002"])
        + "\n"
    ).encode()
    assert c.entry.data == expected
    assert c.bytes_added == len(expected)
    assert c.entry.lines[-1] == b""
    assert c.entry.window == c.entry.lines[:-1]
    assert c.entry.sha256 == hashlib.sha256(b"\n".join(c.entry.window)).hexdigest()


def test_bytes_removed_counts_headers_copies_and_blanks(fx: Fixture) -> None:
    c = card_of(run(fx, "D-001"), "D-001")
    agents = _agents(fx).read_bytes().split(b"\n")
    i = next(n for n, x in enumerate(agents) if x.startswith("### ⟨U02.b1⟩".encode()))
    span = agents[i : i + 3]
    assert span[2] == b""
    assert c.bytes_removed == sum(len(x) + 1 for x in span)
    assert c.copies[0].bytes == len(span[1]) + 1


# --------------------------------------------------------------------------- report


def test_report_json_schema_and_lang(fx: Fixture) -> None:
    import json

    data = json.loads(distill.report_json(run(fx)))
    assert data["schema"] == "declutter.distill-check/1"
    assert data["reorg"] == RID and data["code"] == 0
    assert data["lang"] == "es" and data["paths_lang"] == "es"
    card = data["cards"][1]
    assert set(card) == {
        "card_id", "unit", "path", "state", "reasons", "warnings", "copies",
        "bytes_removed", "bytes_added",
    }  # fmt: skip
    assert set(card["copies"][0]) == {"block", "pin", "src", "lines", "bytes", "categories"}
    assert set(data["no_card"][0]) == {"block", "pin", "unit", "dest", "bytes"}
    assert data["totals"]["cards_ready"] == 3


def test_english_fixture(make_project: MakeProject) -> None:
    f = applied(make_project, "en")
    rep = run(f)
    c = card_of(rep, "D-001")
    assert {"pending_what", "pending_invariant"} <= {r.key for r in c.reasons}
    assert rep.lang == "en" and rep.paths_lang == "en"
    fill(f, "D-001", what="A synthetic rule.", why="not stated")
    c = card_of(run(f), "D-001")
    assert c.state == "ready", c.reasons
    fill(f, "D-001", what="Still PENDING.")
    assert keys(run(f), "D-001") == {"pending_what"}
    assert invariant_title("en") in f.card_path("D-001").read_text()


def test_git_clean_after_check(fx: Fixture) -> None:
    git(fx.root, "add", "-A")
    git(fx.root, "commit", "-q", "-m", "fill")
    run(fx)
    assert git(fx.root, "status", "--porcelain") == ""
