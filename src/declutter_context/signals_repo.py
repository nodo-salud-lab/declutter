"""Repository/session signals S6-S9: compactions, state freshness, branches, status headers."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from declutter_context import gitutil, loadset
from declutter_context.estado import load_estado
from declutter_context.fsutil import iter_files, safe_read_text
from declutter_context.model import DEFAULT_THRESHOLDS, Config, Level, Project, Signal, level_for

_COMPACT_MARKER = b"compact_boundary"
_HEADER_LINES = 15
# Handoff/plan docs: the word must stand alone in the file name (plural allowed), so
# "plan-v2.md" or "2026-10-01_traspaso.md" match but "explanation.md" or "planilla.md" do not.
_DOC_NAME_RE = re.compile(r"(?:^|[-_.\s])(?:plan|traspaso|handoff)(?:e?s)?(?=[-_.\s]|$)")
_STATUS_RE = re.compile(r"^[\s\-*]*(estado|status)\*{0,2}\s*:", re.IGNORECASE)


def _today() -> date:
    """Current local date. Isolated so tests can monkeypatch it."""
    return date.today()


def _thresholds(cfg: Config, key: str) -> tuple[float, float]:
    """Return the (yellow, red) pair for ``key``, falling back to the defaults."""
    pair = cfg.thresholds.get(key)
    if pair is None:
        pair = DEFAULT_THRESHOLDS[key]
    return float(pair[0]), float(pair[1])


def _transcript_dir(project: Project) -> Path:
    return loadset.claude_project_dir(project)


def is_handoff_doc(name: str) -> bool:
    """True if a markdown file name looks like a handoff or plan document."""
    return _DOC_NAME_RE.search(name.lower()) is not None


def _newest_transcript(directory: Path) -> Path | None:
    best: Path | None = None
    best_mtime = -1.0
    try:
        entries = list(directory.glob("*.jsonl"))
    except OSError:
        return None
    for p in entries:
        try:
            if not p.is_file():
                continue
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best_mtime:
            best, best_mtime = p, m
    return best


def _count_marker_lines(path: Path) -> int | None:
    """Count lines containing the compaction marker, streaming the file in binary."""
    count = 0
    try:
        with path.open("rb") as fh:
            for line in fh:
                if _COMPACT_MARKER in line:
                    count += 1
    except OSError:
        return None
    return count


def s6_compactions(project: Project, cfg: Config) -> Signal:
    """S6: number of ``compact_boundary`` lines in the newest session transcript."""
    yellow, red = _thresholds(cfg, "S6")
    name = "Compactions last session"

    def na() -> Signal:
        return Signal("S6", name, None, "count", Level.NA, yellow, red, "no session transcript", "")

    newest = _newest_transcript(_transcript_dir(project))
    if newest is None:
        return na()
    count = _count_marker_lines(newest)
    if count is None:
        return na()
    try:
        size_mb = newest.stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0.0
    return Signal(
        "S6",
        name,
        float(count),
        "count",
        level_for(float(count), yellow, red),
        yellow,
        red,
        f"{count} compactions in {newest.name} ({size_mb:.1f} MB)",
        "act.new_session",
    )


def s7_state_freshness(project: Project, cfg: Config) -> Signal:
    """S7: days since the state file's Updated date; a missing file or date is YELLOW.

    YELLOW (not RED) so that "no state file yet" never hides real problems in the radar.
    """
    yellow, red = _thresholds(cfg, "S7")
    name = "State freshness"
    estado = load_estado(project.root)
    if estado is None:
        return Signal(
            "S7",
            name,
            None,
            "days",
            Level.YELLOW,
            yellow,
            red,
            "no ESTADO.md/STATE.md",
            "act.update_state",
        )
    if estado.updated is None:
        return Signal(
            "S7",
            name,
            None,
            "days",
            Level.YELLOW,
            yellow,
            red,
            "no valid Updated/Actualizado date",
            "act.update_state",
        )
    days = max(0, (_today() - estado.updated).days)
    return Signal(
        "S7",
        name,
        float(days),
        "days",
        level_for(float(days), yellow, red),
        yellow,
        red,
        f"{estado.path.name} updated {estado.updated.isoformat()} ({days} days ago)",
        "act.update_state",
    )


def s8_branches_worktrees(project: Project, cfg: Config) -> Signal:
    """S8: merged local branches and git worktrees; the worse of the two decides."""
    yb, rb = _thresholds(cfg, "S8_branches")
    yw, rw = _thresholds(cfg, "S8_worktrees")
    name = "Branches & worktrees"
    root = project.root
    merged = gitutil.merged_branch_count(root) if gitutil.is_repo(root) else None
    trees = gitutil.worktree_count(root) if merged is not None else None
    if merged is None or trees is None:
        return Signal("S8", name, None, "count", Level.NA, yb, rb, "not a git repository", "")
    lb = level_for(float(merged), yb, rb)
    lw = level_for(float(trees), yw, rw)
    # Report the thresholds of the component that drives the verdict (branches on ties).
    yellow, red = (yw, rw) if lw > lb else (yb, rb)
    return Signal(
        "S8",
        name,
        float(merged),
        "count",
        max(lb, lw),
        yellow,
        red,
        f"{merged} merged branches, {trees} worktrees",
        "act.prune_branches",
    )


def _has_status_header(text: str) -> bool:
    return any(_STATUS_RE.match(line) for line in text.splitlines()[:_HEADER_LINES])


def s9_docs_without_status(project: Project, cfg: Config) -> Signal:
    """S9: handoff/plan markdown docs with no ``estado:``/``status:`` line in the header."""
    yellow, red = _thresholds(cfg, "S9")
    name = "Docs without status"
    total = 0
    missing = 0
    for path in iter_files(project.root, "*.md"):
        if not is_handoff_doc(path.name):
            continue
        text = safe_read_text(path, project.root, max_bytes=64 * 1024)
        if text is None:
            continue
        total += 1
        if not _has_status_header(text):
            missing += 1
    return Signal(
        "S9",
        name,
        float(missing),
        "count",
        level_for(float(missing), yellow, red),
        yellow,
        red,
        f"{missing} of {total} handoff/plan docs lack a status header",
        "act.mark_docs",
    )
