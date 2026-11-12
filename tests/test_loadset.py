"""Tests for loadset: Claude Code and Codex startup load sets (synthetic data only)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from pytest import MonkeyPatch

from conftest import MakeProject, write_tree
from declutter_context import fsutil, gitutil
from declutter_context.loadset import (
    claude_loadset,
    claude_project_slug,
    codex_loadset,
    memory_md_path,
    repo_root,
)
from declutter_context.model import Config, LoadSet, Project


def _kinds(ls: LoadSet) -> dict[str, str]:
    return {f.path.name if f.kind != "rule" else f"rule:{f.path.name}": f.kind for f in ls.files}


def _paths(ls: LoadSet) -> list[Path]:
    return [f.path for f in ls.files]


def test_memory_path_slug(make_project: MakeProject) -> None:
    p = make_project()
    assert memory_md_path(p) == (
        p.claude_home / "projects" / claude_project_slug(p.cwd) / "memory" / "MEMORY.md"
    )


def test_user_and_ancestor_claude_md(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "root\n", "sub/deep/CLAUDE.md": "deep\n"})
    (p.home / "CLAUDE.md").write_text("home\n")
    (p.claude_home / "CLAUDE.md").write_text("user\nline2\n")
    cwd = p.root / "sub" / "deep"
    p2 = Project(root=p.root, cwd=cwd, home=p.home, claude_home=p.claude_home)
    ls = claude_loadset(p2, Config())
    assert ls.agent == "claude"
    assert _paths(ls) == [
        p.claude_home / "CLAUDE.md",
        cwd / "CLAUDE.md",
        p.root / "CLAUDE.md",
        p.home / "CLAUDE.md",
    ]
    assert ls.files[0].kind == "user_claude_md"
    assert ls.files[0].lines == 2
    assert all(f.kind == "claude_md" for f in ls.files[1:])
    assert ls.requested_bytes == ls.total_bytes


def test_walk_stops_at_home(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "x\n"})
    (p.home.parent / "CLAUDE.md").write_text("above home\n")
    ls = claude_loadset(p, Config())
    assert p.home.parent / "CLAUDE.md" not in _paths(ls)


def test_local_md(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "a\n", "CLAUDE.local.md": "b\n"})
    ls = claude_loadset(p, Config())
    assert _kinds(ls) == {"CLAUDE.md": "claude_md", "CLAUDE.local.md": "claude_local_md"}


def test_rules_with_and_without_paths(make_project: MakeProject) -> None:
    p = make_project(
        {
            ".claude/rules/always.md": "# always\n",
            ".claude/rules/nested/also.md": "---\ndescription: x\n---\nbody\n",
            ".claude/rules/scoped.md": "---\npaths:\n  - src/**\n---\nbody\n",
            ".claude/rules/notmd.txt": "ignored\n",
        }
    )
    write_tree(p.claude_home, {"rules/user.md": "user rule\n"})
    ls = claude_loadset(p, Config())
    names = {f.path.name for f in ls.files if f.kind == "rule"}
    assert names == {"always.md", "also.md", "user.md"}


def test_memory_truncated_by_lines(make_project: MakeProject) -> None:
    text = "".join(f"l{i}\n" for i in range(50))
    p = make_project(memory=text)
    ls = claude_loadset(p, Config(memory_max_lines=10))
    (mem,) = [f for f in ls.files if f.kind == "memory"]
    assert mem.truncated and mem.lines == 10
    assert ls.requested_bytes == len(text.encode())
    assert mem.bytes < ls.requested_bytes


def test_memory_truncated_by_bytes(make_project: MakeProject) -> None:
    text = "x" * 1000 + "\n" + "y" * 1000 + "\n"
    p = make_project(memory=text)
    ls = claude_loadset(p, Config(memory_max_bytes=1500))
    (mem,) = [f for f in ls.files if f.kind == "memory"]
    assert mem.truncated and mem.bytes == 1500
    assert ls.requested_bytes == len(text.encode())


def test_memory_not_truncated(make_project: MakeProject) -> None:
    p = make_project(memory="a\nb\n")
    (mem,) = claude_loadset(p, Config()).files
    assert not mem.truncated and mem.lines == 2


def test_memory_imports_not_followed(make_project: MakeProject) -> None:
    p = make_project({"other.md": "o\n"}, memory="see @other.md\n")
    ls = claude_loadset(p, Config())
    assert [f.kind for f in ls.files] == ["memory"]


def test_imports_relative_and_home(make_project: MakeProject) -> None:
    p = make_project(
        {
            "CLAUDE.md": "See @docs/a.md and @~/shared.md and @../outside.md.\n",
            "docs/a.md": "A\n",
            "missing.md": "m\n",
        }
    )
    (p.home / "shared.md").write_text("S\n")
    (p.home / "outside.md").write_text("O\n")
    ls = claude_loadset(p, Config())
    imports = {f.path for f in ls.files if f.kind == "import"}
    assert imports == {p.root / "docs" / "a.md", p.home / "shared.md", p.home / "outside.md"}


def test_import_relative_to_containing_file(make_project: MakeProject) -> None:
    p = make_project(
        {"CLAUDE.md": "@docs/a.md\n", "docs/a.md": "@./b.md\n", "docs/b.md": "B\n", "b.md": "no\n"}
    )
    ls = claude_loadset(p, Config())
    assert p.root / "docs" / "b.md" in _paths(ls)
    assert p.root / "b.md" not in _paths(ls)


def test_import_depth_limit(make_project: MakeProject) -> None:
    p = make_project(
        {
            "CLAUDE.md": "@a.md\n",
            "a.md": "@b.md\n",
            "b.md": "@c.md\n",
            "c.md": "@d.md\n",
            "d.md": "end\n",
        }
    )
    ls = claude_loadset(p, Config(import_max_depth=2))
    assert {f.path.name for f in ls.files if f.kind == "import"} == {"a.md", "b.md"}
    ls4 = claude_loadset(p, Config(import_max_depth=4))
    assert {f.path.name for f in ls4.files if f.kind == "import"} == {
        "a.md",
        "b.md",
        "c.md",
        "d.md",
    }


def test_import_cycle(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "@a.md\n", "a.md": "@b.md\n", "b.md": "@a.md @CLAUDE.md\n"})
    ls = claude_loadset(p, Config())
    names = sorted(f.path.name for f in ls.files)
    assert names == ["CLAUDE.md", "a.md", "b.md"]


def test_import_in_code_ignored(make_project: MakeProject) -> None:
    p = make_project(
        {
            "CLAUDE.md": "```\n@fenced.md\n```\nInline `@inline.md` here.\nmail me@mail.com\n",
            "fenced.md": "f\n",
            "inline.md": "i\n",
            "mail.com": "m\n",
        }
    )
    ls = claude_loadset(p, Config())
    assert [f.kind for f in ls.files] == ["claude_md"]


def test_import_after_fence_is_found(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "```\n@x.md\n```\nreal @y.md\n", "x.md": "x\n", "y.md": "y\n"})
    ls = claude_loadset(p, Config())
    assert {f.path.name for f in ls.files if f.kind == "import"} == {"y.md"}


def test_secret_file_skipped(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "@.env and @cert.pem\n", ".env": "K=1\n", "cert.pem": "p\n"})
    ls = claude_loadset(p, Config())
    assert [f.kind for f in ls.files] == ["claude_md"]


def test_symlink_escape_skipped(make_project: MakeProject, tmp_path: Path) -> None:
    p = make_project({"CLAUDE.md": "@link.md\n"})
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.md").write_text("secret stuff\n")
    (p.root / "link.md").symlink_to(outside / "x.md")
    ls = claude_loadset(p, Config())
    assert [f.kind for f in ls.files] == ["claude_md"]


def _git_project(make_project: MakeProject, files: dict[str, str]) -> Project:
    p = make_project(files, git=True)
    return p


def test_codex_chain_with_override(make_project: MakeProject) -> None:
    p = _git_project(
        make_project,
        {
            "AGENTS.md": "root\n",
            "pkg/AGENTS.md": "pkg\n",
            "pkg/AGENTS.override.md": "pkg override\n",
            "pkg/sub/AGENTS.md": "",
            "pkg/sub/leaf/AGENTS.md": "leaf\n",
        },
    )
    cwd = p.root / "pkg" / "sub" / "leaf"
    p2 = Project(root=p.root, cwd=cwd, home=p.home, claude_home=p.claude_home)
    assert repo_root(p2) == p.root
    ls = codex_loadset(p2, Config())
    assert ls.agent == "codex"
    assert _paths(ls) == [
        p.root / "AGENTS.md",
        p.root / "pkg" / "AGENTS.override.md",
        cwd / "AGENTS.md",
    ]
    assert all(f.kind == "agents_md" for f in ls.files)


def test_codex_shared_budget(make_project: MakeProject) -> None:
    p = _git_project(
        make_project,
        {"AGENTS.md": "a" * 60, "pkg/AGENTS.md": "b" * 60, "pkg/x/AGENTS.md": "c" * 60},
    )
    cwd = p.root / "pkg" / "x"
    p2 = Project(root=p.root, cwd=cwd, home=p.home, claude_home=p.claude_home)
    ls = codex_loadset(p2, Config(codex_budget_bytes=100))
    assert [f.bytes for f in ls.files] == [60, 40]
    assert [f.truncated for f in ls.files] == [False, True]
    assert ls.requested_bytes == 180
    assert ls.total_bytes == 100


def test_codex_non_git(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "hello\n", "sub/AGENTS.md": "x\n"})
    assert repo_root(p) == p.root
    ls = codex_loadset(p, Config())
    assert _paths(ls) == [p.root / "AGENTS.md"]
    assert ls.requested_bytes == 6


# --- injectable reader (phase 2a, spec §3, §10.2.3) ---


def _map_reader(project: Project, mapping: dict[str, str]) -> Callable[[Path], str | None]:
    """Reader that serves ``mapping`` (root-relative POSIX keys) first, then the disk."""

    def read(path: Path) -> str | None:
        try:
            rel = Path(os.path.normpath(path)).relative_to(project.root).as_posix()
        except ValueError:
            rel = None
        if rel is not None and rel in mapping:
            return mapping[rel]
        if not fsutil.is_file(path):
            return None
        return fsutil.safe_read_text(path, project.root, project.home, project.claude_home)

    return read


def test_codex_loadset_reader_overrides_disk(make_project: MakeProject) -> None:
    p = make_project({})
    assert not (p.root / "AGENTS.md").exists()
    reader = _map_reader(p, {"AGENTS.md": "x" * 100})
    ls = codex_loadset(p, Config(), reader=reader, top=p.root)
    assert ls.requested_bytes == 100
    assert _paths(ls) == [p.root / "AGENTS.md"]
    assert not (p.root / "AGENTS.md").exists()


def test_codex_loadset_reader_hides_disk_file(make_project: MakeProject) -> None:
    p = make_project({"AGENTS.md": "on disk\n"})
    ls = codex_loadset(p, Config(), reader=lambda _path: None, top=p.root)
    assert ls.files == ()
    assert ls.requested_bytes == 0


def test_codex_loadset_top_skips_git(make_project: MakeProject, monkeypatch: MonkeyPatch) -> None:
    p = make_project({"AGENTS.md": "root\n", "pkg/AGENTS.md": "pkg\n"})

    def boom(*_args: object, **_kwargs: object) -> str | None:
        raise AssertionError("git must not be consulted when top is given")

    monkeypatch.setattr(gitutil, "run_git", boom)
    p2 = Project(root=p.root, cwd=p.root / "pkg", home=p.home, claude_home=p.claude_home)
    ls = codex_loadset(p2, Config(), top=p.root)
    assert _paths(ls) == [p.root / "AGENTS.md", p.root / "pkg" / "AGENTS.md"]


def test_codex_loadset_reader_budget_truncation(make_project: MakeProject) -> None:
    p = make_project({})
    reader = _map_reader(p, {"AGENTS.md": "a" * 60, "pkg/AGENTS.md": "b" * 60})
    p2 = Project(root=p.root, cwd=p.root / "pkg", home=p.home, claude_home=p.claude_home)
    ls = codex_loadset(p2, Config(codex_budget_bytes=100), reader=reader, top=p.root)
    assert [f.bytes for f in ls.files] == [60, 40]
    assert ls.requested_bytes == 120


def test_claude_loadset_reader_follows_imports_from_map(make_project: MakeProject) -> None:
    p = make_project({"docs/reglas.md": "rule\n"})
    reader = _map_reader(p, {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "agents\n@docs/reglas.md\n"})
    ls = claude_loadset(p, Config(), reader=reader)
    assert _paths(ls) == [
        p.root / "CLAUDE.md",
        p.root / "AGENTS.md",
        p.root / "docs" / "reglas.md",
    ]
    assert [f.kind for f in ls.files] == ["claude_md", "import", "import"]
    assert ls.requested_bytes == len("@AGENTS.md\n") + len("agents\n@docs/reglas.md\n") + 5
    assert not (p.root / "CLAUDE.md").exists()


def test_claude_loadset_reader_hides_disk_claude_md(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "a\n"})
    ls = claude_loadset(p, Config(), reader=lambda _path: None)
    assert ls.files == ()
    assert ls.requested_bytes == 0


def test_claude_loadset_reader_reads_rules_and_memory(make_project: MakeProject) -> None:
    p = make_project({".claude/rules/a.md": "disk rule\n"}, memory="disk memory\n")
    mem = memory_md_path(p)
    rule = p.root / ".claude" / "rules" / "a.md"
    served = {rule: "mapped rule\n", mem: "mapped memory\n"}
    ls = claude_loadset(p, Config(), reader=served.get)
    assert {f.path: f.bytes for f in ls.files} == {rule: 12, mem: 14}
    assert ls.requested_bytes == 26


def test_loadsets_without_reader_unchanged(make_project: MakeProject) -> None:
    p = make_project({"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "hello\n"})
    assert claude_loadset(p, Config()) == claude_loadset(p, Config(), reader=None)
    assert codex_loadset(p, Config()) == codex_loadset(p, Config(), reader=None)
