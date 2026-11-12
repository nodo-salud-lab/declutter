"""Tests for measure: signal aggregation, isolation, serialization and caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import MakeProject
from declutter_context import measure as mm
from declutter_context.model import Config, Level, Project, Signal


def test_all_signals_in_order(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "hello\n"})
    m = mm.measure(p, Config())
    assert [s.id for s in m.signals] == [f"S{i}" for i in range(1, 10)]
    assert len(mm.ALL_SIGNALS) == len(mm.SIGNAL_IDS) == 9


def test_exception_isolation(make_project: MakeProject, monkeypatch: pytest.MonkeyPatch) -> None:
    p = make_project({"CLAUDE.md": "x\n"})

    def boom(project: Project, cfg: Config) -> Signal:
        raise RuntimeError("secret detail")

    fns = list(mm.ALL_SIGNALS)
    fns[2] = boom
    monkeypatch.setattr(mm, "ALL_SIGNALS", tuple(fns))
    m = mm.measure(p, Config())
    assert len(m.signals) == 9
    s3 = m.signals[2]
    assert s3.id == "S3"
    assert s3.level is Level.NA
    assert s3.detail == "error: RuntimeError"
    assert m.signals[0].id == "S1"


def test_round_trip(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "a\n" * 50, "AGENTS.md": "b\n"}, memory="m\n", git=True)
    m = mm.measure(p, Config())
    d = mm.to_dict(m)
    m2 = mm.from_dict(d, p)
    assert m2 == m
    assert all(isinstance(s.level, Level) for s in m2.signals)


def test_round_trip_none_value_and_levels() -> None:
    p = Project.at(Path("/tmp"))
    sigs = tuple(
        Signal(f"S{i}", "n", None if i % 2 else 1.5, "count", lvl, 1.0, 2.0, "d", "act.x")
        for i, lvl in enumerate([Level.NA, Level.GREEN, Level.YELLOW, Level.RED], start=1)
    )
    from declutter_context.model import Measurement

    m = Measurement(project=p, signals=sigs)
    assert mm.from_dict(mm.to_dict(m), p) == m


def test_from_dict_malformed_raises() -> None:
    p = Project.at(Path("/tmp"))
    with pytest.raises(ValueError):
        mm.from_dict({"signals": "nope"}, p)


def test_measure_cached(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    p = make_project({"CLAUDE.md": "hello\n"})
    cache_home = tmp_path / "cachehome"
    calls = {"n": 0}

    def counting(fn: mm.SignalFn) -> mm.SignalFn:
        def wrapper(project: Project, cfg: Config) -> Signal:
            calls["n"] += 1
            return fn(project, cfg)

        return wrapper

    monkeypatch.setattr(mm, "ALL_SIGNALS", tuple(counting(f) for f in mm.ALL_SIGNALS))
    first = mm.measure_cached(p, Config(), home=cache_home)
    assert calls["n"] == 9
    second = mm.measure_cached(p, Config(), home=cache_home)
    assert calls["n"] == 9
    assert second == first


def test_measure_cached_invalidated_by_change(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    p = make_project({"CLAUDE.md": "hello\n"})
    cache_home = tmp_path / "cachehome"
    first = mm.measure_cached(p, Config(), home=cache_home)
    (p.root / "CLAUDE.md").write_text("hello\n" * 30, encoding="utf-8")
    second = mm.measure_cached(p, Config(), home=cache_home)
    assert second.signals[0].value != first.signals[0].value


def test_measure_cached_invalidated_by_config(
    make_project: MakeProject, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    p = make_project({"CLAUDE.md": "line\n" * 10})
    cache_home = tmp_path / "cachehome"
    first = mm.measure_cached(p, Config(), home=cache_home)
    strict = dict(Config().thresholds)
    strict["S1"] = (1.0, 2.0)
    second = mm.measure_cached(p, Config(thresholds=strict), home=cache_home)
    assert first.signals[0].level == Level.GREEN
    assert second.signals[0].level == Level.RED
    # lang does not take part in the digest, so a language change still hits the cache.
    assert mm._config_digest(Config(lang="es")) == mm._config_digest(Config())
