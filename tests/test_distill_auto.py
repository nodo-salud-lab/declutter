"""distill-auto / distill-undo --auto (fase 3A §11). Every project is synthetic."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import signal as sig
from pathlib import Path
from types import SimpleNamespace

import pytest

from _distill_helpers import NOW, RID, Fixture, applied, fill, git
from conftest import MakeProject
from declutter_context import ratchet
from declutter_context.i18n import MESSAGES
from declutter_context.model import Config, ProofFailure, ProofResult
from declutter_context.reorg import Issue, distill, distill_apply, distill_auto, gitops
from declutter_context.reorg import distill_auto_report as rpt
from declutter_context.reorg import distill_batch as db
from declutter_context.reorg.apply import ApplyOutcome
from test_apply import FakePrompter
from test_distill_apply import tree, verify

CFG = Config()


@pytest.fixture
def fx(make_project: MakeProject) -> Fixture:
    return applied(make_project)


def _spec(n: int = 1, stop: object = None) -> distill_apply.BatchSpec:
    return distill_apply.BatchSpec(
        batch=n,
        branch=db.batch_branch(RID, n),
        tag=db.batch_tag(RID, n),
        base_branch="main",
        reason=f"auto {RID}/{n}",
        stop=(lambda: False) if stop is None else stop,  # type: ignore[arg-type]
    )


def _on_batch_branch(fx: Fixture, n: int = 1) -> None:
    git(fx.root, "tag", db.batch_tag(RID, n))
    git(fx.root, "switch", "-q", "-c", db.batch_branch(RID, n))


def _card(fx: Fixture, card: str, spec: distill_apply.BatchSpec) -> distill_apply.ApplyOutcome:
    return distill_apply.apply_batch_card(
        fx.project, CFG, RID, card, spec, FakePrompter(tty=False), now=NOW, lang="es"
    )


# --------------------------------------------------------------------------- batch mode (T3)


def test_batch_card_commits_without_tty_with_batch_trailer(fx: Fixture) -> None:
    fill(fx, "D-002")
    _on_batch_branch(fx)
    res = _card(fx, "D-002", _spec())
    assert res.code == 0, [i.key for i in res.issues]
    assert res.journal is not None and res.journal.state == "committed"
    assert res.journal.batch == 1
    body = git(fx.root, "log", "-1", "--format=%B")
    assert f"Declutter-Distill-Batch: {RID}/1" in body
    assert git(fx.root, "branch", "--show-current").strip() == db.batch_branch(RID, 1)
    rec = json.loads((distill_apply.distill_dir(fx.root, RID, "D-002") / "record.json").read_text())
    assert rec["batch"] == 1 and rec["branch"] == db.batch_branch(RID, 1)
    assert verify(fx)[0]


def test_batch_card_refuses_off_the_batch_branch(fx: Fixture) -> None:
    fill(fx, "D-002")
    res = _card(fx, "D-002", _spec())
    assert res.code == 1 and "wrong_branch" in {i.key for i in res.issues}


def test_batch_card_not_ready_is_code_4_and_writes_nothing(fx: Fixture) -> None:
    _on_batch_branch(fx)
    before = tree(fx.root)
    res = _card(fx, "D-002", _spec())  # still pending
    assert res.code == 4
    assert tree(fx.root) == before


def test_brake_between_stage_and_backup_cancels_clean(fx: Fixture) -> None:
    fill(fx, "D-002")
    _on_batch_branch(fx)
    before = tree(fx.root)
    res = _card(fx, "D-002", _spec(stop=lambda: True))
    assert res.code == 130
    assert res.journal is not None and res.journal.state == "undone"
    assert tree(fx.root) == before
    assert git(fx.root, "branch", "--show-current").strip() == db.batch_branch(RID, 1)


def test_brake_before_write_cancels_clean(fx: Fixture) -> None:
    fill(fx, "D-002")
    _on_batch_branch(fx)
    before = tree(fx.root)
    calls = iter([False, True])
    res = _card(fx, "D-002", _spec(stop=lambda: next(calls)))
    assert res.code == 130
    assert tree(fx.root) == before


def test_batch_preflight_ok_and_dirty_output(fx: Fixture) -> None:
    fill(fx, "D-002")
    assert (
        distill_apply.batch_preflight(
            fx.project, CFG, RID, ["D-002"], FakePrompter(tty=False), now=NOW, lang="es"
        )
        == []
    )
    # AGENTS.md is a declared output of D-002 (src/AGENTS.md only of D-003).
    (fx.root / "AGENTS.md").write_bytes((fx.root / "AGENTS.md").read_bytes() + b"x\n")
    keys = {
        i.key
        for i in distill_apply.batch_preflight(
            fx.project, CFG, RID, ["D-002"], FakePrompter(tty=False), now=NOW, lang="es"
        )
    }
    assert "dirty" in keys


def test_batch_card_refuses_a_dirty_output_like_2b(fx: Fixture) -> None:
    fill(fx, "D-002")
    _on_batch_branch(fx)
    (fx.root / "AGENTS.md").write_bytes((fx.root / "AGENTS.md").read_bytes() + b"x\n")
    before = tree(fx.root)
    res = _card(fx, "D-002", _spec())
    assert res.code == 1 and "dirty" in {i.key for i in res.issues}
    assert tree(fx.root) == before


def test_batch_card_brake_issue_and_no_refs_hint(fx: Fixture) -> None:
    fill(fx, "D-002")
    _on_batch_branch(fx)
    p = FakePrompter(tty=False)
    res = distill_apply.apply_batch_card(
        fx.project, CFG, RID, "D-002", _spec(stop=lambda: True), p, now=NOW, lang="es"
    )
    assert [i.key for i in res.issues] == ["distill.auto.stopped_card"]
    assert p.said == [] and p.prompts == []  # no confirmation, no «git branch -D» hint
    assert git(fx.root, "rev-parse", "--verify", "-q", f"refs/tags/{db.batch_tag(RID, 1)}")


def test_undo_card_of_a_batch_names_the_batch(fx: Fixture) -> None:
    fill(fx, "D-002")
    _on_batch_branch(fx)
    assert _card(fx, "D-002", _spec()).code == 0
    res = distill_apply.undo(
        fx.project, CFG, RID, "D-002", FakePrompter(tty=False), yes=True, now=NOW, lang="es"
    )
    assert res.code == 1
    issue = next(i for i in res.issues if i.key == "distill.batch_card")
    assert "D-002" in issue.detail and "--auto 1" in issue.detail and "--skip D-002" in issue.detail


AUTO_KEYS = (
    "start",
    "applied",
    "skipped",
    "halted",
    "braked",
    "nothing",
    "ratchet_human",
    "report.title",
    "report.seal",
    "report.state",
    "report.applied",
    "report.applied_row",
    "report.skipped",
    "report.skipped_row",
    "report.not_included",
    "report.user_skipped",
    "report.distilled_before",
    "report.no_card",
    "report.agents",
    "report.tighten",
    "report.tighten_none",
    "report.pending",
    "report.commands",
    "report.merge",
    "report.undo",
    "report.none",
    "undo.done",
    "card_kept",
    "dry_run_notice",
    "report.dry_run",
    "undo.ask",
    "undo.commit_row",
)


@pytest.mark.parametrize("lang", ["es", "en"])
def test_auto_messages_exist(lang: str) -> None:
    for k in AUTO_KEYS:
        assert f"distill.auto.{k}" in MESSAGES[lang], (lang, k)


def _report() -> rpt.Report:
    return rpt.Report(
        reorg=RID,
        batch=2,
        branch=db.batch_branch(RID, 2),
        tag=db.batch_tag(RID, 2),
        base_branch="main",
        state="done",
        stop_key=None,
        dry_run=False,
        applied=(
            db.CardResult(
                "D-001", "applied", commit="c" * 40, bytes_removed=900, bytes_added=200, copies=1
            ),
        ),
        skipped=(db.CardResult("D-002", "skipped", reasons=("anchor_missing",)),),
        not_ready=(("D-003", (("token_missing", 12, 4),)),),
        user_skipped=("D-004",),
        distilled_before=(),
        no_card_bytes=120,
        agents_before=(5000, 80),
        agents_after=(4300, 70),
        tightened=("S1_max=4300",),
        pending_paths=("docs/decisiones/D-003-x.md",),
    )


def test_report_md_has_every_part_and_the_commands() -> None:
    md = rpt.report_md(_report(), "es")
    for needle in (
        "lote 2",
        "GUÍA",
        "## Aplicadas (1)",
        "D-001",
        "cccccccccccc",
        "## Saltadas (2)",
        "anchor_missing",
        "token_missing@12:4",
        "--skip: D-004",
        "120 B",
        "5000 B / 80 líneas → 4300 B / 70 líneas (-700 B, -10 líneas)",
        "S1_max=4300",
        "docs/decisiones/D-003-x.md",
        f"git switch main && git merge --no-ff {db.batch_branch(RID, 2)}",
        f"declutter reorg distill-undo {RID} --auto 2",
    ):
        assert needle in md, needle


def test_report_obj_is_json_ready() -> None:
    obj = rpt.report_obj(_report())
    json.dumps(obj)
    assert obj["commands"]["undo"] == f"declutter reorg distill-undo {RID} --auto 2"
    assert obj["applied"][0]["card"] == "D-001"


def test_subject_delta_is_signed_without_a_double_minus() -> None:
    assert distill_apply._delta(410) == "\u2212410"
    assert distill_apply._delta(0) == "\u22120"
    assert distill_apply._delta(-73) == "+73"


def test_tightened_lists_only_the_maxima_that_went_down(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        {
            "event": "tighten",
            "old": {"S1_max": 2621, "S3_max_pct": 40},
            "new": {"S1_max": 2500, "S3_max_pct": 40},
        },
        {
            "event": "tighten",
            "old": {"S1_max": 2500, "S3_max_pct": 40},
            "new": {"S1_max": 2411, "S3_max_pct": 38},
        },
    ]
    monkeypatch.setattr(distill_auto, "_tighten_events", lambda root: events)
    assert distill_auto._tightened_since(fx.root, 0) == ("S1_max 2621→2411", "S3_max_pct 40→38")
    assert distill_auto._tightened_since(fx.root, 2) == ()


def test_dry_run_report_is_labelled() -> None:
    r = dataclasses.replace(_report(), dry_run=True)
    assert "Ensayo (dry-run)" in rpt.report_md(r, "es")
    assert "Dry run: nothing was written" in rpt.report_md(r, "en")
    assert "dry-run" not in rpt.report_md(dataclasses.replace(r, dry_run=False), "es")


def test_report_agents_delta_has_explicit_sign_when_agents_grows() -> None:
    r = dataclasses.replace(_report(), agents_before=(100, 5), agents_after=(173, 9))
    md = rpt.report_md(r, "en")
    assert "(+73 B, +4 lines)" in md and "--" not in md.split("## AGENTS.md")[1].split("##")[0]


# --------------------------------------------------------------------------- the batch (T7)


class TripBrake(distill_auto.Brake):
    """Set from the ``after``-th check on (1-based)."""

    def __init__(self, after: int) -> None:
        super().__init__()
        self.after, self.calls = after, 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls >= self.after or super().is_set()


def auto(
    fx: Fixture,
    *,
    skip: tuple[str, ...] = (),
    dry_run: bool = False,
    brake: distill_auto.Brake | None = None,
) -> distill_auto.AutoOutcome:
    return distill_auto.run(
        fx.project,
        CFG,
        RID,
        distill_auto.AutoOptions(skip=skip, dry_run=dry_run),
        FakePrompter(tty=False),
        now=NOW,
        lang="es",
        brake=brake,
        signals=False,
    )


def _head(fx: Fixture, ref: str = "HEAD") -> str:
    return git(fx.root, "rev-parse", ref).strip()


def _not_ready(fx: Fixture, card: str) -> None:
    fill(fx, card, ["Regla inventada sin anclas para la prueba."])


# 1
def test_full_batch_applies_ready_cards_one_commit_each(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    _not_ready(fx, "D-003")
    main = _head(fx, "main")
    out = auto(fx)
    assert out.code == 0, [i.key for i in out.issues]
    b = out.batch
    assert b is not None and b.state == "done"
    assert [c.card for c in b.cards if c.result == "applied"] == ["D-001", "D-002"]
    log = git(fx.root, "log", "--format=%B", f"{db.batch_tag(RID, 1)}..HEAD")
    assert log.count(f"Declutter-Distill-Batch: {RID}/1") == 2
    assert _head(fx) != main and _head(fx, "main") == main
    assert git(fx.root, "branch", "--show-current").strip() == db.batch_branch(RID, 1)
    assert verify(fx, strict=False)[0]
    assert out.report is not None and [c for c, _r in out.report.not_ready] == ["D-003"]
    assert (db.batch_dir(fx.root, RID, 1) / "informe.md").is_file()
    assert not db.lock_path(fx.root, RID).exists()


# 2
def test_nothing_to_apply_creates_nothing(fx: Fixture) -> None:
    out = auto(fx)
    assert out.code == 0 and out.batch is None
    assert git(fx.root, "branch", "--list", "declutter/distill-auto-*") == ""
    assert git(fx.root, "tag", "--list", "declutter/pre-distill-auto-*") == ""


# 3
def test_skip_leaves_the_card_out_and_reports_it(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    out = auto(fx, skip=("D-002",))
    assert out.code == 0
    assert out.batch is not None and out.batch.skipped_by_user == ("D-002",)
    assert [c.card for c in out.batch.cards] == ["D-001"]
    assert out.report is not None and out.report.user_skipped == ("D-002",)


def test_skip_of_an_unknown_card_exits_1(fx: Fixture) -> None:
    fill(fx, "D-001")
    out = auto(fx, skip=("D-099",))
    assert out.code == 1 and out.batch is None
    assert "distill.auto.skip_unknown" in {i.key for i in out.issues}


# 4
def test_card_failing_on_disk_is_restored_skipped_and_batch_goes_on(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    real = distill_apply._scope

    def scope(ctx: object, outputs: object) -> list[Issue]:
        if getattr(ctx, "card", "") == "D-001":
            return [Issue(code=4, key="distill.outside_outputs", dest="x")]
        return real(ctx, outputs)  # type: ignore[arg-type]

    monkeypatch.setattr(distill_apply, "_scope", scope)
    out = auto(fx)
    assert out.code == 4
    assert out.batch is not None
    assert [(c.card, c.result) for c in out.batch.cards] == [
        ("D-001", "skipped"),
        ("D-002", "applied"),
    ]
    assert verify(fx, strict=False)[0]


# 5
def test_secret_in_a_card_never_starts(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002", ["- Nunca subas AKIAQ7J2M9X4K8L1P3R6 a ningún lado."])
    out = auto(fx)
    assert out.code == 5 and out.batch is None
    assert git(fx.root, "branch", "--list", "declutter/distill-auto-*") == ""


def test_secret_appearing_between_cards_halts_with_5(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    real = distill_apply.apply_batch_card

    def wrapped(*a: object, **kw: object) -> distill_apply.ApplyOutcome:
        res = real(*a, **kw)  # type: ignore[arg-type]
        if a[3] == "D-001":
            fill(fx, "D-002", ["- Nunca subas AKIAQ7J2M9X4K8L1P3R6 a ningún lado."])
        return res

    monkeypatch.setattr(distill_apply, "apply_batch_card", wrapped)
    out = auto(fx)
    assert out.code == 5
    assert out.batch is not None and out.batch.stop_key is not None
    assert [(c.card, c.result) for c in out.batch.cards] == [
        ("D-001", "applied"),
        ("D-002", "failed"),
    ]


# 6
def test_ratchet_without_room_exits_3_and_creates_nothing(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fail = SimpleNamespace(code=3, path="AGENTS.md", rule="R1", detail="S1_max")
    monkeypatch.setattr(ratchet, "check", lambda *a, **kw: SimpleNamespace(code=3, failures=[fail]))
    out = auto(fx)
    assert out.code == 3 and out.batch is None
    assert "distill.auto.ratchet_human" in {i.key for i in out.issues}
    assert git(fx.root, "tag", "--list", "declutter/pre-distill-auto-*") == ""


def test_brake_before_the_first_card_stops_with_130(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    out = auto(fx, brake=TripBrake(1))
    assert out.code == 130
    assert out.batch is not None and out.batch.state == "stopped" and out.batch.cards == ()
    assert not db.lock_path(fx.root, RID).exists()


def test_a_live_lock_refuses_a_new_batch(fx: Fixture) -> None:
    fill(fx, "D-001")
    db.acquire(fx.root, RID, 1, "x")
    try:
        out = auto(fx)
    finally:
        db.release(fx.root, RID)
    assert out.code == 1 and "distill.auto.locked" in {i.key for i in out.issues}
    assert git(fx.root, "branch", "--list", "declutter/distill-auto-*") == ""


# 15
def test_stale_ready_from_the_first_pass_is_not_trusted(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    _not_ready(fx, "D-003")
    real = distill.check

    def forged(*a: object, **kw: object) -> distill.CheckReport:
        rep = real(*a, **kw)  # type: ignore[arg-type]
        cards = tuple(
            dataclasses.replace(c, state="ready", reasons=()) if c.card_id == "D-003" else c
            for c in rep.cards
        )
        return dataclasses.replace(rep, cards=cards, code=0)

    monkeypatch.setattr(distill, "check", forged)
    out = auto(fx)
    assert out.batch is not None
    results = {c.card: c.result for c in out.batch.cards}
    assert results.get("D-003") == "skipped" and out.code == 4


# 18
def test_report_has_no_byte_of_rules(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    auto(fx)
    folder = db.batch_dir(fx.root, RID, 1)
    md = (folder / "informe.md").read_text()
    js = (folder / "informe.json").read_text()
    assert json.loads(js)["applied"]
    for token in ("password", "ZQX7731", "rota", "token tres", "Regla sintética", "docs/pub"):
        assert token not in md, token
        assert token not in js, token


# --------------------------------------------------------------------------- review of T7


def _forge_ready(monkeypatch: pytest.MonkeyPatch, card: str) -> None:
    real = distill.check

    def forged(*a: object, **kw: object) -> distill.CheckReport:
        rep = real(*a, **kw)  # type: ignore[arg-type]
        cards = tuple(
            dataclasses.replace(c, state="ready", reasons=()) if c.card_id == card else c
            for c in rep.cards
        )
        return dataclasses.replace(rep, cards=cards, code=0)

    monkeypatch.setattr(distill, "check", forged)


def _batch_json(fx: Fixture, n: int = 1) -> dict[str, object]:
    raw = (db.batch_dir(fx.root, RID, n) / db.BATCH_NAME).read_text()
    obj: dict[str, object] = json.loads(raw)
    return obj


def test_old_committed_journal_of_another_batch_is_skipped_not_halted(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    assert auto(fx).code == 0
    git(fx.root, "revert", "--no-edit", "HEAD")
    fill(fx, "D-002")
    _forge_ready(monkeypatch, "D-001")  # not_ready on disk, its journal committed in batch 1
    out = auto(fx)
    assert out.batch is not None and out.batch.batch == 2
    assert [(c.card, c.result) for c in out.batch.cards] == [
        ("D-001", "skipped"),
        ("D-002", "applied"),
    ]
    assert out.code == 4 and out.batch.stop_key is None


def test_incomplete_rollback_halts_the_batch(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    real_scope, real_restore = distill_apply._scope, distill_apply.restore

    def scope(ctx: object, outputs: object) -> list[Issue]:
        if getattr(ctx, "card", "") == "D-001":
            return [Issue(code=4, key="distill.outside_outputs", dest="x")]
        return real_scope(ctx, outputs)  # type: ignore[arg-type]

    def restore(*a: object, **kw: object) -> tuple[str, ...]:
        real_restore(*a, **kw)  # type: ignore[arg-type]
        return ("AGENTS.md",)

    monkeypatch.setattr(distill_apply, "_scope", scope)
    monkeypatch.setattr(distill_apply, "restore", restore)
    out = auto(fx)
    assert out.code != 0
    assert out.batch is not None and out.batch.stop_key == "restore_untouched"
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "failed")]


def test_an_exception_closes_the_batch_interrupted_and_frees_the_lock(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")

    def boom(*a: object, **kw: object) -> ApplyOutcome:
        raise OSError("disk")

    monkeypatch.setattr(distill_apply, "apply_batch_card", boom)
    with pytest.raises(OSError):
        auto(fx)
    obj = _batch_json(fx)
    assert obj["state"] == "interrupted" and obj["stop_key"] == "exception:OSError"
    assert not db.lock_path(fx.root, RID).exists()


class _Death(BaseException):
    pass


def test_a_base_exception_leaves_running_and_the_lock(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")

    def die(*a: object, **kw: object) -> ApplyOutcome:
        raise _Death

    monkeypatch.setattr(distill_apply, "apply_batch_card", die)
    with pytest.raises(_Death):
        auto(fx)
    assert _batch_json(fx)["state"] == "running"
    assert db.lock_path(fx.root, RID).exists()


def test_the_lock_is_released_before_the_final_batch_write(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    real = db.write_batch

    def write(root: object, b: db.Batch) -> None:
        if b.state == "done":
            raise OSError("disk")
        real(root, b)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "write_batch", write)
    with pytest.raises(OSError):
        auto(fx)
    assert not db.lock_path(fx.root, RID).exists()


def test_an_orphan_lock_of_a_dead_pid_is_reported_and_kept(fx: Fixture) -> None:
    fill(fx, "D-001")
    assert auto(fx).code == 0
    path = db.lock_path(fx.root, RID)
    path.write_text(json.dumps({"pid": 999_999_999, "started": "x", "batch": 1}))
    fill(fx, "D-002")
    out = auto(fx)
    assert out.code == 1 and out.batch is None
    issue = next(i for i in out.issues if i.key == "distill.auto.locked")
    assert issue.detail == "pid 999999999 dead; rm .declutter/distill/" + RID + "/auto.lock"
    assert path.exists()


def test_tighten_events_are_not_reported_when_the_first_read_failed(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    monkeypatch.setattr(distill_auto, "_tighten_count", lambda root: None)
    out = auto(fx)
    assert out.code == 0 and out.report is not None and out.report.tightened == ()


def test_p2_halt_keeps_the_verify_rev_failed_issue(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    real = distill_apply.verify_run
    bad = ProofResult(
        ok=False,
        mode="posterior",
        failures=(ProofFailure("C2", None, None, None, "AGENTS.md", None, "x"),),
    )

    def verify_run(*a: object, **kw: object) -> ProofResult:
        if kw.get("rev") == "HEAD":
            return bad
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(distill_apply, "verify_run", verify_run)
    out = auto(fx)
    assert out.code == 4
    assert out.batch is not None and out.batch.stop_key == "verify_rev_failed"
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "failed")]
    assert [i.key for i in out.issues] == ["distill.verify_rev_failed"]
    assert out.issues[0].detail


def test_a_ratchet_failure_mid_batch_halts_with_3(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    state = {"after_first": False}
    real_check, real_apply = ratchet.check, distill_apply.apply_batch_card
    fail = SimpleNamespace(code=3, path="AGENTS.md", rule="R1", detail="S1_max")

    def check(*a: object, **kw: object) -> object:
        if state["after_first"]:
            return SimpleNamespace(code=3, failures=[fail])
        return real_check(*a, **kw)  # type: ignore[arg-type]

    def apply(*a: object, **kw: object) -> ApplyOutcome:
        res = real_apply(*a, **kw)  # type: ignore[arg-type]
        state["after_first"] = True
        return res

    monkeypatch.setattr(ratchet, "check", check)
    monkeypatch.setattr(distill_apply, "apply_batch_card", apply)
    out = auto(fx)
    assert out.code == 3
    assert out.batch is not None and out.batch.stop_key == "ratchet"
    assert [(c.card, c.result) for c in out.batch.cards] == [
        ("D-001", "applied"),
        ("D-002", "failed"),
    ]


def test_concurrent_edit_halts_with_4(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")

    def apply(*a: object, **kw: object) -> ApplyOutcome:
        issue = Issue(code=4, key="concurrent_edit", dest="AGENTS.md")
        return ApplyOutcome(code=4, issues=(issue,), journal=None)

    monkeypatch.setattr(distill_apply, "apply_batch_card", apply)
    out = auto(fx)
    assert out.code == 4
    assert out.batch is not None and out.batch.stop_key == "concurrent_edit"
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "failed")]


def test_an_existing_batch_ref_exits_1_with_the_commands(fx: Fixture) -> None:
    fill(fx, "D-001")
    git(fx.root, "branch", db.batch_branch(RID, 1))
    out = auto(fx)
    assert out.code == 1 and out.batch is None
    issue = next(i for i in out.issues if i.key == "ref_exists")
    assert f"git branch -D {db.batch_branch(RID, 1)}" in issue.detail
    assert not db.lock_path(fx.root, RID).exists()


def test_a_git_error_creating_the_branch_stops_and_frees_the_lock(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")

    def fail(root: object, branch: str) -> None:
        raise gitops.GitError("switch", 1)

    monkeypatch.setattr(gitops, "switch_create", fail)
    out = auto(fx)
    assert out.code == 2 and "git_error" in {i.key for i in out.issues}
    obj = _batch_json(fx)
    assert obj["state"] == "stopped" and obj["stop_key"] == "git_error"
    assert not db.lock_path(fx.root, RID).exists()


def test_p2_halt_with_a_secret_beside_it_exits_5(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    real_verify, real_apply = distill_apply.verify_run, distill_apply.apply_batch_card
    bad = ProofResult(
        ok=False,
        mode="posterior",
        failures=(ProofFailure("C2", None, None, None, "AGENTS.md", None, "x"),),
    )

    def verify_run(*a: object, **kw: object) -> ProofResult:
        if kw.get("rev") == "HEAD":
            return bad
        return real_verify(*a, **kw)  # type: ignore[arg-type]

    def apply(*a: object, **kw: object) -> ApplyOutcome:
        res = real_apply(*a, **kw)  # type: ignore[arg-type]
        secret = Issue(code=5, key="secret", dest="AGENTS.md")
        return ApplyOutcome(code=5, issues=(*res.issues, secret), journal=res.journal)

    monkeypatch.setattr(distill_apply, "verify_run", verify_run)
    monkeypatch.setattr(distill_apply, "apply_batch_card", apply)
    out = auto(fx)
    assert out.code == 5
    assert out.batch is not None and out.batch.stop_key == "verify_rev_failed"
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "failed")]


# --------------------------------------------------------------------------- brake and death (T8)


# 7
def test_brake_before_card_2_exits_130_with_card_1_applied(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    card2 = fx.card_path("D-002").read_bytes()
    # checks: card 1 before(1), point 2 (2), point 3 (3); card 2 before(4) -> set
    out = auto(fx, brake=TripBrake(4))
    assert out.code == 130
    assert out.batch is not None and out.batch.state == "stopped"
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "applied")]
    assert fx.card_path("D-002").read_bytes() == card2  # filled, untouched
    assert not db.lock_path(fx.root, RID).exists()


@pytest.mark.parametrize("signum", [sig.SIGINT, sig.SIGTERM])
def test_real_signal_after_card_1(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch, signum: sig.Signals
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    real = distill_apply.apply_batch_card

    def wrapped(*a: object, **kw: object) -> ApplyOutcome:
        res = real(*a, **kw)  # type: ignore[arg-type]
        if a[3] == "D-001":
            os.kill(os.getpid(), signum)
        return res

    monkeypatch.setattr(distill_apply, "apply_batch_card", wrapped)
    before_int, before_term = sig.getsignal(sig.SIGINT), sig.getsignal(sig.SIGTERM)
    out = distill_auto.run(
        fx.project,
        CFG,
        RID,
        distill_auto.AutoOptions(),
        FakePrompter(tty=False),
        now=NOW,
        lang="es",
        signals=True,
    )
    assert out.code == 130
    assert out.batch is not None
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "applied")]
    assert sig.getsignal(sig.SIGINT) is before_int
    assert sig.getsignal(sig.SIGTERM) is before_term


# 8
def test_brake_between_stage_and_backup(fx: Fixture) -> None:
    fill(fx, "D-001")
    before = tree(fx.root)
    out = auto(fx, brake=TripBrake(2))
    assert out.code == 130
    assert tree(fx.root) == before
    j = distill_apply.read_journal(distill_apply.distill_dir(fx.root, RID, "D-001"), fx.root)
    assert j is not None and j.state == "undone"
    assert out.batch is not None
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "stopped")]
    assert git(fx.root, "branch", "--show-current").strip() == db.batch_branch(RID, 1)
    assert _head(fx, db.batch_tag(RID, 1) + "^{commit}") == out.batch.base_commit == _head(fx)


# 9
def test_brake_during_write_completes_the_card(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    brake = distill_auto.Brake()
    real = distill_apply._write_all

    def write_all(ctx: object, b: object, j: object) -> object:
        brake.set()
        return real(ctx, b, j)  # type: ignore[arg-type]

    monkeypatch.setattr(distill_apply, "_write_all", write_all)
    out = auto(fx, brake=brake)
    assert out.code == 130
    assert out.batch is not None
    assert [(c.card, c.result) for c in out.batch.cards] == [("D-001", "applied")]


# 10
class _Killed(BaseException):
    pass


def test_sigkill_is_detected_and_undo_restores_everything(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    before = tree(fx.root)
    base = _branch(fx)
    real = distill_apply._write_all

    def dies(ctx: object, b: object, j: object) -> object:
        if getattr(ctx, "card", "") == "D-002":
            raise _Killed
        return real(ctx, b, j)  # type: ignore[arg-type]

    monkeypatch.setattr(distill_apply, "_write_all", dies)
    with pytest.raises(_Killed):
        auto(fx)
    monkeypatch.setattr(distill_apply, "_write_all", real)
    lock = db.lock_path(fx.root, RID)
    obj = json.loads(lock.read_text())
    obj["pid"] = 999_999_999
    lock.write_text(json.dumps(obj))
    out = auto(fx)
    assert out.code == 1
    issue = next(i for i in out.issues if i.key == "distill.auto.interrupted")
    assert "--auto 1" in issue.detail and "D-002" in issue.detail
    assert db.read_batch(fx.root, RID, 1).state == "interrupted"  # type: ignore[union-attr]
    res = distill_auto.undo_batch(
        fx.project, CFG, RID, 1, FakePrompter(tty=False), now=NOW, lang="es"
    )
    assert res.code == 0, [i.key for i in res.issues]
    after = tree(fx.root)
    cards = {str(fx.card_path(c).relative_to(fx.root)) for c in ("D-001", "D-002")}
    assert {k: v for k, v in after.items() if k not in cards} == {
        k: v for k, v in before.items() if k not in cards
    }
    assert _branch(fx) == base
    assert not lock.exists()


# 16
def test_live_lock_refuses(fx: Fixture) -> None:
    fill(fx, "D-001")
    db.acquire(fx.root, RID, 1, "2026-11-11T10:00:00")  # this very process: alive
    try:
        out = auto(fx)
    finally:
        db.release(fx.root, RID)
    assert out.code == 1 and "distill.auto.locked" in {i.key for i in out.issues}


# 17
def test_dry_run_creates_nothing_and_matches_a_real_run(fx: Fixture, tmp_path: Path) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    before = tree(fx.root)
    head, branch = _head(fx), git(fx.root, "branch", "--show-current")
    dry = auto(fx, dry_run=True)
    assert dry.code == 0, [i.key for i in dry.issues]
    assert dry.report is not None and dry.report.dry_run
    assert tree(fx.root) == before
    assert _head(fx) == head and git(fx.root, "branch", "--show-current") == branch
    for card in ("D-001", "D-002"):
        assert not (distill_apply.distill_dir(fx.root, RID, card) / "journal.json").exists()
    assert git(fx.root, "branch", "--list", "declutter/distill-auto-*") == ""
    assert git(fx.root, "tag", "--list", "declutter/pre-distill-auto-*") == ""
    assert not db.batch_dir(fx.root, RID, 1).exists()
    assert not db.lock_path(fx.root, RID).exists()
    copy = tmp_path / "copy"
    shutil.copytree(fx.root, copy, symlinks=True)
    real = distill_auto.run(
        dataclasses.replace(fx.project, root=copy, cwd=copy),
        CFG,
        RID,
        distill_auto.AutoOptions(),
        FakePrompter(tty=False),
        now=NOW,
        lang="es",
        signals=False,
    )
    assert real.report is not None

    def norm(r: rpt.Report) -> dict[str, object]:
        obj = dict(rpt.report_obj(r))
        obj["applied"] = [
            {k: v for k, v in dict(c).items() if k != "commit"}
            for c in obj["applied"]  # type: ignore[attr-defined]
        ]
        obj.pop("commands", None)
        obj.pop("dry_run", None)
        return obj

    assert norm(dry.report) == norm(real.report)


def test_dry_run_removes_its_temporary_copy(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fill(fx, "D-001")
    seen: list[Path] = []
    real_run = distill_auto._run_batch

    def spy(project: object, *a: object, **kw: object) -> distill_auto.AutoOutcome:
        seen.append(project.root)  # type: ignore[attr-defined]
        return real_run(project, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(distill_auto, "_run_batch", spy)
    assert auto(fx, dry_run=True).code == 0
    assert seen and seen[0] != fx.root and not seen[0].exists()


# --------------------------------------------------------------------------- dry-run isolation


def _repo_state(root: Path) -> tuple[str, str, str, str]:
    return (
        git(root, "rev-parse", "HEAD"),
        git(root, "branch", "--show-current"),
        git(root, "branch", "--list"),
        git(root, "tag", "--list"),
    )


def _worktree_fixture(fx: Fixture, tmp_path: Path) -> Fixture:
    wt = tmp_path / "wt"
    git(fx.root, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
    # run state is local (untracked): bring it along
    shutil.copytree(fx.root / ".declutter", wt / ".declutter", dirs_exist_ok=True)
    return Fixture(dataclasses.replace(fx.project, root=wt, cwd=wt), fx.lang)


def test_dry_run_in_a_worktree_never_touches_the_real_repo(fx: Fixture, tmp_path: Path) -> None:
    wfx = _worktree_fixture(fx, tmp_path)
    fill(wfx, "D-001")
    fill(wfx, "D-002")
    main_before, wt_before = _repo_state(fx.root), _repo_state(wfx.root)
    dry = auto(wfx, dry_run=True)
    assert dry.code == 0, [(i.key, i.detail) for i in dry.issues]
    assert dry.report is not None and len(dry.report.applied) == 2
    assert _repo_state(fx.root) == main_before
    assert _repo_state(wfx.root) == wt_before
    assert git(wfx.root, "branch", "--list", "declutter/distill-auto-*") == ""
    assert git(wfx.root, "tag", "--list", "declutter/pre-distill-auto-*") == ""


def test_dry_run_copy_of_a_subfolder_is_an_independent_repo(fx: Fixture, tmp_path: Path) -> None:
    # A full distill-auto run needs the fixture's own history (merge-base of the run's base
    # commit), so the subfolder case checks the copy itself rather than a whole batch.
    outer = tmp_path / "outer"
    sub = outer / "sub"
    shutil.copytree(fx.root, sub, ignore=shutil.ignore_patterns(".git"), symlinks=True)
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-q", "-m", "x"),
    ):
        git(outer, *args)
    before = _repo_state(outer)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    proj = dataclasses.replace(fx.project, root=sub, cwd=sub)
    copy = distill_auto._build_dry_copy(proj, tmp)
    assert copy.root == (tmp.resolve() / "copy" / "sub")
    assert (copy.root / "AGENTS.md").is_file()
    assert (tmp / "copy" / ".git").is_dir()
    gitops.tag(copy.root, "declutter/probe")
    gitops.switch_create(copy.root, "declutter/probe-branch")
    assert _repo_state(outer) == before
    assert git(copy.root, "branch", "--show-current").strip() == "declutter/probe-branch"


def test_dry_run_refuses_an_outward_symlink(fx: Fixture, tmp_path: Path) -> None:
    fill(fx, "D-001")
    outside = tmp_path / "outside"
    outside.mkdir()
    (fx.root / "salida").symlink_to(outside)
    before = tree(fx.root)
    out = auto(fx, dry_run=True)
    assert out.code == 1
    issue = next(i for i in out.issues if i.key == "distill.auto.dry_run_unsafe")
    assert issue.detail == "symlink salida"
    assert tree(fx.root) == before
    assert git(fx.root, "branch", "--list", "declutter/distill-auto-*") == ""


def test_dry_run_refuses_submodules(fx: Fixture) -> None:
    fill(fx, "D-001")
    (fx.root / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = ./x\n')
    out = auto(fx, dry_run=True)
    assert out.code == 1
    issue = next(i for i in out.issues if i.key == "distill.auto.dry_run_unsafe")
    assert issue.detail == "submodules"


def test_dry_run_refuses_a_huge_project(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(fx, "D-001")
    monkeypatch.setattr(distill_auto, "_DRY_MAX_BYTES", 10)
    out = auto(fx, dry_run=True)
    assert out.code == 1
    issue = next(i for i in out.issues if i.key == "distill.auto.dry_run_too_big")
    assert "distill-undo --auto" in issue.detail


def test_dry_run_copy_is_checked_to_be_independent(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    real = distill_auto._git_path

    def lie(root: Path, *args: str) -> Path:
        if "--git-common-dir" in args and root != fx.root:
            return fx.root / ".git"
        return real(root, *args)

    monkeypatch.setattr(distill_auto, "_git_path", lie)
    out = auto(fx, dry_run=True)
    assert out.code == 1 and "distill.auto.dry_run_unsafe" in {i.key for i in out.issues}
    assert git(fx.root, "branch", "--list", "declutter/distill-auto-*") == ""


def test_dry_run_refuses_a_link_inside_dot_git(fx: Fixture, tmp_path: Path) -> None:
    fill(fx, "D-001")
    outside = tmp_path / "refs-fuera"
    shutil.copytree(fx.root / ".git" / "refs", outside)
    shutil.rmtree(fx.root / ".git" / "refs")
    (fx.root / ".git" / "refs").symlink_to(outside)
    before = _repo_state(fx.root)
    refs_before = sorted(p.as_posix() for p in outside.rglob("*"))
    out = auto(fx, dry_run=True)
    assert out.code == 1
    issue = next(i for i in out.issues if i.key == "distill.auto.dry_run_unsafe")
    assert issue.detail == "symlink .git/refs"
    assert _repo_state(fx.root) == before
    assert sorted(p.as_posix() for p in outside.rglob("*")) == refs_before


# --------------------------------------------------------------------------- undo (T10)


def _branch(fx: Fixture) -> str:
    return git(fx.root, "branch", "--show-current").strip()


def _merge_into_base(fx: Fixture, base: str, n: int = 1) -> None:
    """Merge the batch into its base branch (the fixture's base is the reorg branch)."""
    git(fx.root, "switch", "-q", base)
    git(fx.root, "merge", "-q", "--no-ff", "-m", "merge batch", db.batch_branch(RID, n))


def _undo(
    fx: Fixture, n: int = 1, prompter: FakePrompter | None = None
) -> distill_auto.AutoOutcome:
    return distill_auto.undo_batch(
        fx.project, CFG, RID, n, prompter or FakePrompter(tty=False), now=NOW, lang="es"
    )


# 11
def test_undo_unmerged_goes_back_to_base_and_keeps_refs(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    before = tree(fx.root)
    base = _branch(fx)
    assert auto(fx).code == 0
    out = _undo(fx)
    assert out.code == 0, [i.key for i in out.issues]
    assert _branch(fx) == base
    # M2: the filled cards are back too (byte for byte), so the whole tree is as before.
    assert tree(fx.root) == before
    assert git(fx.root, "branch", "--list", db.batch_branch(RID, 1)).strip()
    assert git(fx.root, "tag", "--list", db.batch_tag(RID, 1)).strip()
    assert db.read_batch(fx.root, RID, 1).state == "undone"  # type: ignore[union-attr]


def test_undo_restores_filled_cards_so_skip_does_not_refill(fx: Fixture) -> None:
    c1, c2 = fill(fx, "D-001"), fill(fx, "D-002")
    filled = {c: c.read_bytes() for c in (c1, c2)}
    assert auto(fx).code == 0
    said = FakePrompter(tty=False)
    assert _undo(fx, prompter=said).code == 0
    for c, data in filled.items():
        assert c.read_bytes() == data
    assert git(fx.root, "diff", "--name-only").split() == sorted(
        str(c.relative_to(fx.root)) for c in (c1, c2)
    )  # uncommitted modifications, as before the batch
    assert not any("D-00" in m and "restaur" in m for m in said.said)
    out = auto(fx, skip=("D-002",))  # no refill needed
    assert out.code == 0, [i.key for i in out.issues]
    assert out.batch is not None
    assert [c.card for c in out.batch.cards if c.result == "applied"] == ["D-001"]


def test_undo_merged_restores_filled_cards(fx: Fixture) -> None:
    c1, c2 = fill(fx, "D-001"), fill(fx, "D-002")
    filled = {c: c.read_bytes() for c in (c1, c2)}
    base = _branch(fx)
    assert auto(fx).code == 0
    _merge_into_base(fx, base)
    out = _undo(fx, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 0, [i.key for i in out.issues]
    for c, data in filled.items():
        assert c.read_bytes() == data


def test_undo_does_not_overwrite_a_card_edited_by_hand(fx: Fixture) -> None:
    c1, c2 = fill(fx, "D-001"), fill(fx, "D-002")
    filled2 = c2.read_bytes()
    base = _branch(fx)
    assert auto(fx).code == 0
    b = db.read_batch(fx.root, RID, 1)
    assert b is not None
    ours = distill_auto._ours(fx.root, RID, b)
    git(fx.root, "switch", "-q", base)  # the cards are the base's (empty) ones again
    c1.write_bytes(c1.read_bytes() + b"nota a mano\n")
    edited = c1.read_bytes()
    said = FakePrompter(tty=False)
    distill_auto._restore_cards(fx.root, RID, ours, said, "es")
    assert c1.read_bytes() == edited
    assert c2.read_bytes() == filled2
    assert len(said.said) == 1 and "D-001" in said.said[0]
    assert "nota a mano" not in said.said[0]


# 12
def test_undo_merged_needs_tty_then_reverts(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    base = _branch(fx)
    assert auto(fx).code == 0  # both filled cards are now committed on the batch branch
    _merge_into_base(fx, base)
    assert _undo(fx).code == 1  # no TTY
    no = _undo(fx, prompter=FakePrompter(tty=True, answers=["no"]))
    assert no.code == 1 and "distill.auto.not_confirmed" in {i.key for i in no.issues}
    out = _undo(fx, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 0, [i.key for i in out.issues]
    assert verify(fx)[0]
    assert git(fx.root, "log", "-2", "--format=%s").count("Revert") == 2
    assert db.read_batch(fx.root, RID, 1).state == "undone"  # type: ignore[union-attr]
    assert not db.lock_path(fx.root, RID).exists()


def test_undo_of_a_missing_batch_exits_1(fx: Fixture) -> None:
    out = _undo(fx, n=7)
    assert out.code == 1 and [i.key for i in out.issues] == ["distill.auto.no_batch"]


def test_undo_merged_refuses_a_staged_foreign_change(fx: Fixture) -> None:
    fill(fx, "D-001")
    base = _branch(fx)
    assert auto(fx).code == 0
    _merge_into_base(fx, base)
    (fx.root / "ajeno.txt").write_text("x\n")
    git(fx.root, "add", "ajeno.txt")
    head = _head(fx)
    out = _undo(fx, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 1 and "index_not_clean" in {i.key for i in out.issues}
    assert _head(fx) == head


def test_undo_refuses_while_a_later_batch_is_applied(fx: Fixture) -> None:
    fill(fx, "D-001")
    base = _branch(fx)
    assert auto(fx).code == 0
    _merge_into_base(fx, base)
    fill(fx, "D-002")
    assert auto(fx).code == 0
    out = _undo(fx, n=1, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 1 and "distill.lifo" in {i.key for i in out.issues}
    assert _undo(fx, n=2).code == 0


# --------------------------------------------------------------------------- review of T10


def _kill_lock(fx: Fixture) -> None:
    lock = db.lock_path(fx.root, RID)
    obj = json.loads(lock.read_text())
    obj["pid"] = 999_999_999
    lock.write_text(json.dumps(obj))


def test_death_right_after_the_commit_is_reconciled_and_undone(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    before = tree(fx.root)
    base = _branch(fx)
    real = gitops.commit_only

    def commits_then_dies(root: Path, subject: str, body: str, paths: list[str]) -> str:
        sha = real(root, subject, body, paths)
        if "/D-002 " in body:
            raise _Killed
        return sha

    monkeypatch.setattr(gitops, "commit_only", commits_then_dies)
    with pytest.raises(_Killed):
        auto(fx)
    monkeypatch.setattr(gitops, "commit_only", real)
    j = distill_apply.read_journal(distill_apply.distill_dir(fx.root, RID, "D-002"), fx.root)
    assert j is not None and j.state == "written" and j.commit is None
    _kill_lock(fx)
    out = _undo(fx)
    assert out.code == 0, [i.key for i in out.issues]
    assert _branch(fx) == base
    cards = {str(fx.card_path(c).relative_to(fx.root)) for c in ("D-001", "D-002")}
    after = tree(fx.root)
    assert {k: v for k, v in after.items() if k not in cards} == {
        k: v for k, v in before.items() if k not in cards
    }
    b = db.read_batch(fx.root, RID, 1)
    assert b is not None and b.state == "undone"
    assert [c.card for c in b.cards if c.result == "applied"] == ["D-001", "D-002"]
    assert not db.lock_path(fx.root, RID).exists()


def test_a_conflict_mid_revert_leaves_the_base_head_unchanged(fx: Fixture) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    base = _branch(fx)
    assert auto(fx).code == 0
    _merge_into_base(fx, base)
    card = fx.card_path("D-001")  # touched by D-001 only; D-002's revert goes first
    card.write_text("Edición ajena en la base que reescribe toda la ficha.\n")
    git(fx.root, "commit", "-q", "-am", "edición ajena")
    head = _head(fx)
    out = _undo(fx, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 1 and "revert_conflict" in {i.key for i in out.issues}
    assert _head(fx) == head and _branch(fx) == base
    assert gitops.operation_in_progress(fx.root) is None
    assert db.read_batch(fx.root, RID, 1).state == "done"  # type: ignore[union-attr]


def test_undo_merged_refuses_an_operation_in_progress(fx: Fixture) -> None:
    fill(fx, "D-001")
    base = _branch(fx)
    assert auto(fx).code == 0
    _merge_into_base(fx, base)
    (fx.root / ".git" / "MERGE_HEAD").write_text(_head(fx) + "\n")
    head = _head(fx)
    out = _undo(fx, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 1 and "operation_in_progress" in {i.key for i in out.issues}
    assert _head(fx) == head


def test_undo_merged_refuses_when_the_base_moved_during_the_question(fx: Fixture) -> None:
    fill(fx, "D-001")
    base = _branch(fx)
    assert auto(fx).code == 0
    _merge_into_base(fx, base)

    class Mover(FakePrompter):
        def ask(self, prompt: str) -> str:
            git(fx.root, "commit", "-q", "--allow-empty", "-m", "otro proceso")
            return "confirmo"

    moved = _head(fx)
    out = _undo(fx, prompter=Mover(tty=True))
    assert out.code == 1 and "distill.auto.head_moved" in {i.key for i in out.issues}
    assert git(fx.root, "log", "-1", "--format=%s").strip() == "otro proceso"
    assert git(fx.root, "rev-parse", "HEAD~1").strip() == moved


def test_undo_unmerged_refuses_from_another_branch(fx: Fixture) -> None:
    fill(fx, "D-001")
    assert auto(fx).code == 0
    git(fx.root, "switch", "-q", "-c", "otra")
    out = _undo(fx)
    assert out.code == 1 and [(i.key, i.detail) for i in out.issues] == [("wrong_branch", "otra")]
    assert _branch(fx) == "otra"
    assert db.read_batch(fx.root, RID, 1).state == "done"  # type: ignore[union-attr]


def test_undo_keeps_a_foreign_lock_of_a_live_process(fx: Fixture) -> None:
    fill(fx, "D-001")
    assert auto(fx).code == 0
    b = db.read_batch(fx.root, RID, 1)
    assert b is not None
    db.acquire(fx.root, RID, 2, "2026-11-12T00:00:00")
    distill_auto._mark_undone(fx.root, RID, b)
    assert db.lock_path(fx.root, RID).exists()


def test_restore_untracked_only_touches_what_the_batch_left(fx: Fixture) -> None:
    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    folder = fx.root / ".declutter" / "distill" / RID / "D-001"
    (folder / "backup").mkdir(parents=True, exist_ok=True)
    (folder / "backup" / "local.dat").write_bytes(b"antes\n")
    firsts = {"local.dat": (folder, sha(b"antes\n"))}
    lasts = {"local.dat": sha(b"del lote\n")}
    local = fx.root / "local.dat"
    local.write_bytes(b"ajeno\n")
    issues = distill_auto._restore_untracked(fx.root, firsts, lasts, set())
    assert [(i.key, i.dest) for i in issues] == [("restore_untouched", "local.dat")]
    assert local.read_bytes() == b"ajeno\n"
    local.write_bytes(b"del lote\n")
    assert distill_auto._restore_untracked(fx.root, firsts, lasts, set()) == []
    assert local.read_bytes() == b"antes\n"


def _die_after_commit_of_d002(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    real = gitops.commit_only

    def commits_then_dies(root: Path, subject: str, body: str, paths: list[str]) -> str:
        sha = real(root, subject, body, paths)
        if "/D-002 " in body:
            raise _Killed
        return sha

    monkeypatch.setattr(gitops, "commit_only", commits_then_dies)
    with pytest.raises(_Killed):
        auto(fx)
    monkeypatch.setattr(gitops, "commit_only", real)
    _kill_lock(fx)


def test_reconcile_refuses_a_manual_commit_that_copies_the_trailers(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    _die_after_commit_of_d002(fx, monkeypatch)
    (fx.root / "manual.txt").write_text("trabajo del usuario\n")
    git(fx.root, "add", "manual.txt")
    git(fx.root, "commit", "-q", "-C", "HEAD")
    manual = _head(fx)
    out = _undo(fx)
    assert out.code == 1
    issue = next(i for i in out.issues if i.key == "distill.auto.reconcile_refused")
    assert issue.detail == manual[:12]
    assert (fx.root / "manual.txt").read_text() == "trabajo del usuario\n"
    assert _head(fx) == manual
    b = db.read_batch(fx.root, RID, 1)
    assert b is not None and [c.card for c in b.cards if c.result == "applied"] == ["D-001"]
    j = distill_apply.read_journal(distill_apply.distill_dir(fx.root, RID, "D-002"), fx.root)
    assert j is not None and j.state == "written" and j.commit is None


def test_an_interrupted_and_merged_batch_is_reconciled_and_reverted_clean(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-001")
    fill(fx, "D-002")
    base = _branch(fx)
    _die_after_commit_of_d002(fx, monkeypatch)
    _merge_into_base(fx, base)
    out = _undo(fx, prompter=FakePrompter(tty=True, answers=["confirmo"]))
    assert out.code == 0, [i.key for i in out.issues]
    assert _branch(fx) == base
    # Clean apart from the two filled cards, which are back as uncommitted modifications.
    dirty = git(fx.root, "status", "--porcelain", "--untracked-files=no").split("\n")
    assert sorted(x[3:] for x in dirty if x) == sorted(
        str(fx.card_path(c).relative_to(fx.root)) for c in ("D-001", "D-002")
    )
    assert git(fx.root, "log", "-2", "--format=%s").count("Revert") == 2
    b = db.read_batch(fx.root, RID, 1)
    assert b is not None and b.state == "undone"
    assert [c.card for c in b.cards if c.result == "applied"] == ["D-001", "D-002"]
