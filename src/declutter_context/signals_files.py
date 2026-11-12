"""File-based signals S1-S5: instruction size, startup tokens, Codex budget, changelog, memory."""

from __future__ import annotations

import math
import re
from pathlib import Path

from . import codexcfg, fsutil, loadset
from .model import DEFAULT_THRESHOLDS, Config, Level, Project, Signal, level_for
from .reorg import mdblocks

_MONTHS = (
    # Spanish then English; full names first so the alternation prefers them.
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre"
    "|diciembre|january|february|march|april|june|july|august|september|october|november"
    "|december|ene|feb|mar|abr|may|jun|jul|ago|sept|sep|set|oct|nov|dic|dec|jan|apr|aug"
)
_CHANGELOG_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}-\d{1,2}-\d{4}"
    rf"|(?<!\d)\d{{1,2}}(?:\s+de)?[\s-]+(?:{_MONTHS})\b"
    r"|\b(?:rama|branch)\s+`",
    re.IGNORECASE,
)
_H2_RE = mdblocks.H2_RE  # re-exported; the parser lives in reorg.mdblocks (spec §4.7)
_MIN_HEADINGS = 3


def _thr(cfg: Config, key: str) -> tuple[float, float]:
    """Return (yellow, red) for ``key``, falling back to the built-in defaults."""
    yellow, red = cfg.thresholds.get(key, DEFAULT_THRESHOLDS[key])
    return float(yellow), float(red)


def _read_root(project: Project, path: Path) -> str | None:
    """Read an instruction file confined to the project root."""
    return fsutil.safe_read_text(path, project.root)


def _root_line_count(project: Project, path: Path) -> int | None:
    """Line count of a root instruction file; huge files are counted by streaming."""
    text = _read_root(project, path)
    if text is None:
        return None
    if len(text.encode("utf-8")) < fsutil.MAX_READ_BYTES:
        return len(text.splitlines())
    stats = fsutil.safe_text_stats(path, project.root)
    return None if stats is None else stats.lines


def _label(project: Project, path: Path, kind: str) -> str:
    """Short, unambiguous label for a loaded file: project-relative or ``~``-relative."""
    if kind == "memory":
        return "MEMORY.md (auto-memory)"
    text = str(path)
    for base, prefix in (
        (project.root, ""),
        (project.claude_home, "~/.claude/"),
        (project.home, "~/"),
    ):
        try:
            text = prefix + path.relative_to(base).as_posix()
            break
        except ValueError:
            continue
    return f"import:{text}" if kind == "import" else text


def _kib(n: float) -> str:
    return f"{n / 1024:.1f}"


def s1_instruction_lines(project: Project, cfg: Config) -> Signal:
    """S1: line count of root CLAUDE.md plus AGENTS.md."""
    yellow, red = _thr(cfg, "S1")
    counts: dict[str, int | None] = {}
    for name in ("CLAUDE.md", "AGENTS.md"):
        counts[name] = _root_line_count(project, project.root / name)
    if all(v is None for v in counts.values()):
        return Signal(
            id="S1",
            name="Root instruction lines",
            unit="lines",
            value=None,
            level=Level.NA,
            yellow=yellow,
            red=red,
            detail="no CLAUDE.md or AGENTS.md",
            action="act.split_claude_md",
        )
    total = sum(v or 0 for v in counts.values())
    detail = f"CLAUDE.md {counts['CLAUDE.md'] or 0} + AGENTS.md {counts['AGENTS.md'] or 0} lines"
    return Signal(
        id="S1",
        name="Root instruction lines",
        unit="lines",
        value=float(total),
        level=level_for(total, yellow, red),
        yellow=yellow,
        red=red,
        detail=detail,
        action="act.split_claude_md",
    )


def s2_startup_tokens(project: Project, cfg: Config) -> Signal:
    """S2: estimated tokens Claude Code loads at startup."""
    yellow, red = _thr(cfg, "S2")
    ls = loadset.claude_loadset(project, cfg)
    tokens = math.ceil(ls.total_bytes / cfg.bytes_per_token)
    top = sorted(ls.files, key=lambda f: f.bytes, reverse=True)[:3]
    detail = ", ".join(
        f"{_label(project, f.path, f.kind)} ~{math.ceil(f.bytes / cfg.bytes_per_token)} tok"
        for f in top
    )
    return Signal(
        id="S2",
        name="Startup tokens (Claude)",
        value=float(tokens),
        unit="tokens",
        level=level_for(tokens, yellow, red),
        yellow=yellow,
        red=red,
        detail=detail,
        action="act.trim_startup",
    )


_CLAUDE_FILES = ("CLAUDE.md", ".claude/CLAUDE.md")


