"""Tests for signals S1-S5 (synthetic data only)."""

from __future__ import annotations

import pytest

from conftest import MakeProject
from declutter_context.model import Config, Level
from declutter_context.signals_files import (
    _CHANGELOG_RE,
    s1_instruction_lines,
    s2_startup_tokens,
    s3_codex_budget,
    s4_changelog_ratio,
    s5_memory,
)

CFG = Config()


def _lines(n: int) -> str:
    return "line\n" * n


# ---------------- S1 ----------------


@pytest.mark.parametrize(
    ("n", "level"),
    [(200, Level.GREEN), (201, Level.YELLOW), (400, Level.YELLOW), (401, Level.RED)],
)
def test_s1_boundaries(make_project: MakeProject, n: int, level: Level) -> None:
    s = s1_instruction_lines(make_project({"CLAUDE.md": _lines(n)}), CFG)
    assert s.id == "S1"
    assert s.value == n
    assert s.level is level
    assert (s.yellow, s.red) == (200, 400)
    assert s.action == "act.split_claude_md"
    assert s.detail == f"CLAUDE.md {n} + AGENTS.md 0 lines"


def test_s1_sums_both_files(make_project: MakeProject) -> None:
    s = s1_instruction_lines(make_project({"CLAUDE.md": _lines(150), "AGENTS.md": _lines(51)}), CFG)
    assert s.value == 201
    assert s.level is Level.YELLOW
    assert s.detail == "CLAUDE.md 150 + AGENTS.md 51 lines"


def test_s1_na_when_no_files(make_project: MakeProject) -> None:
    s = s1_instruction_lines(make_project(), CFG)
    assert s.value is None
    assert s.level is Level.NA


def test_s1_only_agents(make_project: MakeProject) -> None:
    s = s1_instruction_lines(make_project({"AGENTS.md": _lines(3)}), CFG)
    assert s.value == 3
    assert s.level is Level.GREEN


# ---------------- S2 ----------------


@pytest.mark.parametrize(
    ("n", "level"),
    [(8_000, Level.GREEN), (8_001, Level.YELLOW), (20_000, Level.YELLOW), (20_001, Level.RED)],
)
def test_s2_boundaries(make_project: MakeProject, n: int, level: Level) -> None:
    p = make_project({"CLAUDE.md": "a" * n})
    s = s2_startup_tokens(p, Config(bytes_per_token=1.0))
    assert s.value == n
    assert s.level is level
    assert s.unit == "tokens"
    assert s.action == "act.trim_startup"


def test_s2_ceil_and_detail_top3(make_project: MakeProject) -> None:
    p = make_project(
        {
            "CLAUDE.md": "a" * 100,
            ".claude/rules/big.md": "b" * 300,
            ".claude/rules/mid.md": "c" * 200,
            ".claude/rules/small.md": "d" * 10,
        }
    )
    s = s2_startup_tokens(p, Config(bytes_per_token=3.0))
    assert s.value == 204  # ceil(610 / 3)
    assert s.detail == (
        ".claude/rules/big.md ~100 tok, .claude/rules/mid.md ~67 tok, CLAUDE.md ~34 tok"
    )


# ---------------- S3 ----------------


@pytest.mark.parametrize(
    ("n", "level"),
    [(600, Level.GREEN), (601, Level.YELLOW), (1000, Level.YELLOW), (1001, Level.RED)],
)
def test_s3_boundaries(make_project: MakeProject, n: int, level: Level) -> None:
    p = make_project({"AGENTS.md": "a" * n})
    s = s3_codex_budget(p, Config(codex_budget_bytes=1000))
    assert s.value == pytest.approx(100 * n / 1000)
    assert s.level is level
    assert s.unit == "%"
    assert s.action == "act.codex_budget"


def test_s3_detail(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "a" * 2048})
    s = s3_codex_budget(p, CFG)
    assert s.detail == "AGENTS.md chain 2.0 KiB of 32.0 KiB"


def test_s3_na_without_instruction_files(make_project: MakeProject) -> None:
    s = s3_codex_budget(make_project({"README.md": "x\n"}), CFG)
    assert s.value is None
    assert s.level is Level.NA


def test_s3_na_with_trivial_claude_md(make_project: MakeProject) -> None:
    s = s3_codex_budget(make_project({"CLAUDE.md": "# Title\n\n  \n"}), CFG)
    assert s.level is Level.NA


@pytest.mark.parametrize("rel", ["CLAUDE.md", ".claude/CLAUDE.md"])
def test_s3_yellow_when_codex_reads_nothing(make_project: MakeProject, rel: str) -> None:
    s = s3_codex_budget(make_project({rel: "# Rules\n- run the tests\n"}), CFG)
    assert s.value is None
    assert s.level is Level.YELLOW
    assert s.detail.startswith("codex_reads_nothing:")
    assert s.action == "act.codex_reads_nothing"


def _codex_cfg(n: int) -> dict[str, str]:
    return {".codex/config.toml": f"project_doc_max_bytes = {n}\n"}


def test_s3_uses_project_codex_config(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "a" * 2048, **_codex_cfg(65536), "declutter.toml": ""})
    s = s3_codex_budget(p, Config(codex_budget_bytes=65536))
    assert s.value == pytest.approx(100 * 2048 / 65536)
    assert s.detail == "AGENTS.md chain 2.0 KiB of 64.0 KiB (.codex/config.toml)"


def test_s3_project_codex_wins_and_warns_on_mismatch(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "a" * 40_000, **_codex_cfg(65536)})
    s = s3_codex_budget(p, CFG)  # codex_budget_bytes 32768 (default)
    assert s.value == pytest.approx(100 * 40_000 / 65536)
    assert s.level is Level.YELLOW
    assert "budget_mismatch" in s.detail and "32.0 KiB" in s.detail


