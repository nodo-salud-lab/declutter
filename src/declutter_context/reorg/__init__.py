"""``declutter reorg``: lossless reorganization of CLAUDE.md / AGENTS.md (phase 2a).

This module holds what every reorg step shares: exit codes and their precedence
(spec §10.5), the :class:`Issue` record, :class:`ReorgError` and the prompter used by the
guided flow. Issues carry metadata only (keys, ids, line ranges, sizes), never file content.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INTERNAL = 2
EXIT_THRESHOLD = 3
EXIT_PROOF = 4
EXIT_SECRET = 5
EXIT_INTERRUPTED = 130

# Highest first: 5 > 2 > 1 > 4 > 3. 130 (interrupted) overrides everything (spec §10.5).
PRECEDENCE: tuple[int, ...] = (EXIT_SECRET, EXIT_INTERNAL, EXIT_USAGE, EXIT_PROOF, EXIT_THRESHOLD)


def resolve_code(codes: Iterable[int]) -> int:
    """130 if present; else the first of PRECEDENCE present; else 0."""
    present = set(codes)
    if EXIT_INTERRUPTED in present:
        return EXIT_INTERRUPTED
    for code in PRECEDENCE:
        if code in present:
            return code
    return EXIT_OK


@dataclass(frozen=True)
class Issue:
    """One detected problem. ``detail`` is metadata only; NEVER file content."""

    code: int  # 1..5
    key: str  # i18n key, e.g. "check.4", "C2", "secret", "concurrent_edit"
    unit: str | None = None
    block: str | None = None
    source: str | None = None
    src: tuple[int, int] | None = None
    dest: str | None = None
    at: tuple[int, int] | None = None
    detail: str = ""


class ReorgError(Exception):
    """Raised with every issue found; the exit code follows the precedence rule."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        self.issues: tuple[Issue, ...] = tuple(issues)
        super().__init__(", ".join(i.key for i in self.issues))

    @property
    def code(self) -> int:
        return resolve_code(i.code for i in self.issues)


class Prompter(Protocol):
    """Line-oriented interaction, injectable for tests."""

    def is_tty(self) -> bool: ...

    def ask(self, prompt: str) -> str:
        """Read one line (without its newline); "" on EOF."""
        ...

    def say(self, text: str) -> None: ...


class StdPrompter:
    """Prompter over stdin/stdout."""

    def is_tty(self) -> bool:
        try:
            return sys.stdin.isatty() and sys.stdout.isatty()
        except (AttributeError, ValueError):
            return False

    def ask(self, prompt: str) -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        line = sys.stdin.readline()
        return line.rstrip("\r\n")

    def say(self, text: str) -> None:
        print(text)
