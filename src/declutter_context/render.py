"""Text, Markdown and JSON renderers for measurements and the project radar."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date

from declutter_context.i18n import t
from declutter_context.model import Estado, EstadoItem, Level, Measurement, RadarRow, Signal

LEVEL_EMOJI: dict[Level, str] = {
    Level.GREEN: "🟢",
    Level.YELLOW: "🟡",
    Level.RED: "🔴",
    Level.NA: "⚪",
}

_LEVEL_KEY: dict[Level, str] = {
    Level.GREEN: "green",
    Level.YELLOW: "yellow",
    Level.RED: "red",
    Level.NA: "na",
}

_DASH = "—"


def _num(value: float | None) -> str:
    """Format a number: ``n/a`` for None, no decimals for integral values."""
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _level_label(level: Level, lang: str) -> str:
    return t(f"level.{_LEVEL_KEY[level]}", lang)


def _unit(unit: str, lang: str) -> str:
    translated = t(f"unit.{unit}", lang)
    return unit if translated == f"unit.{unit}" else translated


def _signal_line(s: Signal, lang: str) -> str:
    name = t(f"sig.{s.id}", lang)
    if name == f"sig.{s.id}":
        name = s.name
    thresholds = f"(y>{_num(s.yellow)} r>{_num(s.red)})"
    parts = [
        f"{LEVEL_EMOJI[s.level]} {s.id} {name}: {_num(s.value)} {_unit(s.unit, lang)}",
        thresholds,
    ]
    if s.detail:
        parts.append(s.detail)
    return " ".join(parts)


def measurement_text(m: Measurement, lang: str, bytes_per_token: float = 3.6) -> str:
    """Render a measurement as plain text: header, one line per signal, actions, note."""
    lines = [f"{LEVEL_EMOJI[m.level]} {m.project.root} — {_level_label(m.level, lang)}"]
    lines.extend(_signal_line(s, lang) for s in m.signals)

    pending = [s for s in m.signals if s.level in (Level.RED, Level.YELLOW) and s.action]
    pending.sort(key=lambda s: -int(s.level))  # stable: RED first, original order within level
    if pending:
        lines.append("")
        lines.append(f"{t('next_actions', lang)}:")
        seen: set[str] = set()
        for s in pending:
            if s.action in seen:
                continue
            seen.add(s.action)
            lines.append(f"  {LEVEL_EMOJI[s.level]} {s.id}: {t(s.action, lang)}")
    lines.append("")
    lines.append(t("tokens_note", lang, bpt=f"{bytes_per_token:g}"))
    return "\n".join(lines)


def measurement_json(m: Measurement) -> str:
    """Render a measurement as indented JSON (levels as lowercase strings)."""
    data = {
        "project": str(m.project.root),
        "level": _LEVEL_KEY[m.level],
        "signals": [
            {
                "id": s.id,
                "name": s.name,
                "value": s.value,
                "unit": s.unit,
                "level": _LEVEL_KEY[s.level],
                "yellow": s.yellow,
                "red": s.red,
                "detail": s.detail,
                "action": s.action,
            }
            for s in m.signals
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- radar


def _opt_int(value: int | None) -> str:
    return _DASH if value is None else str(value)


def _activity(row: RadarRow) -> str:
    return _DASH if row.last_activity is None else row.last_activity.date().isoformat()


def _worst_text(row: RadarRow, lang: str) -> str:
    if row.error:
        return row.error
    parts: list[str] = []
    w = row.worst
    # Only a yellow/red signal is a "worst signal"; a missing state file (S7) is
    # already explained by the no-state hint below, so it is not repeated.
    shown = w is not None and w.level in (Level.YELLOW, Level.RED)
    if w is not None and shown and not (w.id == "S7" and row.estado is None):
        parts.append(f"{w.id} {w.detail}".strip())
    if row.estado is None:
        parts.append(t("radar.no_state", lang))
    return " | ".join(parts) if parts else _DASH


def _counts(row: RadarRow) -> tuple[str, str]:
    if row.estado is None:
        return _DASH, _DASH
    return str(len(row.estado.open_next_ai)), str(len(row.estado.open_human))


def _radar_cells(row: RadarRow, lang: str) -> list[str]:
    nxt, hum = _counts(row)
    return [
        LEVEL_EMOJI[row.level],
        row.entry.name,
        _activity(row),
        _opt_int(row.dirty),
        _opt_int(row.ahead),
        nxt,
        hum,
        _worst_text(row, lang),
    ]


def _open_sections(
    row: RadarRow,
) -> tuple[tuple[EstadoItem, ...], tuple[EstadoItem, ...], tuple[str, ...]]:
    estado: Estado | None = row.estado
    if estado is None:
        return (), (), ()
    return estado.open_next_ai, estado.open_human, estado.blocked


def radar_text(rows: Sequence[RadarRow], lang: str) -> str:
    """Render the radar as an aligned text table plus open items per project."""
    headers = [
        t("col.level", lang),
        t("col.project", lang),
        t("col.activity", lang),
        t("col.dirty", lang),
        t("col.ahead", lang),
        t("col.next_ai", lang),
        t("col.human", lang),
        t("col.worst", lang),
    ]
    table = [headers] + [_radar_cells(r, lang) for r in rows]
    # The emoji column is fixed width (rendered as 2 cells); pad the others by length.
    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    widths[0] = max(len(headers[0]), 2)

    def fmt(cells: list[str]) -> str:
        out = []
        for i, c in enumerate(cells):
            if i == 0:
                pad = widths[0] - (2 if c in LEVEL_EMOJI.values() else len(c))
                out.append(c + " " * max(pad, 0))
            elif i == len(cells) - 1:
                out.append(c)
            else:
                out.append(c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [fmt(table[0])]
    lines.extend(fmt(cells) for cells in table[1:])

    for row in rows:
        nxt, hum, blk = _open_sections(row)
        if not nxt and not hum and not blk:
            continue
        lines.append("")
        lines.append(f"{row.entry.name}")
        if nxt:
            lines.append(f"  {t('radar.next_ai', lang)}:")
            lines.extend(f"    - {i.text}" for i in nxt)
        if hum:
            lines.append(f"  {t('radar.human', lang)}:")
            lines.extend(f"    - {i.text}" for i in hum)
        if blk:
            lines.append(f"  {t('radar.blocked', lang)}:")
            lines.extend(f"    - {b}" for b in blk)
    return "\n".join(lines)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def radar_markdown(rows: Sequence[RadarRow], lang: str, generated: date) -> str:
    """Render the radar as a Markdown document with a summary table and per-project lists."""
    headers = [
        t("col.level", lang),
        t("col.project", lang),
        t("col.session", lang),
        t("col.activity", lang),
        t("col.dirty", lang),
        t("col.ahead", lang),
        t("col.next_ai", lang),
        t("col.human", lang),
        t("col.worst", lang),
    ]
    lines = [
        f"# {t('radar.title', lang)}",
        "",
        f"{t('radar.generated', lang)}: {generated.isoformat()}",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for r in rows:
        c = _radar_cells(r, lang)
        cells = [c[0], c[1], r.entry.session or _DASH, *c[2:]]
        lines.append("| " + " | ".join(_md_cell(x) for x in cells) + " |")

    for r in rows:
        nxt, hum, blk = _open_sections(r)
        if not nxt and not hum and not blk:
            continue
        lines.extend(["", f"## {r.entry.name}"])
        if nxt:
            lines.extend(["", f"### {t('radar.next_ai', lang)}", ""])
            lines.extend(f"- {i.text}" for i in nxt)
        if hum:
            lines.extend(["", f"### {t('radar.human', lang)}", ""])
            lines.extend(f"- {i.text}" for i in hum)
        if blk:
            lines.extend(["", f"### {t('radar.blocked', lang)}", ""])
            lines.extend(f"- {b}" for b in blk)
    return "\n".join(lines) + "\n"


def _estado_dict(e: Estado) -> dict[str, object]:
    return {
        "path": str(e.path),
        "updated": e.updated.isoformat() if e.updated else None,
        "phase": e.phase,
        "next_ai": [{"text": i.text, "done": i.done} for i in e.next_ai],
        "human": [{"text": i.text, "done": i.done} for i in e.human],
        "blocked": list(e.blocked),
        "resume": list(e.resume),
        "errors": list(e.errors),
    }


def radar_json(rows: Sequence[RadarRow]) -> str:
    """Render the radar as a JSON list (ISO dates, levels as lowercase strings)."""
    data = [
        {
            "name": r.entry.name,
            "path": str(r.entry.path),
            "session": r.entry.session,
            "level": _LEVEL_KEY[r.level],
            "last_activity": r.last_activity.isoformat() if r.last_activity else None,
            "dirty": r.dirty,
            "ahead": r.ahead,
            "worst": (
                {"id": r.worst.id, "level": _LEVEL_KEY[r.worst.level], "detail": r.worst.detail}
                if r.worst
                else None
            ),
            "error": r.error,
            "estado": _estado_dict(r.estado) if r.estado else None,
        }
        for r in rows
    ]
    return json.dumps(data, indent=2, ensure_ascii=False)
