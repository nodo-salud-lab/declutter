"""Shared fixtures: build synthetic projects in a temp dir. No real data is ever used."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from declutter_context.loadset import claude_project_slug
from declutter_context.model import Project

MakeProject = Callable[..., Project]


def write_tree(base: Path, files: Mapping[str, str]) -> None:
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


@pytest.fixture
def make_project(tmp_path: Path) -> MakeProject:
    """make_project(files={...}, memory=None, git=False) -> Project

    Layout: tmp/home (fake $HOME), tmp/home/.claude (fake claude home), tmp/home/proj (root).
    ``memory`` (str) is written to the project's auto-memory MEMORY.md.
    """

    def _make(
        files: Mapping[str, str] | None = None, *, memory: str | None = None, git: bool = False
    ) -> Project:
        home = tmp_path / "home"
        root = home / "proj"
        root.mkdir(parents=True, exist_ok=True)
        (home / ".claude").mkdir(exist_ok=True)
        write_tree(root, files or {})
        project = Project.at(root, home=home, claude_home=home / ".claude")
        if memory is not None:
            slug = claude_project_slug(project.cwd)
            mem = project.claude_home / "projects" / slug / "memory" / "MEMORY.md"
            mem.parent.mkdir(parents=True, exist_ok=True)
            mem.write_text(memory, encoding="utf-8")
        if git:
            for cmd in (
                ["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "t"],
                ["add", "-A"],
                ["commit", "-q", "--allow-empty", "-m", "init"],
            ):
                subprocess.run(["git", "-C", str(root), *cmd], check=True, capture_output=True)
        return project

    return _make
