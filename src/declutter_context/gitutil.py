"""Minimal, read-only git helpers. All return None when git or the repo is unavailable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_TIMEOUT_S = 5


def run_git(root: Path, *args: str) -> str | None:
    """Run ``git -C root <args>`` and return stdout, or None on any failure."""
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_repo(root: Path) -> bool:
    """True if ``root`` is inside a git work tree."""
    out = run_git(root, "rev-parse", "--is-inside-work-tree")
    return out is not None and out.strip() == "true"


def main_branch(root: Path) -> str | None:
    """Best guess at the main branch: origin/HEAD target, else 'main', else 'master'."""
    out = run_git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if out and out.strip():
        return out.strip().split("/", 1)[-1]
    for name in ("main", "master"):
        if run_git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}") is not None:
            return name
    return None


def dirty_count(root: Path) -> int | None:
    """Number of changed or untracked paths (``git status --porcelain``)."""
    out = run_git(root, "status", "--porcelain")
    return None if out is None else len([ln for ln in out.splitlines() if ln.strip()])


def ahead_count(root: Path) -> int | None:
    """Commits on HEAD not yet on its upstream branch."""
    out = run_git(root, "rev-list", "--count", "@{upstream}..HEAD")
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def last_commit_ts(root: Path) -> int | None:
    """Committer timestamp of HEAD."""
    out = run_git(root, "log", "-1", "--format=%ct")
    if not out or not out.strip():
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def merged_branch_count(root: Path) -> int | None:
    """Local branches already merged into the main branch (excluding it)."""
    base = main_branch(root)
    if base is None:
        return None
    out = run_git(root, "branch", "--merged", base, "--format=%(refname:short)")
    if out is None:
        return None
    return len([b for b in out.splitlines() if b.strip() and b.strip() != base])


def worktree_count(root: Path) -> int | None:
    """Number of git worktrees, including the main one."""
    out = run_git(root, "worktree", "list", "--porcelain")
    if out is None:
        return None
    return len([ln for ln in out.splitlines() if ln.startswith("worktree ")])


def latest_ref_ts(root: Path) -> int | None:
    """Newest committer timestamp across HEAD and every local branch, or None.

    Unlike ``last_commit_ts`` this also sees work on other branches (and therefore on
    other worktrees, which always have a branch or a detached HEAD of their own).
    """
    best = last_commit_ts(root)
    out = run_git(
        root,
        "for-each-ref",
        "--sort=-committerdate",
        "--count=1",
        "--format=%(committerdate:unix)",
        "refs/heads",
    )
    if out and out.strip():
        try:
            ts = int(out.strip().splitlines()[0])
        except ValueError:
            ts = None
        if ts is not None and (best is None or ts > best):
            best = ts
    return best
