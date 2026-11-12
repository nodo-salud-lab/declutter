"""Project-scoped Codex budget (``declutter reorg codex-budget``; S3).

Codex reads ``project_doc_max_bytes`` from a project-local ``.codex/config.toml``, but only
when the project is trusted in Codex (``~/.codex/config.toml``, which this module never
reads or writes). The writer sets one top-level integer key and keeps every other byte of
the file; each candidate edit is re-parsed and accepted only when nothing else changed.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from declutter_context import fsutil

CODEX_CONFIG = ".codex/config.toml"
CODEX_KEY = "project_doc_max_bytes"
DECLUTTER_CONFIG = "declutter.toml"
DECLUTTER_KEY = "codex_budget_bytes"
DEFAULT_BYTES = 65_536  # default of ``--bytes``: twice Codex's 32 KiB
_STEP = 16_384
_HEADER_RE = re.compile(r"^[ \t]*\[")


class CodexCfgError(Exception):
    """A refused edit. ``key`` is an i18n key (``reorg.<key>``); never file content."""

    def __init__(self, key: str, path: str = "") -> None:
        super().__init__(f"{key}: {path}" if path else key)
        self.key = key
        self.path = path


def suggest_bytes(largest: int) -> int:
    """Budget that fits ``largest`` bytes with 10 % headroom: the smallest multiple of
    16 KiB that is >= ``ceil(largest * 1.1)``, never under 64 KiB."""
    need = math.ceil(largest * 1.1)
    return max(DEFAULT_BYTES, math.ceil(need / _STEP) * _STEP)


def _parse(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise CodexCfgError("toml_invalid") from None


def _same(candidate: str, want: Mapping[str, Any]) -> bool:
    try:
        return tomllib.loads(candidate) == want
    except tomllib.TOMLDecodeError:
        return False


def set_int_key(text: str, key: str, value: int) -> str:
    """``text`` with the top-level ``key`` set to ``value``; every other line unchanged.

    An existing top-level assignment keeps its indentation, spacing and trailing comment;
    a missing one is inserted before the first table header (or appended). Raises
    :class:`CodexCfgError` (``toml_invalid``) when ``text`` is not TOML and
    (``cannot_edit``) when no candidate edit leaves the rest of the document identical.
    """
    want = _parse(text)
    want[key] = value
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    k = re.escape(key)
    assign = re.compile(
        rf"^([ \t]*)({k}|\"{k}\"|'{k}')([ \t]*=[ \t]*)([^#\r\n]*?)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?$"
    )
    for i, line in enumerate(lines):
        m = assign.match(line)
        if m is None:
            continue
        new_line = m.group(1) + m.group(2) + m.group(3) + str(value) + m.group(5)
        candidate = "".join([*lines[:i], new_line + (m.group(6) or ""), *lines[i + 1 :]])
        if _same(candidate, want):
            return candidate
    entry = f"{key} = {value}{nl}"
    for i, line in enumerate(lines):
        if _HEADER_RE.match(line):
            candidate = "".join([*lines[:i], entry, *lines[i:]])
            if _same(candidate, want):
                return candidate
    base = text if not text or text.endswith("\n") else text + nl
    candidate = base + entry
    if _same(candidate, want):
        return candidate
    raise CodexCfgError("cannot_edit")


def project_doc_max_bytes(root: Path) -> int | None:
    """The top-level ``project_doc_max_bytes`` of ``root/.codex/config.toml`` (a positive
    integer), or ``None`` when the file is missing, unreadable, not TOML or the key is
    absent or invalid. Whether Codex honors it depends on trust, which is not checked."""
    text = fsutil.safe_read_text(root / CODEX_CONFIG, root)
    if text is None:
        return None
    try:
        value = tomllib.loads(text).get(CODEX_KEY)
    except tomllib.TOMLDecodeError:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


@dataclass(frozen=True)
class PendingWrite:
    """One file ``declutter reorg codex-budget`` would write."""

    rel: str
    path: Path
    data: bytes
    created: bool
    changed: bool
    line: str  # ``key = value`` (metadata for the output)


def _prepare(root: Path, rel: str, key: str, value: int, header: str) -> PendingWrite:
    path = root / rel
    for p in (path.parent, path):
        if p != root and p.is_symlink():
            raise CodexCfgError("cfg_symlink", rel)
    if not os.path.lexists(path):
        text = header + set_int_key("", key, value)
        return PendingWrite(rel, path, text.encode("utf-8"), True, True, f"{key} = {value}")
    try:
        raw = fsutil.safe_read_bytes(path, root)
    except fsutil.UnsafeReadError as exc:
        raise CodexCfgError("unsafe_read", f"{rel} ({exc.reason})") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CodexCfgError("not_utf8", rel) from None
    try:
        new = set_int_key(text, key, value)
    except CodexCfgError as exc:
        raise CodexCfgError(exc.key, rel) from None
    data = new.encode("utf-8")
    return PendingWrite(rel, path, data, False, data != raw, f"{key} = {value}")


def prepare(root: Path, value: int, codex_header: str = "") -> tuple[PendingWrite, PendingWrite]:
    """Both writes, validated before anything is written: ``.codex/config.toml`` and
    ``declutter.toml``. ``codex_header`` prefixes a newly created ``.codex/config.toml``."""
    codex = _prepare(root, CODEX_CONFIG, CODEX_KEY, value, codex_header)
    declutter = _prepare(root, DECLUTTER_CONFIG, DECLUTTER_KEY, value, "")
    return codex, declutter


def write(root: Path, w: PendingWrite) -> None:
    """Atomic write confined to ``root`` (refuses symlinks and paths outside)."""
    try:
        fsutil.atomic_write_bytes(w.path, w.data, root)
    except fsutil.UnsafeWriteError as exc:
        raise CodexCfgError("unsafe_write", f"{w.rel} ({exc.reason})") from None
