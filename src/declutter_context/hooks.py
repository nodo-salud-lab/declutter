"""Claude Code hook and status line entry points.

Both public entry points take the raw stdin text, never raise, and return the text to
print (possibly empty). Failures are appended to the cache hook log.
"""

from __future__ import annotations

import json
from pathlib import Path

from declutter_context import cache, config, gitutil, measure
from declutter_context.i18n import t
from declutter_context.model import Level, Measurement, Project, Signal
from declutter_context.render import LEVEL_EMOJI

_STATUS_DETAIL_MAX = 60
HYGIENE_COMMAND = "/declutter:declutter-hygiene"  # plugin skills are namespaced


def project_from_cwd(
    cwd: Path, *, home: Path | None = None, claude_home: Path | None = None
) -> Project:
    """Build a Project whose root is the git toplevel of ``cwd`` (or ``cwd`` itself)."""
    root = cwd
    top = gitutil.run_git(cwd, "rev-parse", "--show-toplevel")
    if top is not None and top.strip():
        root = Path(top.strip())
    return Project.at(root, cwd=cwd, home=home, claude_home=claude_home)


def _parse(stdin_text: str) -> dict[str, object]:
    """Parse the hook JSON; raise ValueError when it is not a JSON object."""
    data = json.loads(stdin_text)
    if not isinstance(data, dict):
        raise ValueError("stdin JSON is not an object")
    return data


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _measure(
    cwd_value: object, home: Path | None, claude_home: Path | None
) -> tuple[Measurement, str]:
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else Path.cwd()
    project = project_from_cwd(cwd, home=home, claude_home=claude_home)
    cfg = config.load_config(project.root, home=home)
    return measure.measure_cached(project, cfg, home=home), cfg.lang


def session_start(
    stdin_text: str, *, home: Path | None = None, claude_home: Path | None = None
) -> str:
    """Return a one-line warning when the project is RED, else an empty string."""
    try:
        data = _parse(stdin_text)
        m, lang = _measure(data.get("cwd"), home, claude_home)
        worst = m.worst
        if m.level is not Level.RED or worst is None:
            return ""
        name = t(f"sig.{worst.id}", lang)
        action = t(worst.action, lang) if worst.action else ""
        what = _one_line(worst.detail)
        if worst.value is not None:
            what = f"{_value_text(worst, lang)} ({what})"
        parts = [f"⚠️ declutter: {name} — {what}."]
        if action:
            parts.append(f"{_one_line(action)} ({HYGIENE_COMMAND})")
        else:
            parts.append(f"({HYGIENE_COMMAND})")
        return _one_line(" ".join(parts))
    except Exception as exc:
        cache.log_error(f"session-start: {type(exc).__name__}: {exc}", home=home)
        return ""


def _pct(data: dict[str, object]) -> int | None:
    ctx = data.get("context_window")
    if not isinstance(ctx, dict):
        return None
    value = ctx.get("used_percentage")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(value)


def _cwd_of(data: dict[str, object]) -> object:
    ws = data.get("workspace")
    if isinstance(ws, dict) and isinstance(ws.get("current_dir"), str) and ws["current_dir"]:
        return ws["current_dir"]
    return data.get("cwd")


def _value_text(s: Signal, lang: str) -> str:
    """Localized ``<value> <unit>`` (e.g. ``450 líneas``); the signal name when n/a."""
    if s.value is None:
        return t(f"sig.{s.id}", lang)
    value = str(int(s.value)) if float(s.value).is_integer() else f"{s.value:.1f}"
    unit = t(f"unit.{s.unit}", lang)
    if unit == f"unit.{s.unit}":
        unit = s.unit
    return f"{value} {unit}".strip() if unit != "%" else f"{value}%"


def _signal_label(s: Signal, lang: str = "en") -> str:
    text = _one_line(f"{s.id} {_value_text(s, lang)}")
    if len(text) > _STATUS_DETAIL_MAX:
        text = text[: _STATUS_DETAIL_MAX - 1] + "…"
    return text


def statusline(
    stdin_text: str, *, home: Path | None = None, claude_home: Path | None = None
) -> str:
    """Return e.g. ``ctx 42% · 🟢`` or ``ctx 42% · 🔴 S1 450 lines``; empty on failure.

    The value and unit follow the configured language (``S1 450 líneas`` in Spanish).
    """
    try:
        data = _parse(stdin_text)
        pct = _pct(data)
        m, lang = _measure(_cwd_of(data), home, claude_home)
        prefix = f"ctx {pct}% · " if pct is not None else ""
        worst = m.worst
        if worst is None or m.level in (Level.NA, Level.GREEN):
            return f"{prefix}{LEVEL_EMOJI[Level.GREEN if worst else Level.NA]}"
        return f"{prefix}{LEVEL_EMOJI[m.level]} {_signal_label(worst, lang)}"
    except Exception as exc:
        cache.log_error(f"statusline: {type(exc).__name__}: {exc}", home=home)
        return ""
