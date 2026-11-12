"""Golden test of a large synthetic project.

``reorg_synth.large_synthetic_project`` builds a ``CLAUDE.md`` of 547 lines with 35 unit
ranges and fixed per-unit bytes. Every heading and line is synthetic: a heading only switches on
the features the classifier reacts to (date, R, C, T, M, G2 categories), and the bodies set the
imperative and safety lines per unit, the path mentions and the copy sizes (one list group, one
long fence and 20 one-line copies).

``plan --dry-run --json`` must reproduce the destinations, the 17 cards in order, the
pointers, the pinned copies per unit, the 18 pending confirmations, the review order of the
menu, the line totals per destination and, because the bytes are fixed, code 3
(``codex_budget``).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import MakeProject
from declutter_context import cli
from declutter_context.model import Config
from declutter_context.reorg import EXIT_THRESHOLD, mdblocks, render
from declutter_context.reorg import manifest as mf
from reorg_synth import large_synthetic_lines, large_synthetic_project

NOW = datetime(2026, 11, 4, 15, 30)

H10 = "docs/historial/2026-10.md"
H09 = "docs/historial/2026-09.md"

# unit -> (lines, destination file, card, pointer, pinned copies, confidence).
EXPECTED: dict[str, tuple[tuple[int, int], str, str | None, str | None, int, str]] = {
    "U00": ((1, 10), "AGENTS.md", None, None, 0, "high"),
    "U01": ((11, 49), "AGENTS.md", None, None, 0, "high"),
    "U02": ((50, 69), "AGENTS.md", None, None, 0, "high"),
    "U03": ((70, 77), H10, "D-001", "root", 0, "medium"),
    "U04": ((78, 82), H10, None, None, 0, "high"),
    "U05": ((83, 86), H10, None, None, 0, "high"),
    "U06": ((87, 97), H10, "D-002", None, 1, "medium"),
    "U07": ((98, 109), "AGENTS.md", None, None, 0, "high"),
    "U08": ((110, 134), "AGENTS.md", None, None, 0, "high"),
    "U09": ((135, 166), "AGENTS.md", None, None, 0, "high"),
    "U10": ((167, 184), H10, "D-003", "root", 1, "medium"),
    "U11": ((185, 197), H10, "D-004", "zone:functions", 0, "medium"),
    "U12": ((198, 201), H10, None, None, 1, "high"),
    "U13": ((202, 260), H09, "D-005", "root", 4, "medium"),
    "U14": ((261, 272), "AGENTS.md", None, None, 0, "high"),
    "U15": ((273, 285), "AGENTS.md", None, None, 0, "low"),
    "U16": ((286, 362), "docs/mapa.md", None, "root", 1, "high"),
    "U17": ((363, 379), "AGENTS.md", None, None, 0, "low"),
    "U18": ((380, 390), "docs/trampas.md", None, "root", 3, "high"),
    "U19": ((391, 405), H10, "D-006", "zone:src", 2, "medium"),
    "U20": ((406, 419), H10, "D-007", "zone:src", 2, "medium"),
    "U21": ((420, 431), H10, "D-008", None, 1, "medium"),
    "U22": ((432, 440), H10, "D-009", None, 1, "medium"),
    "U23": ((441, 451), "AGENTS.md", None, None, 0, "high"),
    "U24": ((452, 462), H10, "D-010", None, 1, "medium"),
    "U25": ((463, 472), H10, None, None, 0, "high"),
    "U26": ((473, 488), H10, "D-011", None, 2, "medium"),
    "U27": ((489, 501), H10, None, None, 1, "high"),
    "U28": ((502, 505), "AGENTS.md", None, None, 0, "high"),
    "U29": ((506, 517), H10, "D-012", None, 1, "medium"),
    "U30": ((518, 527), H10, "D-013", None, 0, "medium"),
    "U31": ((528, 531), H10, "D-014", None, 0, "medium"),
    "U32": ((532, 535), H10, "D-015", None, 0, "medium"),
    "U33": ((536, 544), H10, "D-016", None, 0, "medium"),
    "U34": ((545, 547), H10, "D-017", None, 0, "medium"),
}
DEST = {"AGENTS.md": "L1", H10: "L4", H09: "L4", "docs/mapa.md": "L5m", "docs/trampas.md": "L5t"}
G2_UNITS = ("U08", "U09", "U14", "U23", "U28")
DEMOTED = ("U03", "U10", "U13")
PENDING = (
    "U03", "U06", "U10", "U11", "U13", "U18", "U19", "U20", "U21", "U22", "U24", "U26",
    "U29", "U30", "U31", "U32", "U33", "U34",
)  # fmt: skip
REVIEW_HEAD = ("U15", "U17", "U03", "U11", "U30", "U31", "U32", "U33", "U34")
MEDIUM_REST = ("U06", "U10", "U13", "U19", "U20", "U21", "U22", "U24", "U26", "U29")
TOTALS = {"AGENTS.md": (11, 195, 22_979), H10: (21, 205, 53_730), H09: (1, 59, 6_012)}
TOTALS |= {"docs/mapa.md": (1, 77, 5_193), "docs/trampas.md": (1, 11, 2_586)}


def _pinned(unit: dict[str, object]) -> list[dict[str, Any]]:
    """The G1 copies of a unit (``pin`` set); the others are card or trap excerpts."""
    copies = unit["copies"]
    assert isinstance(copies, list)
    return [c for c in copies if c["pin"] is not None]


def _agents_literal(out: mf.PlanOutcome) -> dict[str, tuple[int, int]]:
    """(lines, bytes) of the primary and copy placements of the root ``AGENTS.md``."""
    lines = mdblocks.split_lines(out.build.files["AGENTS.md"]).lines
    res: dict[str, tuple[int, int]] = {}
    for role in ("primary", "copy"):
        spans = [
            lines[p.at[0] - 1 : p.at[1]]
            for p in out.build.placements
            if p.dest == "AGENTS.md" and p.role == role
        ]
        res[role] = (sum(len(s) for s in spans), sum(len(x) + 1 for s in spans for x in s))
    return res


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr(cli, "_now", lambda: NOW)


def test_large_synthetic_source_shape() -> None:
    raw = "".join(x + "\n" for x in large_synthetic_lines()).encode("utf-8")
    doc = mdblocks.parse(raw, "CLAUDE.md")
    assert (len(doc.lines), doc.bytes, doc.final_newline, doc.eol, doc.bom) == (
        547,
        90_500,
        True,
        "lf",
        False,
    )
    fences = [ln for ln in doc.lines if ln.startswith(b"```")]
    assert len(fences) == 2  # one fence (opening and closing lines)
    assert not any(b"@" in ln for ln in doc.lines)  # no ``@ruta`` imports (rule 3b)


def test_large_synthetic_golden(
    make_project: MakeProject, capsys: pytest.CaptureFixture[str]
) -> None:
    project = large_synthetic_project(make_project)
    before = sorted(p.name for p in project.root.iterdir())
    argv = ["reorg", "plan", "-C", str(project.root), "--dry-run", "--json", "--lang", "es"]
    code = cli.main(argv)
    report = json.loads(capsys.readouterr().out)
    assert sorted(p.name for p in project.root.iterdir()) == before  # dry run
    assert not (project.root / ".declutter").exists()

    units = {u["id"]: u for u in report["units"]}
    assert list(units) == list(EXPECTED)
    for uid, (lines, file, card, pointer, copies, confidence) in EXPECTED.items():
        u = units[uid]
        got = (tuple(u["lines"]), u["file"], u["card_id"], u["pointer"])
        assert got == (lines, file, card, pointer), uid
        assert u["dest"] == DEST[file], uid
        assert len(_pinned(u)) == copies, uid
        assert u["confidence"] == confidence, uid
        assert u["protected"] is (uid in G2_UNITS), uid
        assert ("budget:demoted" in u["reasons"]) is (uid in DEMOTED), uid

    pinned = [c for u in report["units"] for c in _pinned(u)]
    assert len(pinned) == 22
    assert sum(c["lines"][1] - c["lines"][0] + 1 for c in pinned) == 107
    assert [(c["lines"], c["pin"]) for c in _pinned(units["U10"])] == [([169, 183], "list_group")]
    (fence,) = _pinned(units["U16"])
    assert fence["lines"][1] - fence["lines"][0] + 1 == 72  # the whole fence (decision 4)

    cards = [u["card_id"] for u in report["units"] if u["card_id"]]
    assert cards == [f"D-{i:03d}" for i in range(1, 18)]
    assert tuple(report["pending_confirmation"]) == PENDING

    for file, (n_units, n_lines, n_bytes) in TOTALS.items():
        rows = [u for u in report["units"] if u["file"] == file]
        assert len(rows) == n_units, file
        assert sum(u["lines"][1] - u["lines"][0] + 1 for u in rows) == n_lines, file
        assert sum(u["bytes"] for u in rows) == n_bytes, file
    assert sum(u["bytes"] for u in report["units"]) == 90_500

    # Code 3 (the Codex budget check, ``check.9``) and the levers, none chosen.
    assert code == EXIT_THRESHOLD and report["code"] == EXIT_THRESHOLD
    assert {i["key"] for i in report["issues"]} == {"check.9"}
    assert set(report["levers"]) == {
        "lever_review_low", "lever_guard_ack", "lever_codex_budget", "lever_distill",
        "lever_revisit_g2",
    }  # fmt: skip
    assert report["levers"]["lever_review_low"] == 768 + 1_149  # U15 + U17
    assert report["levers"]["lever_guard_ack"] == 15_438  # every literal copy
    proj = report["projection"]
    assert proj["before"]["S1"] == 547 and proj["before"]["S3"] is None
    assert proj["before"]["S4"] == pytest.approx(100 * 27 / 34)
    assert proj["after"]["S4"] == pytest.approx(100 * 5 / 15)  # calculated by hand
    assert proj["after"]["S3"] > 100  # over the Codex budget

    out = mf.plan_project(
        mf.PlanRequest(project=project, cfg=Config(), lang="es", now=NOW, dry_run=True)
    )
    literal = _agents_literal(out)
    assert literal["primary"] == (195, 22_979)
    assert literal["copy"] == (107, 15_438)  # 38,417 B of literal text in AGENTS.md
    outputs = {o["path"] for o in report["outputs"]}
    zones = {p.split("/")[0] for p in outputs if p.endswith("/AGENTS.md")}
    assert zones == {"src", "functions"}  # pointers of U19/U20 and U11; no rule 10 or 8
    assert {"src/CLAUDE.md", "functions/CLAUDE.md"} <= outputs
    agents = out.build.files["AGENTS.md"].decode("utf-8").split("\n")
    index = [x for x in agents if "docs/trampas.md#" in x and x.startswith("- ")]
    assert len(index) == 6  # one line per first-level item of U18
    order = render.review_order(out.manifest)
    assert order[: len(REVIEW_HEAD)] == REVIEW_HEAD
    assert order[len(REVIEW_HEAD) : len(REVIEW_HEAD) + len(MEDIUM_REST)] == MEDIUM_REST
    assert sorted(order) == sorted(EXPECTED)
