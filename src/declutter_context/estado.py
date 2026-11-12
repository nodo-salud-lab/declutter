"""Parser for the project state file (ESTADO.md / STATE.md). See spec section 4.

The parser never raises: every problem becomes an entry in ``Estado.errors``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

from declutter_context.fsutil import safe_read_text
from declutter_context.model import Estado, EstadoItem

STATE_FILENAMES: tuple[str, ...] = ("ESTADO.md", "STATE.md")

_SECTION_ALIASES: dict[str, str] = {
    "siguiente (ia)": "next_ai",
    "next (ai)": "next_ai",
    "decide el humano": "human",
    "human decides": "human",
    "bloqueado": "blocked",
    "blocked": "blocked",
    "donde retomar": "resume",
    "resume at": "resume",
}

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^##(?!#)\s*(.*)$")
_UPDATED_RE = re.compile(r"^(?:actualizado|updated)\s*:\s*(.*)$", re.IGNORECASE)
_PHASE_RE = re.compile(r"^(?:fase|phase)\s*:\s*(.*)$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHECKBOX_RE = re.compile(r"^\[([ xX])\](?:\s+(.*)|\s*)$")


def _normalize(title: str) -> str:
    """Lowercase, strip accents and collapse whitespace for section matching."""
    decomposed = unicodedata.normalize("NFKD", title)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def find_estado(root: Path) -> Path | None:
    """Return the first existing state file under ``root``, or None."""
    for name in STATE_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _parse_item(raw: str) -> EstadoItem | None:
    """Parse the text after '- ' into an item; None if it is empty."""
    body = raw.strip()
    done = False
    m = _CHECKBOX_RE.match(body)
    if m:
        done = m.group(1) in ("x", "X")
        body = (m.group(2) or "").strip()
    if not body:
        return None
    return EstadoItem(text=body, done=done)


def parse_estado(text: str, path: Path) -> Estado:
    """Parse the text of a state file into an ``Estado``. Never raises."""
    errors: list[str] = []
    updated: date | None = None
    phase: str | None = None
    items: dict[str, list[EstadoItem]] = {"next_ai": [], "human": [], "blocked": [], "resume": []}
    seen_updated = False
    current: str | None = None
    in_header = True

    clean = _COMMENT_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    for line in clean.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading:
            in_header = False
            current = _SECTION_ALIASES.get(_normalize(heading.group(1)))
            continue
        if in_header:
            stripped = line.strip()
            um = _UPDATED_RE.match(stripped)
            if um and not seen_updated:
                seen_updated = True
                value = um.group(1).strip()
                if _ISO_DATE_RE.match(value):
                    try:
                        updated = date.fromisoformat(value)
                    except ValueError:
                        errors.append(f"invalid date in 'Actualizado/Updated': {value!r}")
                else:
                    errors.append(f"invalid date in 'Actualizado/Updated': {value!r}")
                continue
            pm = _PHASE_RE.match(stripped)
            if pm and phase is None:
                phase = pm.group(1).strip() or None
            continue
        if current is None or not line.startswith("- "):
            continue
        item = _parse_item(line[2:])
        if item is None:
            continue
        items[current].append(item)

    return Estado(
        path=path,
        updated=updated,
        phase=phase,
        next_ai=tuple(items["next_ai"]),
        human=tuple(items["human"]),
        blocked=tuple(i.text for i in items["blocked"]),
        resume=tuple(i.text for i in items["resume"]),
        errors=tuple(errors),
    )


def load_estado(root: Path) -> Estado | None:
    """Find and parse the state file under ``root``; None if absent or unreadable."""
    path = find_estado(root)
    if path is None:
        return None
    text = safe_read_text(path, root)
    if text is None:
        return None
    return parse_estado(text, path)
