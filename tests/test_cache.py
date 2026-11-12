"""Tests for the fingerprint cache."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from declutter_context import cache
from declutter_context.loadset import claude_project_slug
from declutter_context.model import Project

MakeProject = Callable[..., Project]


@pytest.fixture(autouse=True)
def _no_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


def test_cache_dir_default_and_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert cache.cache_dir(tmp_path) == tmp_path / ".cache" / "declutter"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache.cache_dir(tmp_path) == tmp_path / "xdg" / "declutter"


def test_round_trip(tmp_path: Path) -> None:
    cache.put("k", "fp1", {"a": 1, "b": [1, 2]}, home=tmp_path, now=1000.0)
    assert cache.get("k", "fp1", home=tmp_path, now=1010.0) == {"a": 1, "b": [1, 2]}
    assert cache.get("k", "other", home=tmp_path, now=1010.0) is None
    assert cache.get("missing", "fp1", home=tmp_path, now=1010.0) is None
    mode = cache.cache_dir(tmp_path).stat().st_mode & 0o777
    assert mode == 0o700


def test_ttl_expiry(tmp_path: Path) -> None:
    cache.put("k", "fp", {"x": 1}, home=tmp_path, now=1000.0)
    assert cache.get("k", "fp", ttl_s=60, home=tmp_path, now=1060.0) == {"x": 1}
    assert cache.get("k", "fp", ttl_s=60, home=tmp_path, now=1060.5) is None


def test_corrupt_entries(tmp_path: Path) -> None:
    cache.put("k", "fp", {"x": 1}, home=tmp_path, now=1.0)
    path = cache._entry_path("k", tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert cache.get("k", "fp", home=tmp_path, now=2.0) is None
    path.write_text('["a list"]', encoding="utf-8")
    assert cache.get("k", "fp", home=tmp_path, now=2.0) is None
    path.write_text('{"fp": "fp", "ts": "x", "data": {}}', encoding="utf-8")
    assert cache.get("k", "fp", home=tmp_path, now=2.0) is None
    path.write_text('{"fp": "fp", "ts": 1, "data": 5}', encoding="utf-8")
    assert cache.get("k", "fp", home=tmp_path, now=2.0) is None


def test_put_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file, not dir", encoding="utf-8")
    cache.put("k", "fp", {"x": 1}, home=blocker)  # cache dir cannot be created
    cache.put("k", "fp", {"x": object()}, home=tmp_path)  # not JSON serializable
    assert cache.get("k", "fp", home=tmp_path) is None
    leftovers = (
        list(cache.cache_dir(tmp_path).glob(".tmp-*")) if cache.cache_dir(tmp_path).exists() else []
    )
    assert leftovers == []


def test_fingerprint_changes_with_claude_md(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": "one\n"})
    before = cache.fingerprint(project)
    assert before == cache.fingerprint(project)
    (project.root / "CLAUDE.md").write_text("one\ntwo more bytes\n", encoding="utf-8")
    assert cache.fingerprint(project) != before


def test_fingerprint_tracks_rules_memory_and_git(make_project: MakeProject) -> None:
    project = make_project({".claude/rules/a.md": "r\n"}, memory="- m\n", git=True)
    base = cache.fingerprint(project)
    (project.root / ".claude" / "rules" / "b.md").write_text("new\n", encoding="utf-8")
    after_rule = cache.fingerprint(project)
    assert after_rule != base
    mem = next((project.claude_home / "projects").rglob("MEMORY.md"))
    mem.write_text("- m\n- n\n", encoding="utf-8")
    after_mem = cache.fingerprint(project)
    assert after_mem != after_rule
    head = project.root / ".git" / "HEAD"
    st = head.stat()
    os.utime(head, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert cache.fingerprint(project) != after_mem


def test_fingerprint_ignores_transcripts(make_project: MakeProject) -> None:
    project = make_project({"CLAUDE.md": "x\n"})
    before = cache.fingerprint(project)
    slug = claude_project_slug(project.cwd)
    transcript = project.claude_home / "projects" / slug / "session.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text('{"a": 1}\n', encoding="utf-8")
    assert cache.fingerprint(project) == before
    transcript.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    assert cache.fingerprint(project) == before


def test_log_error_appends_line(tmp_path: Path) -> None:
    cache.log_error("boom\nsecond line", home=tmp_path)
    cache.log_error("again", home=tmp_path)
    lines = (cache.cache_dir(tmp_path) / "hook.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("boom second line")
    assert lines[1].endswith("again")


def test_log_rotation_keeps_newest_half(tmp_path: Path) -> None:
    msg = "x" * 200
    for i in range(1500):
        cache.log_error(f"{i:05d} {msg}", home=tmp_path)
    log = cache.cache_dir(tmp_path) / "hook.log"
    assert log.stat().st_size <= cache.LOG_MAX_BYTES
    text = log.read_text(encoding="utf-8")
    assert text.endswith(f"01499 {msg}\n")
    assert "00000 " not in text
    assert all(line.split(" ", 1)[1].strip() for line in text.splitlines())


def test_log_error_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    cache.log_error("nope", home=blocker)
