"""Shared data types. This module is the contract every other module builds on.

All types are immutable dataclasses. Nothing here does I/O.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum
from pathlib import Path


class Level(IntEnum):
    """Traffic-light level. Ordered so that ``max()`` returns the worst level.

    ``NA`` (not applicable) is the lowest so it never dominates a real result.
    """

    NA = -1
    GREEN = 0
    YELLOW = 1
    RED = 2


# Default thresholds: signal key -> (yellow, red). "Higher is worse" for every key.
# A value strictly greater than the yellow threshold is YELLOW; strictly greater than red is RED.
DEFAULT_THRESHOLDS: Mapping[str, tuple[float, float]] = {
    "S1": (200, 400),  # root instruction lines (CLAUDE.md + AGENTS.md)
    "S2": (8_000, 20_000),  # estimated tokens loaded at Claude Code startup
    "S3": (60, 100),  # % of Codex project-doc budget used
    "S4": (20, 40),  # % of "##" headings that look like a changelog entry
    "S5_lines": (150, 190),  # MEMORY.md lines (Claude loads the first 200)
    "S5_bytes": (18_432, 24_576),  # MEMORY.md bytes (Claude loads the first 25 KB)
    "S6": (5, 10),  # compactions in the most recent session transcript
    "S7": (7, 21),  # days since the state file was updated
    "S8_branches": (50, 150),  # local branches already merged into the main branch
    "S8_worktrees": (10, 20),  # git worktrees
    "S9": (20, 100),  # handoff/plan docs without a "estado:"/"status:" header
}


@dataclass(frozen=True)
class ReorgConfig:
    """Budgets and lexicon extension for ``declutter reorg`` (table ``[reorg]``, spec §3, §6.4)."""

    l1_max_bytes: int = 12_288
    l1_max_lines: int = 200
    zone_max_lines: int = 150
    codex_warn_pct: int = 60
    extra_lexicon: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    """Effective configuration (defaults, then global file, then project file)."""

    thresholds: Mapping[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_THRESHOLDS)
    )
    bytes_per_token: float = 3.6
    codex_budget_bytes: int = 32_768
    import_max_depth: int = 4
    memory_max_lines: int = 200
    memory_max_bytes: int = 25_600
    lang: str = "en"
    reorg: ReorgConfig = field(default_factory=ReorgConfig)


@dataclass(frozen=True)
class Project:
    """A project to inspect. ``home`` and ``claude_home`` are injectable for tests.

    ``session_cwd`` is the folder Claude Code sessions for this project are started from
    when it differs from ``cwd`` (registry ``session`` field). It only selects the
    ``~/.claude/projects/<slug>`` folder used for memory (S2, S5) and transcripts (S6).
    """

    root: Path
    cwd: Path
    home: Path
    claude_home: Path
    session_cwd: Path | None = None

    @classmethod
    def at(
        cls,
        root: Path,
        *,
        cwd: Path | None = None,
        home: Path | None = None,
        claude_home: Path | None = None,
        session_cwd: Path | None = None,
    ) -> Project:
        """Build a project with every path resolved (``cwd`` defaults to ``root``)."""
        r = root.resolve()
        h = (home or Path.home()).resolve()
        return cls(
            root=r,
            cwd=(cwd or r).resolve(),
            home=h,
            claude_home=(claude_home or h / ".claude").resolve(),
            session_cwd=session_cwd.resolve() if session_cwd is not None else None,
        )


@dataclass(frozen=True)
class LoadedFile:
    """One file an agent loads at startup."""

    path: Path
    kind: str  # "user_claude_md" | "claude_md" | "claude_local_md" | "rule" | "import"
    #          | "memory" | "agents_md"
    bytes: int  # bytes actually loaded (after any truncation)
    lines: int  # lines actually loaded
    truncated: bool = False


@dataclass(frozen=True)
class LoadSet:
    """What a given agent loads at session start."""

    agent: str  # "claude" | "codex"
    files: tuple[LoadedFile, ...]
    requested_bytes: int  # bytes the agent would load if there were no limits

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)


@dataclass(frozen=True)
class Signal:
    """One measured signal with its verdict."""

    id: str  # "S1".."S9"
    name: str  # short human name (English); render.py translates
    value: float | None  # None when not applicable
    unit: str  # "lines" | "tokens" | "%" | "days" | "count"
    level: Level
    yellow: float
    red: float
    detail: str = ""  # one line, factual (e.g. "CLAUDE.md 450 lines")
    action: str = ""  # message key for the suggested action (see i18n.py)


@dataclass(frozen=True)
class EstadoItem:
    text: str
    done: bool = False


@dataclass(frozen=True)
class Estado:
    """Parsed state file (ESTADO.md / STATE.md)."""

    path: Path
    updated: date | None
    phase: str | None
    next_ai: tuple[EstadoItem, ...] = ()
    human: tuple[EstadoItem, ...] = ()
    blocked: tuple[str, ...] = ()
    resume: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()  # parse problems, never raised

    @property
    def open_next_ai(self) -> tuple[EstadoItem, ...]:
        return tuple(i for i in self.next_ai if not i.done)

    @property
    def open_human(self) -> tuple[EstadoItem, ...]:
        return tuple(i for i in self.human if not i.done)


@dataclass(frozen=True)
class Measurement:
    project: Project
    signals: tuple[Signal, ...]

    @property
    def level(self) -> Level:
        real = [s.level for s in self.signals if s.level is not Level.NA]
        return max(real) if real else Level.NA

    @property
    def worst(self) -> Signal | None:
        real = [s for s in self.signals if s.level is not Level.NA]
        return max(real, key=lambda s: s.level) if real else None


@dataclass(frozen=True)
class RegistryEntry:
    """One project listed in ~/.config/declutter/projects.toml."""

    path: Path
    name: str
    session: str | None = None


@dataclass(frozen=True)
class RadarRow:
    entry: RegistryEntry
    level: Level
    last_activity: datetime | None
    dirty: int | None  # uncommitted changes; None if not a git repo
    ahead: int | None  # commits not pushed to upstream; None if unknown
    estado: Estado | None
    worst: Signal | None
    error: str | None = None  # set when the project could not be inspected


def level_for(value: float | None, yellow: float, red: float) -> Level:
    """Shared threshold rule: ``> red`` is RED, ``> yellow`` is YELLOW, else GREEN."""
    if value is None:
        return Level.NA
    if value > red:
        return Level.RED
    if value > yellow:
        return Level.YELLOW
    return Level.GREEN


# --- declutter reorg (phase 2a, spec §4.8). Line ranges are 1-based and inclusive. ---


@dataclass(frozen=True)
class SourceDoc:
    """One source file split under the exact line model of spec §4.2."""

    path: str  # POSIX, relative to root
    sha256: str  # of the raw bytes (BOM included)
    bytes: int
    lines: tuple[builtins.bytes, ...]  # BOM removed, "\r" kept
    bom: bool
    final_newline: bool
    eol: str  # "lf" | "crlf" | "mixed" | "none"


@dataclass(frozen=True)
class Block:
    id: str  # "U13.b3"
    kind: str  # "fence"|"comment"|"table"|"heading"|"item"|"paragraph"|"front_matter"
    start: int
    end: int
    key: str  # "<heading_key>:<sha12>[~N]"
    categories: frozenset[str] = frozenset()
    imperative: bool = False
    decision: bool = False
    trap: bool = False
    claude_only: bool = False


@dataclass(frozen=True)
class Features:
    """Classification features of one unit (spec §5.1)."""

    dated: str | None = None  # "YYYY-MM-DD" | "undated" | None (D did not match)
    branch: str | None = None
    rule: bool = False  # R (title)
    exec: bool = False  # C (title)
    trap: bool = False  # T (title)
    map: bool = False  # M (title)
    title_guard: frozenset[str] = frozenset()  # G2
    k: int = 0  # blocks with imperative lexicon
    s: int = 0  # blocks with >=1 safety category
    t: int = 0  # blocks with trap lexicon
    imports: int = 0  # I
    q_ratio: float = 0.0
    zone: str | None = None
    zone_ratio: float = 0.0
    zone_mentions: int = 0
    generated: bool = False  # rule 1: a region written by an earlier run (added in T05)


@dataclass(frozen=True)
class Unit:
    id: str  # "U00", "U00b", "U01"...
    source: str
    heading: str  # "" for preamble units
    heading_key: str  # preamble: its id
    start: int
    end: int
    blocks: tuple[Block, ...]
    features: Features = field(default_factory=Features)


@dataclass(frozen=True)
class Decision:
    unit: str
    dest: str  # "L1"|"L1c"|"L2:<dir>"|"L4"|"L5t"|"L5m"
    file: str  # destination path (POSIX)
    card: bool
    pointer: str | None  # "root"|"zone:<dir>"|"root+zone:<dir>"|None
    confidence: str  # "high"|"medium"|"low"
    reasons: tuple[str, ...]
    protected: bool
    needs_confirmation: bool
    card_id: str | None = None  # manifest field units[].card_id


@dataclass(frozen=True)
class Placement:
    role: str  # "primary"|"copy"|"generated"|"marker"|"extended_prefix"|"control"
    dest: str
    at: tuple[int, int]
    sha256: str  # sha256(b"\n".join(lines)); control: sha256 of the whole file
    unit: str | None = None
    block: str | None = None
    source: str | None = None
    src: tuple[int, int] | None = None
    kind: str | None = None  # generated region type, marker "src"|"end"|"anchor", "front_matter"
    backup_sha256: str | None = None  # extended_prefix only
    prefix_newline_added: bool = False  # extended_prefix only


@dataclass(frozen=True)
class Pin:
    id: str  # "P-001"
    reorg: str
    unit: str
    block: str | None
    file: str
    role: str  # "copy"|"l1_block"|"unit"
    categories: tuple[str, ...]
    lines: int
    sha256: str


@dataclass(frozen=True)
class ReorgPlan:
    id: str
    created: str
    sources: tuple[SourceDoc, ...]
    units: tuple[Unit, ...]
    decisions: tuple[Decision, ...]
    pins: tuple[Pin, ...]
    projection: Mapping[str, object]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ProofFailure:
    check: str  # "C0".."C9", "G2", "G3"
    unit: str | None
    source: str | None
    src: tuple[int, int] | None
    dest: str | None
    at: tuple[int, int] | None
    reason: str  # metadata only, never file content


@dataclass(frozen=True)
class ProofResult:
    ok: bool
    mode: str  # "exact" | "posterior"
    failures: tuple[ProofFailure, ...]
    states: tuple[tuple[str, str], ...] = ()  # (unit id, state key) in posterior mode
