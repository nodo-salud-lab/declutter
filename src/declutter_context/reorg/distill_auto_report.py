"""The distill-auto report (fase 3A §7): built by the engine only, from batch.json, the
journals and measurements. It carries ids, keys, line numbers, counts, bytes and shas;
never a byte of a card, a literal or AGENTS.md (G6). Seal: GUÍA."""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import t
from .distill_batch import CardResult


@dataclass(frozen=True)
class Report:
    reorg: str
    batch: int
    branch: str
    tag: str
    base_branch: str
    state: str  # done | stopped | <state> with stop_key
    stop_key: str | None
    dry_run: bool
    applied: tuple[CardResult, ...]
    skipped: tuple[CardResult, ...]  # skipped, stopped and failed cards of the batch + not_ready
    not_ready: tuple[tuple[str, tuple[tuple[str, int | None, int | None], ...]], ...]
    user_skipped: tuple[str, ...]
    distilled_before: tuple[str, ...]
    no_card_bytes: int
    agents_before: tuple[int, int]
    agents_after: tuple[int, int]
    tightened: tuple[str, ...]
    pending_paths: tuple[str, ...]

    @property
    def merge_cmd(self) -> str:
        return f"git switch {self.base_branch} && git merge --no-ff {self.branch}"

    @property
    def undo_cmd(self) -> str:
        return f"declutter reorg distill-undo {self.reorg} --auto {self.batch}"


def _where(key: str, line: int | None, col: int | None) -> str:
    if line is None:
        return key
    return f"{key}@{line}:{col}" if col is not None else f"{key}@{line}"


def _signed(n: int) -> str:
    """Explicit sign, so a negative delta never renders as a double minus."""
    return f"{n:+d}"


def _m(key: str, lang: str, **kw: object) -> str:
    return t(f"distill.auto.{key}", lang, **kw)


def report_md(r: Report, lang: str) -> str:
    none = _m("report.none", lang)
    state = r.state if r.stop_key is None else f"{r.state} ({r.stop_key})"
    out = [
        _m("report.title", lang, id=r.reorg, n=r.batch),
        "",
        _m("report.seal", lang),
        *([_m("report.dry_run", lang)] if r.dry_run else []),
        _m("report.state", lang, branch=r.branch, tag=r.tag, state=state),
        "",
        _m("report.applied", lang, n=len(r.applied)),
    ]
    out += [
        _m(
            "report.applied_row",
            lang,
            card=c.card,
            copies=c.copies if c.copies is not None else "?",
            removed=c.bytes_removed if c.bytes_removed is not None else "?",
            added=c.bytes_added if c.bytes_added is not None else "?",
            commit=(c.commit or "-")[:12],
        )
        for c in r.applied
    ] or [none]
    out += ["", _m("report.skipped", lang, n=len(r.skipped) + len(r.not_ready))]
    rows = [
        _m("report.skipped_row", lang, card=c.card, keys=", ".join(c.reasons) or c.result)
        for c in r.skipped
    ]
    rows += [
        _m(
            "report.skipped_row",
            lang,
            card=card,
            keys=", ".join(_where(k, ln, col) for k, ln, col in reasons) or "not_ready",
        )
        for card, reasons in r.not_ready
    ]
    out += rows or [none]
    out += ["", _m("report.not_included", lang)]
    out.append(_m("report.user_skipped", lang, cards=", ".join(r.user_skipped) or none))
    out.append(_m("report.distilled_before", lang, cards=", ".join(r.distilled_before) or none))
    out.append(_m("report.no_card", lang, bytes=r.no_card_bytes))
    (b0, l0), (b1, l1) = r.agents_before, r.agents_after
    out += [
        "",
        _m(
            "report.agents",
            lang,
            b0=b0,
            l0=l0,
            b1=b1,
            l1=l1,
            db=_signed(b1 - b0),
            dl=_signed(l1 - l0),
        ),
    ]
    out.append("")
    if r.tightened:
        out.append(_m("report.tighten", lang, items=", ".join(r.tightened)))
    else:
        out.append(_m("report.tighten_none", lang))
    paths = "\n".join(f"- {p}" for p in r.pending_paths) or none
    out += ["", _m("report.pending", lang, paths=paths)]
    out += ["", _m("report.commands", lang)]
    if not r.dry_run:
        if r.applied:
            out.append(_m("report.merge", lang, cmd=r.merge_cmd))
        out.append(_m("report.undo", lang, cmd=r.undo_cmd))
    return "\n".join(out) + "\n"


def _card(c: CardResult) -> dict[str, object]:
    obj: dict[str, object] = {"card": c.card, "result": c.result}
    for name in ("commit", "bytes_removed", "bytes_added", "copies"):
        value = getattr(c, name)
        if value is not None:
            obj[name] = value
    if c.reasons:
        obj["reasons"] = list(c.reasons)
    return obj


def report_obj(r: Report) -> dict[str, object]:
    return {
        "schema": "declutter.distill-auto-report/1",
        "seal": "GUIA",
        "reorg": r.reorg,
        "batch": r.batch,
        "branch": r.branch,
        "tag": r.tag,
        "base_branch": r.base_branch,
        "state": r.state,
        "stop_key": r.stop_key,
        "dry_run": r.dry_run,
        "applied": [_card(c) for c in r.applied],
        "skipped": [_card(c) for c in r.skipped],
        "not_ready": [
            {"card": card, "reasons": [{"key": k, "line": ln, "col": col} for k, ln, col in rs]}
            for card, rs in r.not_ready
        ],
        "user_skipped": list(r.user_skipped),
        "distilled_before": list(r.distilled_before),
        "no_card_bytes": r.no_card_bytes,
        "agents": {"before": list(r.agents_before), "after": list(r.agents_after)},
        "tightened": list(r.tightened),
        "pending_paths": list(r.pending_paths),
        "commands": (
            {} if r.dry_run else {"merge": r.merge_cmd if r.applied else None, "undo": r.undo_cmd}
        ),
    }
