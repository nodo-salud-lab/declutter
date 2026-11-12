"""Rendering, i18n and the guided flow with its menu (spec §5.6.5, §5.7, §7, §8 G6/G9, §10.2-§10.4).

Synthetic projects only (``make_project``). Every content check seeds ``ZQX-SEED-7731``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context.i18n import MESSAGES
from declutter_context.model import Config, Project, ProofFailure
from declutter_context.reorg import Issue
from declutter_context.reorg import manifest as mf
from declutter_context.reorg import render as rr
from declutter_context.reorg.manifest import BlockOverride, Manifest, UnitOverride

NOW = datetime(2026, 11, 4, 15, 30)
RID = "20261104-1530"
SEED = "ZQX-SEED-7731"

BASE = "# Demo\nIntro del proyecto.\n\n## Cómo correr\nEjecuta make test.\n"
LOW1 = f"\n## Varios\nalgo {SEED}\n"
K_L4 = "\n## Sesión 2026-10-05\n- Nunca uses la base de prueba.\n"
NOTE_L4 = "\n## Nota 2026-10-03\nSolo registro de la sesión.\n"
G2 = "\n## Datos de pacientes\nTodo dato es confidencial.\n"
LOW2 = "\n## Otra cosa\nmás\n"
GUARDED = "\n## Cambio 2026-10-06\nNo subas el password al repo.\n"
FULL = BASE + LOW1 + K_L4 + NOTE_L4 + G2 + LOW2 + GUARDED


class FakePrompter:
    def __init__(self, tty: bool, answers: Sequence[str] = ()) -> None:
        self.tty = tty
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def is_tty(self) -> bool:
        return self.tty

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def say(self, text: str) -> None:
        self.said.append(text)

    def text(self) -> str:
        return "\n".join(self.prompts + self.said)


def _project(make_project: MakeProject, text: str = FULL) -> Project:
    project = make_project({"src/a.py": "x = 1\n"})
    (project.root / "CLAUDE.md").write_bytes(text.encode())
    return project


def _plan(project: Project, *, dry_run: bool = False) -> mf.PlanOutcome:
    return mf.plan_project(
        mf.PlanRequest(project=project, cfg=Config(), lang="es", now=NOW, dry_run=dry_run)
    )


def _unit(m: Manifest, heading: str) -> mf.UnitEntry:
    return next(u for u in m.units if u.heading == heading)


def _stored(project: Project) -> Manifest:
    m, _ = mf.load_run(project.root, RID)
    return m


def _unit_overrides(m: Manifest, key: str) -> dict[str, tuple[object, str]]:
    return {
        o.field: (o.value, o.reason)
        for o in m.overrides
        if isinstance(o, UnitOverride) and o.heading_key == key
    }


def _goto(order: Sequence[str], current: str, target: str) -> list[str]:
    i, j = order.index(current), order.index(target)
    return ["n"] * (j - i) if j >= i else ["p"] * (i - j)


def _tree(root: Path) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            out[os.path.relpath(Path(dirpath) / d, root) + "/"] = None
        for f in filenames:
            p = Path(dirpath) / f
            out[os.path.relpath(p, root)] = p.read_bytes()
    return out


# --------------------------------------------------------------------------- failure line


def test_failure_line_format_without_content() -> None:
    f = ProofFailure(
        check="C2",
        unit="U13",
        source="CLAUDE.md",
        src=(202, 260),
        dest="docs/historial/2026-09.md",
        at=(41, 99),
        reason="dest line 57 differs (212 B vs 213 B)",
    )
    line = rr.failure_line(f, "es")
    assert line.startswith("C2 U13 CLAUDE.md:202-260 → docs/historial/2026-09.md:41-99: ")
    assert "212 B vs 213 B" in line
    i = Issue(code=4, key="check.16", unit="U03", source="CLAUDE.md", src=(10, 12), detail="L4")
    assert rr.failure_line(i, "en").startswith("check.16 U03 CLAUDE.md:10-12: ")
    blk = Issue(code=4, key="check.5", unit="U07", block="U07.b1", source="CLAUDE.md", src=(5, 5))
    assert rr.failure_line(blk, "es").startswith("check.5 U07.b1 CLAUDE.md:5: ")
    secret = Issue(code=5, key="secret", source="CLAUDE.md", at=(123, 123), detail="aws_access_key")
    assert rr.failure_line(secret, "es") == "CLAUDE.md:123 secreto:aws_access_key"
    assert rr.failure_line(secret, "en") == "CLAUDE.md:123 secret:aws_access_key"
    # Producers that scan outputs carry the path in ``dest`` (manifest/apply).
    out_secret = Issue(code=5, key="secret", dest="docs/x.md", at=(5, 5), detail="aws_access_key")
    assert rr.failure_line(out_secret, "es") == "docs/x.md:5 secreto:aws_access_key"
    # The message comes from the catalog, never the bare key.
    assert rr.failure_line(Issue(code=1, key="needs_yes"), "es") != "needs_yes"


# --------------------------------------------------------------------------- plan table


def test_plan_table_review_order_and_pending_mark(make_project: MakeProject) -> None:
    project = _project(make_project)
    out = _plan(project, dry_run=True)
    m = out.manifest
    order = list(rr.review_order(m))
    by_id = {u.id: u for u in m.units}
    lows = [u.id for u in m.units if u.confidence == "low"]
    assert lows and order[: len(lows)] == lows
    k_out = _unit(m, "Sesión 2026-10-05").id
    assert order[len(lows)] == k_out
    assert list(order[len(lows) + 1 :]) == [u.id for u in m.units if u.id not in (*lows, k_out)]
    table = rr.plan_table(m, "es", unit_bytes=out.unit_bytes)
    rows = [r for r in table.splitlines() if any(f" {uid} " in r for uid in by_id)]
    assert [next(uid for uid in by_id if f" {uid} " in r) for r in rows] == list(order)
    pending_row = next(r for r in rows if f" {k_out} " in r)
    assert "pendiente de aceptar" in pending_row
    assert "protegida (G2)" in next(r for r in rows if " Datos de pacientes " in r)
    assert str(out.unit_bytes[k_out]) in pending_row
    assert "rule:7" in pending_row
    guarded = next(r for r in rows if "Cambio 2026-10-06" in r)
    assert "secrets" in guarded
    long_unit = replace(m.units[1], heading="x" * 80)
    long_table = rr.plan_table(replace(m, units=(long_unit,)), "en")
    assert "x" * 59 + "…" in long_table and "x" * 60 not in long_table


def test_levers_text_lists_all_and_recommends_none() -> None:
    levers = {k: 100 + n for n, k in enumerate(mf.LEVER_KEYS)}
    for lang in ("es", "en"):
        text = rr.levers_text(levers, lang)
        for n, k in enumerate(mf.LEVER_KEYS):
            assert MESSAGES[lang][f"reorg.{k}"] in text
            assert str(100 + n) in text
        low = text.lower()
        for word in ("recomend", "recommend", "sugerimos", "suggest"):
            assert word not in low


def test_projection_text_shows_files_signals_chains_and_warnings(
    make_project: MakeProject,
) -> None:
    project = _project(make_project)
    m = _plan(project, dry_run=True).manifest
    text = rr.projection_text(m.projection, m.warnings, "es")
    assert "AGENTS.md" in text and "CLAUDE.md" in text
    for sig in ("S1", "S2", "S3", "S4"):
        assert sig in text
    assert "root" in text
    assert MESSAGES["es"]["reorg.pending_confirmation"] in text


def test_closing_text_mentions_undo_prune_merge_and_ratchet(make_project: MakeProject) -> None:
    project = make_project(git=True)
    (project.root / "CLAUDE.md").write_bytes(FULL.encode())
    import subprocess

    subprocess.run(["git", "-C", str(project.root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(project.root), "commit", "-qm", "c"], check=True)
    m = _plan(project, dry_run=True).manifest
    for lang in ("es", "en"):
        text = rr.closing_text(m, lang)
        assert f"declutter reorg undo {RID}" in text
        assert f"declutter reorg prune {RID}" in text
        assert f"git switch main && git merge declutter/reorg-{RID}" in text
        assert "declutter ratchet check" in text
        assert "install" not in text.lower() and "instal" not in text.lower()


def test_outcome_json_is_report_dumps(make_project: MakeProject) -> None:
    project = _project(make_project)
    out = _plan(project, dry_run=True)
    report = mf.outcome_report(out)
    assert rr.outcome_json(report) == mf.report_dumps(report).decode("utf-8")
    assert json.loads(rr.outcome_json(report))["id"] == RID


# --------------------------------------------------------------------------- menu


def _menu(project: Project, answers: Sequence[str]) -> tuple[str, FakePrompter]:
    p = FakePrompter(tty=True, answers=answers)
    res = rr.run_menu(project, Config(), RID, p, now=NOW, lang="es")
    return res, p


def test_menu_scripted_keys(make_project: MakeProject) -> None:
    project = _project(make_project)
    m = _plan(project).manifest
    order = list(rr.review_order(m))
    ids = {u.heading: u.id for u in m.units}
    varios, otra, sesion = ids["Varios"], ids["Otra cosa"], ids["Sesión 2026-10-05"]
    nota, como, cambio = ids["Nota 2026-10-03"], ids["Cómo correr"], ids["Cambio 2026-10-06"]
    preamble = ids[""]
    assert order[0] == varios
    script: list[str] = ["n", "p"]  # varios -> otra -> varios
    script += ["k", "se queda en la capa 1"]
    cur = varios
    for target, keys in (
        (otra, ["s", "acepto el destino", "z", "src", "es de la zona src"]),
        (sesion, ["h", "va al historial"]),
        (nota, ["f", "sin ficha para esta nota"]),
        (como, ["t", "es una trampa conocida"]),
        (preamble, ["m", "es parte del mapa"]),
        (cambio, ["u", _unit(m, "Cambio 2026-10-06").blocks[0].id, "falso positivo del campo"]),
    ):
        script += _goto(order, cur, target) + keys
        cur = target
    script += ["v", "q"]
    res, p = _menu(project, script)
    assert res == "quit"
    s = _stored(project)
    key = {u.id: u.heading_key for u in m.units}
    assert _unit_overrides(s, key[varios]) == {"keep": (True, "se queda en la capa 1")}
    assert _unit_overrides(s, key[otra]) == {
        "accept": (True, "acepto el destino"),
        "dest": ("L2:src", "es de la zona src"),
    }
    assert _unit_overrides(s, key[sesion]) == {"dest": ("L4", "va al historial")}
    card0 = _unit(m, "Nota 2026-10-03").card
    assert _unit_overrides(s, key[nota]) == {"card": (not card0, "sin ficha para esta nota")}
    assert _unit_overrides(s, key[como]) == {"dest": ("L5t", "es una trampa conocida")}
    assert _unit_overrides(s, key[preamble]) == {"dest": ("L5m", "es parte del mapa")}
    acks = [o for o in s.overrides if isinstance(o, BlockOverride)]
    assert acks == [
        BlockOverride(_unit(m, "Cambio 2026-10-06").blocks[0].key, "falso positivo del campo")
    ]
    assert SEED not in p.text()


def test_menu_g2_rejects_moves(make_project: MakeProject) -> None:
    project = _project(make_project)
    m = _plan(project).manifest
    order = list(rr.review_order(m))
    g2 = _unit(m, "Datos de pacientes").id
    script = _goto(order, order[0], g2)
    for key in ("h", "z", "t", "m", "c", "s"):
        script.append(key)
    script.append("q")
    res, p = _menu(project, script)
    assert res == "quit"
    assert _stored(project).overrides == ()
    assert sum("protegida (G2)" in x for x in p.said) >= 6


def test_menu_c_rejects_unit_with_safety(make_project: MakeProject) -> None:
    project = _project(make_project)
    m = _plan(project).manifest
    order = list(rr.review_order(m))
    target = _unit(m, "Cambio 2026-10-06").id
    res, p = _menu(project, [*_goto(order, order[0], target), "c", "motivo cualquiera", "q"])
    assert res == "quit"
    assert _stored(project).overrides == ()
    assert any(MESSAGES["es"]["reorg.menu.c_rejected"] in x for x in p.said)


def test_menu_f_only_for_l4(make_project: MakeProject) -> None:
    project = _project(make_project)
    m = _plan(project).manifest
    res, p = _menu(project, ["f", "q"])  # cursor on a low L1 unit
    assert res == "quit"
    assert _stored(project).overrides == ()
    assert any(MESSAGES["es"]["reorg.menu.f_only_l4"] in x for x in p.said)
    del m


def test_menu_apply_only_after_passing_check(make_project: MakeProject) -> None:
    project = _project(make_project, BASE + K_L4)
    m = _plan(project).manifest
    order = list(rr.review_order(m))
    target = _unit(m, "Sesión 2026-10-05").id
    res, p = _menu(project, ["A", "q"])
    assert res == "quit"
    assert any(MESSAGES["es"]["reorg.menu.a_unavailable"] in x for x in p.said)
    res, p = _menu(project, [*_goto(order, order[0], target), "s", "revisada a mano", "A"])
    assert res == "apply"


def test_menu_low_units_presented_first(make_project: MakeProject) -> None:
    project = _project(make_project)
    m = _plan(project).manifest
    lows = [u for u in m.units if u.confidence == "low"]
    assert len(lows) == 2
    res, p = _menu(project, ["n", "q"])
    assert res == "quit"
    shown = [x for x in p.said if x.startswith("→ ")]
    assert shown[0].startswith(f"→ {lows[0].id} ") and shown[1].startswith(f"→ {lows[1].id} ")
    assert any(MESSAGES["es"]["reorg.menu.low_intro"].split("{")[0] in x for x in p.said)


def test_menu_edit_unbalanced_quotes(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(make_project)
    _plan(project)
    monkeypatch.setenv("EDITOR", 'vi "unclosed')
    res, p = _menu(project, ["e", "q"])
    assert res == "quit"
    assert any(MESSAGES["es"]["reorg.menu.editor_invalid"] in x for x in p.said)


def test_menu_edit_missing_editor(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(make_project)
    _plan(project)
    monkeypatch.setenv("EDITOR", str(project.root / "no-such-editor-zqx"))
    res, p = _menu(project, ["e", "q"])
    assert res == "quit"
    assert any(MESSAGES["es"]["reorg.menu.editor_failed"] in x for x in p.said)


def test_menu_empty_answer_quits(make_project: MakeProject) -> None:
    project = _project(make_project)
    _plan(project)
    res, _ = _menu(project, [])
    assert res == "quit"


# --------------------------------------------------------------------------- guide


def test_guide_without_tty_is_plan_dry_run_and_writes_nothing(make_project: MakeProject) -> None:
    project = _project(make_project)
    before = _tree(project.root)
    p = FakePrompter(tty=False)
    code = rr.run_guide(project, Config(), p, now=NOW, lang="es")
    assert code == 0
    assert _tree(project.root) == before
    assert not (project.root / ".declutter").exists()
    assert p.prompts == []
    assert any("Varios" in x for x in p.said)
    assert SEED not in p.text()


def test_guide_without_tty_returns_plan_code_on_secret(make_project: MakeProject) -> None:
    project = _project(make_project, BASE + "\nAKIA" + "ABCDEFGHIJKLMNOP\n")
    before = _tree(project.root)
    p = FakePrompter(tty=False)
    code = rr.run_guide(project, Config(), p, now=NOW, lang="es")
    assert code == 5
    assert _tree(project.root) == before
    assert "ABCDEFGHIJKLMNOP" not in p.text()
    assert any("secreto:aws_access_key" in x for x in p.said)


def test_guide_tty_quit_keeps_plan(make_project: MakeProject) -> None:
    project = _project(make_project)
    p = FakePrompter(tty=True, answers=["q"])
    code = rr.run_guide(project, Config(), p, now=NOW, lang="es")
    assert code == 0
    assert (project.root / ".declutter" / "reorg" / RID / "manifest.json").exists()
    assert not (project.root / "AGENTS.md").exists()
    assert SEED not in p.text()


def test_guide_tty_apply_without_git(make_project: MakeProject) -> None:
    project = _project(make_project, BASE + NOTE_L4)
    p = FakePrompter(tty=True, answers=["A", "s"])
    code = rr.run_guide(project, Config(), p, now=NOW, lang="es")
    assert code == 0, p.said
    assert (project.root / "AGENTS.md").exists()
    text = p.text()
    assert "AGENTS.md" in text  # step 5 lists the files
    assert f"declutter reorg undo {RID}" in text  # step 7
    assert "declutter ratchet check" in text


# --------------------------------------------------------------------------- i18n


ISSUE_KEYS = [f"check.{n}" for n in range(1, 17)] + [f"C{n}" for n in range(10)] + [
    "G2", "G3", "secret", "unsafe_read", "not_utf8", "nul_byte", "open_fence", "open_comment",
    "open_front_matter", "front_matter_conflict", "extend_open_structure", "agents_override",
    "gitignore_no_star", "paths_both_langs", "detached_head", "operation_in_progress", "dirty",
    "ref_exists", "git_normalizes", "blob_differs", "not_repo", "already_applied", "needs_yes",
    "g9_tty", "concurrent_edit", "commit_rejected", "run_missing", "codex_budget",
    "ratchet_would_fail", "ambiguous_override", "manifest_invalid", "replace_symlink",
]  # fmt: skip
WARNING_KEYS = [
    "l1_over_budget", "codex_warn", "open_branch_touches_source", "relative_link_moved",
    "import_moved", "import_depth", "import_codex", "pending_confirmation",
    "ratchet_would_fail", "override_orphan", "override_ambiguous", "zone_over_limit",
    "git_normalizes_lf_safe",
]  # fmt: skip
STATE_KEYS = [
    "edited_after", "literal_intact", "history_altered", "marker_missing", "excerpt_altered",
    "pin_missing", "protected_altered", "generated_edited",
]  # fmt: skip


def test_i18n_keys_exist_in_both_langs() -> None:
    keys = [f"reorg.{k}" for k in (*ISSUE_KEYS, *WARNING_KEYS, *STATE_KEYS, *mf.LEVER_KEYS)]
    keys += [f"ratchet.R{n}" for n in range(1, 7)] + ["reorg.confirm_word"]
    for lang in ("es", "en"):
        missing = [k for k in keys if k not in MESSAGES[lang]]
        assert missing == [], (lang, missing)
    assert MESSAGES["es"]["reorg.confirm_word"] == "confirmo"
    assert MESSAGES["en"]["reorg.confirm_word"] == "confirm"
    assert set(MESSAGES["es"]) == set(MESSAGES["en"])


def test_i18n_covers_every_issue_key_in_code() -> None:
    import re

    src = Path(__file__).resolve().parents[1] / "src" / "declutter_context" / "reorg"
    found: set[str] = set()
    for f in src.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        found |= set(re.findall(r'key="([a-z][a-z0-9_.]*)"', text))
        found |= set(re.findall(r'_fail\(\s*[A-Z_]+,\s*"([a-z_]+)"', text))
        found |= set(re.findall(r'_issue\(\s*\d+,\s*"([a-z][a-z0-9_.]*)"', text))
    missing = sorted(k for k in found if f"reorg.{k}" not in MESSAGES["en"])
    assert missing == []


# --------------------------------------------------------------------------- G6


def test_render_outputs_have_no_content(make_project: MakeProject) -> None:
    project = _project(make_project)
    out = _plan(project, dry_run=True)
    m = out.manifest
    texts = [
        rr.plan_table(m, "es", unit_bytes=out.unit_bytes),
        rr.plan_table(m, "en"),
        rr.projection_text(m.projection, m.warnings, "es"),
        rr.levers_text(dict.fromkeys(mf.LEVER_KEYS, 1), "en"),
        rr.closing_text(m, "es"),
        rr.outcome_json(mf.outcome_report(out)),
        *(rr.failure_line(i, "es") for i in out.issues),
    ]
    for text in texts:
        assert SEED not in text