# ---------------- S4 ----------------


def _headings(dated: int, plain: int) -> str:
    parts = [f"## 2026-10-{i + 1:02d} entry\nbody\n" for i in range(dated)]
    parts += [f"## Section {i}\nbody\n" for i in range(plain)]
    return "# Title\n" + "".join(parts)


@pytest.mark.parametrize(
    ("dated", "level"),
    [(1, Level.GREEN), (2, Level.YELLOW), (3, Level.RED)],
)
def test_s4_boundaries(make_project: MakeProject, dated: int, level: Level) -> None:
    # 5 headings: 20% is NOT above 20, 40% is NOT above 40, 60% is red.
    s = s4_changelog_ratio(make_project({"CLAUDE.md": _headings(dated, 5 - dated)}), CFG)
    assert s.value == pytest.approx(20.0 * dated)
    assert s.level is level
    assert s.detail == f"{dated} of 5 headings dated"
    assert s.action == "act.move_changelog"


def test_s4_exactly_at_red_threshold_is_yellow(make_project: MakeProject) -> None:
    s = s4_changelog_ratio(make_project({"CLAUDE.md": _headings(2, 3)}), CFG)
    assert s.value == 40.0
    assert s.level is Level.YELLOW


def test_s4_na_with_fewer_than_three_headings(make_project: MakeProject) -> None:
    s = s4_changelog_ratio(make_project({"CLAUDE.md": _headings(2, 0)}), CFG)
    assert s.value is None
    assert s.level is Level.NA
    assert s4_changelog_ratio(make_project(), CFG).level is Level.NA


def test_s4_only_level_two_headings(make_project: MakeProject) -> None:
    text = "# A 2026-10-01\n### B 2026-10-02\n## C\n## D\n## E\n```\n## 2026-10-03\n```\n"
    s = s4_changelog_ratio(make_project({"CLAUDE.md": text}), CFG)
    assert s.value == 0.0
    assert s.detail == "0 of 3 headings dated"


def test_s4_combines_both_files(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "## a\n## b\n", "AGENTS.md": "## 2026-01-01\n"})
    s = s4_changelog_ratio(p, CFG)
    assert s.detail == "1 of 3 headings dated"


@pytest.mark.parametrize(
    "heading",
    [
        "2026-11-04 cierre",
        "Cierre 4-11-2026",
        "04-11-2026",
        "07-oct",
        "3 nov",
        "12 de octubre",
        "5 de Marzo de 2026",
        "21-sep demo",
        "Oct 3 release" if False else "3 Oct release",
        "15 December",
        "Rama `feat-x`",
        "branch `main` merge",
    ],
)
def test_changelog_patterns_match(heading: str) -> None:
    assert _CHANGELOG_RE.search(heading)


@pytest.mark.parametrize(
    "heading",
    [
        "Arquitectura",
        "Reglas",
        "Versión 3",
        "Comandos de marzo",
        "3 marcos teóricos",
        "rama principal",
    ],
)
def test_changelog_patterns_do_not_match(heading: str) -> None:
    assert not _CHANGELOG_RE.search(heading)


def test_s4_spanish_formats_in_file(make_project: MakeProject) -> None:
    text = "## 07-oct fix\n## 3 nov cierre\n## 12 de octubre\n## Reglas\n## Rama `x`\n"
    s = s4_changelog_ratio(make_project({"CLAUDE.md": text}), CFG)
    assert s.detail == "4 of 5 headings dated"
    assert s.level is Level.RED


# ---------------- S5 ----------------


@pytest.mark.parametrize(
    ("n", "level"),
    [(150, Level.GREEN), (151, Level.YELLOW), (190, Level.YELLOW), (191, Level.RED)],
)
def test_s5_line_boundaries(make_project: MakeProject, n: int, level: Level) -> None:
    s = s5_memory(make_project(memory=_lines(n)), CFG)
    assert s.value == n
    assert s.level is level
    assert (s.yellow, s.red) == (150, 190)
    assert s.action == "act.memory_index"


@pytest.mark.parametrize(
    ("size", "level"),
    [(18_432, Level.GREEN), (18_433, Level.YELLOW), (24_576, Level.YELLOW), (24_577, Level.RED)],
)
def test_s5_byte_boundaries(make_project: MakeProject, size: int, level: Level) -> None:
    s = s5_memory(make_project(memory="a" * (size - 1) + "\n"), CFG)
    assert s.value == 1
    assert s.level is level


def test_s5_na_when_absent(make_project: MakeProject) -> None:
    s = s5_memory(make_project(), CFG)
    assert s.value is None
    assert s.level is Level.NA


def test_s5_detail_uses_full_file(make_project: MakeProject) -> None:
    s = s5_memory(make_project(memory="a" * 99 + "\n" + _lines(180)), CFG)
    assert s.value == 181
    assert s.detail.startswith("181 lines, ")
    assert s.detail.endswith(" KB")


# ---------------- Bloated case ----------------


def test_bloated_claude_md_is_red(make_project: MakeProject) -> None:
    body = "".join(
        f"## 2026-10-{(i % 28) + 1:02d} sesion {i}\n" + "detalle\n" * 13 for i in range(30)
    )
    text = body
    n_lines = len(text.splitlines())
    text += "x\n" * (450 - n_lines)
    p = make_project({"CLAUDE.md": text})
    s1 = s1_instruction_lines(p, CFG)
    s4 = s4_changelog_ratio(p, CFG)
    assert s1.value == 450
    assert s1.level is Level.RED
    assert s4.value == 100.0
    assert s4.level is Level.RED
    assert s4.detail == "30 of 30 headings dated"