def _has_instructions(text: str) -> bool:
    """True when ``text`` has at least one non-blank line that is not a Markdown heading."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines())


def _claude_has_instructions(project: Project) -> bool:
    for rel in _CLAUDE_FILES:
        text = _read_root(project, project.root / rel)
        if text is not None and _has_instructions(text):
            return True
    return False


def codex_budget(project: Project, cfg: Config) -> tuple[int, str]:
    """Budget S3 measures against and a suffix for the detail line.

    A project ``.codex/config.toml`` with ``project_doc_max_bytes`` wins, because that is
    what Codex applies (in a trusted project); otherwise ``codex_budget_bytes``. When both
    are set and differ, the suffix carries a ``budget_mismatch`` note with both values."""
    project_value = codexcfg.project_doc_max_bytes(project.root)
    if project_value is None:
        return cfg.codex_budget_bytes, ""
    suffix = f" ({codexcfg.CODEX_CONFIG})"
    if project_value != cfg.codex_budget_bytes:
        suffix += (
            f"; budget_mismatch: codex_budget_bytes is {_kib(cfg.codex_budget_bytes)} KiB, "
            f"run declutter reorg codex-budget --bytes {project_value}"
        )
    return project_value, suffix


def s3_codex_budget(project: Project, cfg: Config) -> Signal:
    """S3: percent of the Codex project-doc byte budget used by the AGENTS.md chain.

    Without AGENTS.md in the chain, S3 is yellow when a root ``CLAUDE.md`` (or
    ``.claude/CLAUDE.md``) has instructions, since Codex does not read it, and n/a
    otherwise (Enmienda 05-11)."""
    yellow, red = _thr(cfg, "S3")
    ls = loadset.codex_loadset(project, cfg)
    if not ls.files:
        if _claude_has_instructions(project):
            return Signal(
                id="S3",
                name="Codex budget",
                value=None,
                unit="%",
                level=Level.YELLOW,
                yellow=yellow,
                red=red,
                detail="codex_reads_nothing: CLAUDE.md has instructions, no AGENTS.md in chain",
                action="act.codex_reads_nothing",
            )
        return Signal(
            id="S3",
            name="Codex budget",
            value=None,
            unit="%",
            level=Level.NA,
            yellow=yellow,
            red=red,
            detail="no AGENTS.md in chain",
            action="act.codex_budget",
        )
    budget, suffix = codex_budget(project, cfg)
    pct = 100 * ls.requested_bytes / budget
    detail = f"AGENTS.md chain {_kib(ls.requested_bytes)} KiB of {_kib(budget)} KiB{suffix}"
    return Signal(
        id="S3",
        name="Codex budget",
        value=pct,
        unit="%",
        level=level_for(pct, yellow, red),
        yellow=yellow,
        red=red,
        detail=detail,
        action="act.codex_budget",
    )


def _level2_headings(text: str) -> list[str]:
    """Return level-2 ("## ") heading texts outside fences, comments and front matter."""
    return mdblocks.level2_headings(text)


def s4_changelog_ratio(project: Project, cfg: Config) -> Signal:
    """S4: percent of level-2 headings that look like changelog entries."""
    yellow, red = _thr(cfg, "S4")
    headings: list[str] = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = _read_root(project, project.root / name)
        if text is not None:
            headings.extend(_level2_headings(text))
    total = len(headings)
    if total < _MIN_HEADINGS:
        return Signal(
            id="S4",
            name="Changelog ratio",
            value=None,
            unit="%",
            level=Level.NA,
            yellow=yellow,
            red=red,
            detail=f"{total} level-2 headings (need {_MIN_HEADINGS})",
            action="act.move_changelog",
        )
    dated = sum(1 for h in headings if _CHANGELOG_RE.search(h))
    pct = 100 * dated / total
    return Signal(
        id="S4",
        name="Changelog ratio",
        value=pct,
        unit="%",
        level=level_for(pct, yellow, red),
        yellow=yellow,
        red=red,
        detail=f"{dated} of {total} headings dated",
        action="act.move_changelog",
    )


def s5_memory(project: Project, cfg: Config) -> Signal:
    """S5: size of the full auto-memory MEMORY.md (lines and bytes, worst wins)."""
    y_lines, r_lines = _thr(cfg, "S5_lines")
    y_bytes, r_bytes = _thr(cfg, "S5_bytes")
    path = loadset.memory_md_path(project)
    stats = fsutil.safe_text_stats(path, *loadset.allowed_roots(path, project))
    if stats is None:
        return Signal(
            id="S5",
            name="Memory cap",
            value=None,
            unit="lines",
            level=Level.NA,
            yellow=y_lines,
            red=r_lines,
            detail="no MEMORY.md",
            action="act.memory_index",
        )
    lines = stats.lines
    size = stats.bytes
    level = max(level_for(lines, y_lines, r_lines), level_for(size, y_bytes, r_bytes))
    return Signal(
        id="S5",
        name="Memory cap",
        value=float(lines),
        unit="lines",
        level=level,
        yellow=y_lines,
        red=r_lines,
        detail=f"{lines} lines, {size / 1024:.1f} KB",
        action="act.memory_index",
    )
