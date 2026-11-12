"""Tests for i18n and render."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from declutter_context.i18n import MESSAGES, t
from declutter_context.model import (
    Estado,
    EstadoItem,
    Level,
    Measurement,
    Project,
    RadarRow,
    RegistryEntry,
    Signal,
)
from declutter_context.render import (
    measurement_json,
    measurement_text,
    radar_json,
    radar_markdown,
    radar_text,
)


def _project(tmp_path: Path) -> Project:
    return Project.at(tmp_path, home=tmp_path, claude_home=tmp_path / ".claude")


def _measurement(tmp_path: Path) -> Measurement:
    return Measurement(
        _project(tmp_path),
        (
            Signal(
                "S1",
                "Root instruction lines",
                450,
                "lines",
                Level.RED,
                200,
                400,
                "CLAUDE.md 450 lines",
                "act.split_claude_md",
            ),
            Signal(
                "S2",
                "Startup tokens (Claude)",
                9500.5,
                "tokens",
                Level.YELLOW,
                8000,
                20000,
                "",
                "act.trim_startup",
            ),
            Signal("S3", "Codex budget", 10, "%", Level.GREEN, 60, 100, "", "act.codex_budget"),
            Signal("S8", "Branches & worktrees", None, "count", Level.NA, 50, 150, "no git", ""),
        ),
    )


def _row(tmp_path: Path, name: str, level: Level, **kw: object) -> RadarRow:
    base: dict[str, object] = {
        "entry": RegistryEntry(tmp_path / name, name, "sess"),
        "level": level,
        "last_activity": datetime(2026, 11, 3, 10, 0),
        "dirty": 2,
        "ahead": 0,
        "estado": None,
        "worst": None,
    }
    base.update(kw)
    return RadarRow(**base)  # type: ignore[arg-type]


def test_t_fallbacks() -> None:
    assert t("level.red", "es") == "rojo"
    assert t("level.red", "xx") == "red"
    assert t("nope.key", "es") == "nope.key"
    assert t("tokens_note", "en", bpt=3.6) == "token counts are estimates: bytes / 3.6"
    assert t("tokens_note", "en") == "token counts are estimates: bytes / {bpt}"


def test_catalogs_have_same_keys() -> None:
    assert set(MESSAGES["en"]) == set(MESSAGES["es"])


def test_measurement_text_en(tmp_path: Path) -> None:
    out = measurement_text(_measurement(tmp_path), "en")
    lines = out.splitlines()
    assert lines[0] == f"🔴 {tmp_path.resolve()} — red"
    assert "🔴 S1 Root instruction lines: 450 lines (y>200 r>400) CLAUDE.md 450 lines" in out
    assert "⚪ S8 Branches & worktrees: n/a count (y>50 r>150) no git" in out
    assert "9500.5 tokens" in out
    assert "Next actions" in out
    assert out.index("S1: Move history") < out.index("S2: Cut what loads")
    assert "S3: Shrink" not in out
    assert lines[-1] == "token counts are estimates: bytes / 3.6"


def test_measurement_text_es(tmp_path: Path) -> None:
    out = measurement_text(_measurement(tmp_path), "es", bytes_per_token=4.0)
    assert "— rojo" in out
    assert "Siguientes acciones" in out
    assert "Líneas de instrucción raíz" in out
    assert out.splitlines()[-1] == "los tokens son estimaciones: bytes / 4"


def test_measurement_text_all_green_has_no_actions(tmp_path: Path) -> None:
    m = Measurement(
        _project(tmp_path),
        (Signal("S1", "x", 10, "lines", Level.GREEN, 200, 400, "", "act.split_claude_md"),),
    )
    out = measurement_text(m, "en")
    assert "Next actions" not in out
    assert out.startswith("🟢")


def test_measurement_text_red_before_yellow(tmp_path: Path) -> None:
    m = Measurement(
        _project(tmp_path),
        (
            Signal("S1", "x", 300, "lines", Level.YELLOW, 200, 400, "", "act.split_claude_md"),
            Signal("S2", "x", 30000, "tokens", Level.RED, 8000, 20000, "", "act.trim_startup"),
        ),
    )
    out = measurement_text(m, "en")
    assert out.index("S2: Cut") < out.index("S1: Move")


def test_measurement_json_roundtrip(tmp_path: Path) -> None:
    data = json.loads(measurement_json(_measurement(tmp_path)))
    assert data["level"] == "red"
    assert data["project"] == str(tmp_path.resolve())
    assert [s["id"] for s in data["signals"]] == ["S1", "S2", "S3", "S8"]
    assert data["signals"][3]["value"] is None
    assert data["signals"][3]["level"] == "na"
    assert set(data["signals"][0]) == {
        "id",
        "name",
        "value",
        "unit",
        "level",
        "yellow",
        "red",
        "detail",
        "action",
    }


def test_measurement_all_na(tmp_path: Path) -> None:
    m = Measurement(_project(tmp_path), ())
    assert json.loads(measurement_json(m))["level"] == "na"
    assert measurement_text(m, "es").startswith("⚪")


def _rows(tmp_path: Path) -> list[RadarRow]:
    estado = Estado(
        path=tmp_path / "ESTADO.md",
        updated=date(2026, 11, 1),
        phase="build",
        next_ai=(EstadoItem("write tests"), EstadoItem("old", done=True)),
        human=(EstadoItem("decide | scope"),),
        blocked=("b",),
    )
    worst = Signal("S1", "Root instruction lines", 450, "lines", Level.RED, 200, 400, "450 lines")
    return [
        _row(tmp_path, "alpha", Level.RED, estado=estado, worst=worst),
        _row(tmp_path, "beta", Level.NA, last_activity=None, dirty=None, ahead=None),
        _row(tmp_path, "gamma", Level.NA, error="not a directory"),
    ]


def test_radar_text_en(tmp_path: Path) -> None:
    out = radar_text(_rows(tmp_path), "en")
    lines = out.splitlines()
    assert "Project" in lines[0] and "Worst signal" in lines[0]
    assert "alpha" in lines[1] and "2026-11-03" in lines[1] and "S1 450 lines" in lines[1]
    assert "—" in lines[2]
    assert "no state file: run declutter init or /declutter:declutter-check --deep" in lines[2]
    assert "not a directory" in lines[3]
    assert "  - write tests" in out
    assert "old" not in out
    assert "decide | scope" in out


def test_radar_text_es_no_state(tmp_path: Path) -> None:
    out = radar_text(_rows(tmp_path), "es")
    assert "sin ESTADO.md: usa declutter init o /declutter:declutter-check --deep" in out
    assert "Decide el humano" in out


def test_radar_empty_rows() -> None:
    assert radar_text([], "en").count("\n") == 0
    md = radar_markdown([], "es", date(2026, 11, 4))
    assert "| Nivel" not in md and "| Niv |" in md
    assert json.loads(radar_json([])) == []


def test_radar_markdown(tmp_path: Path) -> None:
    md = radar_markdown(_rows(tmp_path), "en", date(2026, 11, 4))
    assert md.startswith("# Project radar")
    assert "2026-11-04" in md
    assert "| Lvl | Project | Session |" in md
    assert "|---|---|---|---|---|---|---|---|---|" in md
    assert "## alpha" in md
    assert "### Next (AI)" in md and "- write tests" in md
    assert "### Human decides" in md and "- decide | scope" in md
    assert "## beta" not in md
    assert md.count("\n| ") >= 4  # header + 3 rows
    assert "decide \\| scope" not in md  # pipes in list items are not escaped, only in cells


def test_radar_json_roundtrip(tmp_path: Path) -> None:
    data = json.loads(radar_json(_rows(tmp_path)))
    assert [d["name"] for d in data] == ["alpha", "beta", "gamma"]
    a = data[0]
    assert a["level"] == "red"
    assert a["last_activity"] == "2026-11-03T10:00:00"
    assert a["estado"]["updated"] == "2026-11-01"
    assert a["estado"]["next_ai"] == [
        {"text": "write tests", "done": False},
        {"text": "old", "done": True},
    ]
    assert a["worst"]["id"] == "S1"
    assert data[1]["estado"] is None and data[1]["last_activity"] is None
    assert data[1]["dirty"] is None
    assert data[2]["error"] == "not a directory"
