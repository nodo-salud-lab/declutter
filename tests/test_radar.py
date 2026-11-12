"""Tests for radar: registry, discovery and rows."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import radar, signals_files
from declutter_context.model import Config, Level, Measurement, Project, RegistryEntry


@pytest.fixture(autouse=True)
def _fake_measure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace the cached measurement (module built elsewhere) with S1 only."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    def fake(project: Project, cfg: Config, home: Path | None) -> Measurement:
        return Measurement(
            project=project, signals=(signals_files.s1_instruction_lines(project, cfg),)
        )

    monkeypatch.setattr(radar, "_measure", fake)


def test_registry_round_trip_tricky(tmp_path: Path) -> None:
    entries = [
        RegistryEntry(Path("/a/b c"), 'say "hi"', "sess\\one"),
        RegistryEntry(Path("/x/ñandú"), "demo ñandú ", None),
        RegistryEntry(Path("/w/back\\slash"), "tab\there\nnl\x01", "é"),
    ]
    reg = tmp_path / "sub" / "projects.toml"
    radar.save_registry(reg, entries)
    assert radar.load_registry(reg) == entries
    assert not list(reg.parent.glob(".tmp-*"))


def test_registry_missing_and_invalid(tmp_path: Path) -> None:
    assert radar.load_registry(tmp_path / "none.toml") == []
    bad = tmp_path / "bad.toml"
    bad.write_text("[[project\n", encoding="utf-8")
    assert radar.load_registry(bad) == []


def test_registry_defaults_and_skips(tmp_path: Path) -> None:
    reg = tmp_path / "p.toml"
    reg.write_text(
        '[[project]]\npath = "~/work/demo"\n\n[[project]]\nname = "nopath"\n\n'
        '[[project]]\npath = 5\n\n[[project]]\npath = "/abs/x"\nname = "X"\nsession = "s"\n',
        encoding="utf-8",
    )
    got = radar.load_registry(reg, home=tmp_path / "h")
    assert got == [
        RegistryEntry(tmp_path / "h" / "work" / "demo", "demo", None),
        RegistryEntry(Path("/abs/x"), "X", "s"),
    ]


def test_discover_rules(tmp_path: Path) -> None:
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "a" / "inner").mkdir()
    (tmp_path / "a" / "inner" / "CLAUDE.md").write_text("x", encoding="utf-8")
    (tmp_path / "grp" / "b").mkdir(parents=True)
    (tmp_path / "grp" / "b" / "AGENTS.md").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg" / ".git").mkdir(parents=True)
    (tmp_path / "plain").mkdir()
    (tmp_path / "d1" / "d2" / "d3" / "d4" / "deep").mkdir(parents=True)
    (tmp_path / "d1" / "d2" / "d3" / "d4" / "deep" / ".git").mkdir()
    (tmp_path / "d1" / "d2" / "ok").mkdir()
    (tmp_path / "d1" / "d2" / "ok" / ".git").mkdir()
    assert radar.discover(tmp_path) == sorted(
        [tmp_path / "a", tmp_path / "grp" / "b", tmp_path / "d1" / "d2" / "ok"]
    )
    assert radar.discover(tmp_path, max_depth=1) == [tmp_path / "a"]


def test_build_row_git_with_state(make_project: MakeProject) -> None:
    p = make_project(
        {
            "CLAUDE.md": "a\n" * 5,
            "ESTADO.md": "# P\nActualizado: 2026-11-01\n\n## Próximo (IA)\n- tarea uno\n"
            "\n## Humano\n- decidir\n",
        },
        git=True,
    )
    (p.root / "new.txt").write_text("x", encoding="utf-8")
    row = radar.build_row(RegistryEntry(p.root, "proj"), home=p.home, claude_home=p.claude_home)
    assert row.error is None
    assert row.level is Level.GREEN
    assert row.dirty == 1
    assert row.ahead is None
    assert row.estado is not None
    assert row.last_activity is not None


def test_build_row_non_git(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "a\n"})
    row = radar.build_row(RegistryEntry(p.root, "proj"), home=p.home, claude_home=p.claude_home)
    assert row.dirty is None and row.ahead is None and row.estado is None
    assert row.last_activity is not None


def test_build_row_missing_path(tmp_path: Path) -> None:
    row = radar.build_row(RegistryEntry(tmp_path / "ghost", "ghost"), home=tmp_path)
    assert row.level is Level.NA
    assert row.error


def test_build_row_exception_becomes_error(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project({"CLAUDE.md": "a\n"})

    def boom(project: Project, cfg: Config, home: Path | None) -> Measurement:
        raise RuntimeError("kaput")

    monkeypatch.setattr(radar, "_measure", boom)
    row = radar.build_row(RegistryEntry(p.root, "proj"), home=p.home)
    assert row.level is Level.NA
    assert row.error is not None and "kaput" in row.error


def test_build_rows_order_preserved(tmp_path: Path) -> None:
    entries = []
    for i in range(12):
        d = tmp_path / f"p{i:02d}"
        d.mkdir()
        (d / "CLAUDE.md").write_text("a\n" * (i + 1), encoding="utf-8")
        entries.append(RegistryEntry(d, f"n{i:02d}"))
    entries.insert(3, RegistryEntry(tmp_path / "missing", "missing"))
    rows = radar.build_rows(entries, home=tmp_path, max_workers=4)
    assert [r.entry.name for r in rows] == [e.name for e in entries]
    assert rows[3].error


def test_build_rows_empty() -> None:
    assert radar.build_rows([]) == []


def test_performance_15_projects(tmp_path: Path) -> None:
    entries = []
    for i in range(15):
        d = tmp_path / f"q{i}"
        d.mkdir()
        (d / "CLAUDE.md").write_text("x\n" * 10, encoding="utf-8")
        (d / "ESTADO.md").write_text("# T\nActualizado: 2026-11-01\n", encoding="utf-8")
        for cmd in (
            ["init", "-q", "-b", "main"],
            ["-c", "user.email=t@e.c", "-c", "user.name=t", "add", "-A"],
            ["-c", "user.email=t@e.c", "-c", "user.name=t", "commit", "-q", "-m", "i"],
        ):
            subprocess.run(["git", "-C", str(d), *cmd], check=True, capture_output=True)
        entries.append(RegistryEntry(d, d.name))
    t0 = time.perf_counter()
    rows = radar.build_rows(entries, home=tmp_path)
    elapsed = time.perf_counter() - t0
    assert all(r.error is None for r in rows)
    assert elapsed < 2.0, elapsed
