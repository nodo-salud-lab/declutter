"""distill_apply: ``declutter reorg distill-apply`` / ``distill-undo`` over a synthetic run (fase
2b §6-§10, §15.1 items 12-19). Every project is synthetic (``_distill_helpers``)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from _distill_helpers import (
    INV_ES,
    NOW,
    RID,
    SEED,
    Fixture,
    applied,
    fill,
    git,
)
from conftest import MakeProject
from declutter_context import fsutil, ratchet
from declutter_context.model import Config, Project
from declutter_context.reorg import distill, distill_apply, mdblocks
from declutter_context.reorg.lexicon import build_lexicon
from declutter_context.reorg.undo import verify_run
from test_apply import FakePrompter

LEX = build_lexicon({})
CFG = Config()
OUTPUTS_ALL = (
    "AGENTS.md",
    "src/AGENTS.md",
    "docs/decisiones/INDICE.md",
    ".declutter/pins.json",
    ".declutter/ratchet.log",
    ".declutter/ratchet.json",
)


def ok_prompter(reason: str = "motivo sintético", word: str = "confirmo") -> FakePrompter:
    return FakePrompter(tty=True, answers=[reason, word, "n"])


def distill_card(
    fx: Fixture,
    card: str,
    *,
    prompter: FakePrompter | None = None,
    commit: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    no_git: bool = False,
    fill_first: bool = True,
) -> distill_apply.ApplyOutcome:
    """Fill the card like the skill (when it is still pending), then distill it."""
    if fill_first and "\nPENDIENTE\n" in fx.card_path(card).read_text():
        fill(fx, card)
    opts = distill_apply.DistillOptions(commit=commit, yes=yes, dry_run=dry_run, no_git=no_git)
    return distill_apply.apply(
        fx.project,
        CFG,
        RID,
        card,
        opts,
        prompter or ok_prompter(),
        now=NOW,
        lang="es",
    )


def undo_card(fx: Fixture, card: str, *, yes: bool = True) -> distill_apply.ApplyOutcome:
    return distill_apply.undo(
        fx.project, CFG, RID, card, FakePrompter(tty=False), yes=yes, now=NOW, lang="es"
    )


def tree(root: Path) -> dict[str, str]:
    """sha of every project file except ``.git/`` and the local ``.declutter/`` folders."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if ".git" in rel.parts or not p.is_file():
            continue
        if rel.parts[:2] in ((".declutter", "reorg"), (".declutter", "distill")):
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def verify(fx: Fixture, *, strict: bool = False, rev: str | None = None) -> tuple[bool, set[str]]:
    res = verify_run(fx.project, CFG, RID, rev=rev, strict=strict)
    return res.ok, {s for _k, s in res.states}


def ratchet_code(fx: Fixture) -> int:
    return ratchet.check(fx.project, CFG, staged=False).code


def events(fx: Fixture) -> list[dict[str, object]]:
    raw = (fx.root / ratchet.LOG_REL).read_bytes()
    return [dict(e) for e in ratchet.log_events(raw)]


def pins(fx: Fixture) -> tuple[ratchet.Pin, ...]:
    return ratchet.pins_loads((fx.root / ratchet.PINS_REL).read_bytes())


@pytest.fixture
def fx(make_project: MakeProject) -> Fixture:
    """An applied run whose cards are still pending: each test fills the cards it distills
    (a filled card that is not distilled is ``generated_edited`` under ``--strict``)."""
    return applied(make_project)


def _keys(res: distill_apply.ApplyOutcome) -> set[str]:
    return {i.key for i in res.issues}


# --------------------------------------------------------------------------- happy path


def _expected_agents(fx: Fixture, before: bytes, card: str) -> bytes:
    """AGENTS.md after distilling ``card`` as the first card, built here from §6.1-§6.2."""
    rep = distill.check(fx.root, RID, card=card, lexicon=LEX)
    cc = rep.cards[0]
    assert cc.entry is not None
    lines = list(mdblocks.split_lines(before).lines)
    drop: set[int] = set()
    for c in cc.copies:
        assert c.at is not None
        drop.update(range(c.at[0] - 2, c.at[1] + 1))  # 0-based header .. blank
    end = lines.index(b"<!-- declutter:gen-end invariants -->")
    out: list[bytes] = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        out.append(line)
        if i == end:
            out += [
                b"",
                b"## Invariantes de seguridad (destiladas)",
                f"<!-- declutter:gen invariants-distilled {RID} -->".encode(),
                *cc.entry.lines,
                b"<!-- declutter:gen-end invariants-distilled -->",
            ]
    return b"".join(x + b"\n" for x in out)


def test_happy_path_three_copies(fx: Fixture) -> None:
    fill(fx, "D-002")
    root = fx.root
    before = tree(root)
    agents0 = (root / "AGENTS.md").read_bytes()
    expected = _expected_agents(fx, agents0, "D-002")
    pins0 = pins(fx)
    log0 = (root / ratchet.LOG_REL).read_bytes()
    state0 = ratchet.ratchet_loads((root / ratchet.RATCHET_REL).read_bytes())
    card = fx.card_path("D-002")
    res = distill_card(fx, "D-002")
    assert res.code == 0, res.issues
    assert res.journal is not None and res.journal.state == "written"
    agents = (root / "AGENTS.md").read_bytes()
    assert agents == expected
    assert agents.count("### ⟨U03.b2, U03.b3, U03.b4⟩ D-002 → ".encode()) == 1
    assert b"\n### \xe2\x9f\xa8U03.b2\xe2\x9f\xa9 \xe2\x86\x92" not in agents  # literal header gone
    text = card.read_text()
    assert text.split("\n")[1] == "estado: destilada"
    index = (root / "docs/decisiones/INDICE.md").read_text()
    assert "| D-002 | Sesión 2026-10-06 | destilada | [ficha](" in index
    new_pins = pins(fx)
    assert new_pins[: len(pins0)] == pins0
    assert [p.role for p in new_pins[len(pins0) :]] == ["invariant", "card"]
    log = (root / ratchet.LOG_REL).read_bytes()
    assert log.startswith(log0)
    kinds = [e["event"] for e in events(fx)]
    assert kinds.count("baseline") == 1
    assert "distill" in kinds
    state = ratchet.ratchet_loads((root / ratchet.RATCHET_REL).read_bytes())
    assert state.s1_max <= state0.s1_max and state.s3_max_pct <= state0.s3_max_pct
    if state != state0:
        assert kinds[-1] == "tighten"
    else:
        assert "tighten" not in kinds
    # C8 of the distillation: nothing else changed.
    after = tree(root)
    changed = {k for k in after if before.get(k) != after[k]}
    assert changed <= {"AGENTS.md", str(card.relative_to(root)), *OUTPUTS_ALL}
    assert "ESTADO.md" not in changed and "CLAUDE.md" not in changed
    assert git(root, "branch", "--show-current").strip() == f"declutter/distill-{RID}-D-002"
    assert git(root, "tag", "--list", f"declutter/pre-distill-{RID}-D-002").strip()
    # Base branch untouched.
    assert git(root, "show", f"declutter/reorg-{RID}:AGENTS.md").encode() == agents0


@pytest.mark.parametrize("card", ["D-001", "D-003"])
def test_a_one_line_card_alone_passes_r1(fx: Fixture, card: str) -> None:
    """Enmienda 05-11: an entry has the lines of a copy, so only the one-time region
    (blank, title, marker, closing marker: 4 lines) is added, within the slack of 5."""
    lines0 = len((fx.root / "AGENTS.md").read_bytes().split(b"\n"))
    res = distill_card(fx, card, commit=True)
    assert res.code == 0, res.issues
    assert len((fx.root / "AGENTS.md").read_bytes().split(b"\n")) == lines0 + 4
    assert ratchet_code(fx) == 0
    assert verify(fx, strict=True)[0]


def test_successive_distills_after_tighten_pass_r1(fx: Fixture) -> None:
    sizes = []
    for card in ("D-001", "D-003", "D-002"):
        res = distill_card(fx, card, commit=True)
        assert res.code == 0, (card, res.issues)
        assert ratchet_code(fx) == 0
        sizes.append(len((fx.root / "AGENTS.md").read_bytes().split(b"\n")))
    assert sizes[1] == sizes[0]  # a one-line copy and its entry have the same lines
    assert sizes[2] == sizes[1] - 4  # three copies (9 lines) -> one 5-line entry
    state = ratchet.ratchet_loads((fx.root / ratchet.RATCHET_REL).read_bytes())
    assert state.s1_max <= 57
    assert verify(fx, strict=True)[0]


def test_root_and_zone_pointers_become_distilled(fx: Fixture) -> None:
    res = distill_card(fx, "D-003")
    assert res.code == 0, res.issues
    agents = (fx.root / "AGENTS.md").read_text()
    zone = (fx.root / "src/AGENTS.md").read_text()
    assert "D-003-contrato-sintetico-2026-10-08.md (destilada)" in agents
    assert "(pendiente de destilar)" not in agents.split("## Reglas vigentes")[1]
    assert "D-003-contrato-sintetico-2026-10-08.md (destilada)" in zone


def test_verify_and_ratchet_after_distill(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    ok, states = verify(fx)
    assert ok, states
    assert {"distilled", "card_distilled", "control_appended"} <= states
    assert verify(fx, strict=True)[0]
    assert verify(fx, rev="HEAD")[0]
    assert ratchet_code(fx) == 0
    rep = distill.check(fx.root, RID, card="D-002", lexicon=LEX)
    assert rep.cards[0].state == "distilled" and rep.code == 0


def test_commit_message_and_trailers(fx: Fixture) -> None:
    res = distill_card(fx, "D-002", commit=True)
    assert res.code == 0, res.issues
    assert res.journal is not None and res.journal.state == "committed"
    msg = git(fx.root, "log", "-1", "--format=%B")
    assert msg.startswith(f"declutter(distill {RID} D-002): 3 copias → invariante, \u2212")
    assert f"Declutter-Distill: {RID}/D-002 card-sha256=" in msg
    assert f"Declutter-Reorg: {RID}" in msg
    assert git(fx.root, "status", "--porcelain").strip() == ""


# --------------------------------------------------------------------------- confirmation


def test_no_tty_exits_1_and_writes_nothing(fx: Fixture) -> None:
    fill(fx, "D-002")
    before = tree(fx.root)
    res = distill_card(fx, "D-002", prompter=FakePrompter(tty=False))
    assert res.code == 1
    assert "distill.no_tty" in _keys(res)
    assert tree(fx.root) == before
    assert not (fx.root / ".declutter/distill").exists()
    res = distill_card(fx, "D-002", prompter=FakePrompter(tty=False), yes=True)
    assert res.code == 1
    assert tree(fx.root) == before


def test_yes_still_asks_reason_and_word(fx: Fixture) -> None:
    p = ok_prompter()
    res = distill_card(fx, "D-002", prompter=p, yes=True)
    assert res.code == 0, res.issues
    assert any("Motivo" in x for x in p.prompts)
    assert any("confirmo" in x for x in p.prompts)
    event = next(e for e in events(fx) if e["event"] == "distill")
    assert event["reason"] == "motivo sintético"


@pytest.mark.parametrize(
    ("answers", "key"),
    [(["motivo", "no"], "distill.not_confirmed"), (["", "confirmo"], "distill.no_reason")],
)
def test_wrong_word_or_empty_reason_exits_1(fx: Fixture, answers: list[str], key: str) -> None:
    fill(fx, "D-002")
    before = tree(fx.root)
    res = distill_card(fx, "D-002", prompter=FakePrompter(tty=True, answers=answers))
    assert res.code == 1 and key in _keys(res)
    assert tree(fx.root) == before


def test_confirmation_lists_metadata_only(fx: Fixture) -> None:
    p = ok_prompter()
    assert distill_card(fx, "D-001", prompter=p).code == 0
    said = "\n".join(p.said)
    assert "U02.b1" in said and "P-001" in said and "AGENTS.md" in said
    assert SEED not in said


def test_dry_run_writes_nothing_and_asks_nothing(fx: Fixture) -> None:
    fill(fx, "D-002")
    before = tree(fx.root)
    p = FakePrompter(tty=False)
    res = distill_card(fx, "D-002", prompter=p, dry_run=True)
    assert res.code == 0, res.issues
    assert p.prompts == []
    assert tree(fx.root) == before
    assert git(fx.root, "branch", "--show-current").strip() == f"declutter/reorg-{RID}"
    assert not list((fx.root / ".declutter").glob("distill/*/*/dry-stage"))


# --------------------------------------------------------------------------- refusals


def test_not_ready_card_exits_4(fx: Fixture) -> None:
    before = tree(fx.root)  # cards not filled: PENDIENTE
    res = distill_card(fx, "D-002", fill_first=False)
    assert res.code == 4
    assert "distill.not_ready" in _keys(res)
    assert tree(fx.root) == before


def test_second_run_is_idempotent(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    before = tree(fx.root)
    head = git(fx.root, "rev-parse", "HEAD")
    p = ok_prompter()
    res = distill_card(fx, "D-002", prompter=p)
    assert res.code == 0, res.issues
    assert any("ya destilada" in x for x in p.said)
    assert tree(fx.root) == before and git(fx.root, "rev-parse", "HEAD") == head


def test_written_journal_without_commit_is_already_applied(fx: Fixture) -> None:
    assert distill_card(fx, "D-002").code == 0
    before = tree(fx.root)
    res = distill_card(fx, "D-002")
    assert res.code == 1 and "distill.already_applied" in _keys(res)
    assert tree(fx.root) == before
    # Resume the commit without a TTY: --commit --yes.
    res = distill_card(fx, "D-002", prompter=FakePrompter(tty=False), commit=True, yes=True)
    assert res.code == 0, res.issues
    assert res.journal is not None and res.journal.state == "committed"
    assert verify(fx, strict=True)[0]


def test_resume_without_tty_needs_yes(fx: Fixture) -> None:
    assert distill_card(fx, "D-002").code == 0
    res = distill_card(fx, "D-002", prompter=FakePrompter(tty=False), commit=True)
    assert res.code == 1 and "distill.needs_yes" in _keys(res)


def test_dirty_output_other_than_card_refused(fx: Fixture) -> None:
    (fx.root / "AGENTS.md").write_bytes((fx.root / "AGENTS.md").read_bytes() + b"x\n")
    res = distill_card(fx, "D-002")
    assert res.code == 1 and "dirty" in _keys(res)


def test_existing_refs_refused_with_commands(fx: Fixture) -> None:
    git(fx.root, "tag", f"declutter/pre-distill-{RID}-D-002")
    res = distill_card(fx, "D-002")
    assert res.code == 1
    issue = next(i for i in res.issues if i.key == "ref_exists")
    assert f"git tag -d declutter/pre-distill-{RID}-D-002" in issue.detail


def test_unknown_card_exits_1(fx: Fixture) -> None:
    res = distill_card(fx, "D-099", fill_first=False)
    assert res.code == 1


# --------------------------------------------------------------------------- order


def test_order_independent_result(
    make_project: MakeProject, tmp_path_factory: pytest.TempPathFactory
) -> None:
    a = applied(make_project)
    other = tmp_path_factory.mktemp("second") / "home"
    (other / ".claude").mkdir(parents=True)
    (other / "proj").mkdir()

    def second() -> Project:
        return Project.at(other / "proj", home=other, claude_home=other / ".claude")

    b = applied(second)
    for card in ("D-001", "D-003"):
        assert distill_card(a, card, commit=True).code == 0
    for card in ("D-003", "D-001"):
        assert distill_card(b, card, commit=True).code == 0
    assert (a.root / "AGENTS.md").read_bytes() == (b.root / "AGENTS.md").read_bytes()
    for fx in (a, b):
        assert verify(fx, strict=True)[0]
        assert ratchet_code(fx) == 0


def test_all_three_cards_then_verify(fx: Fixture) -> None:
    for card in ("D-002", "D-001", "D-003"):
        res = distill_card(fx, card, commit=True)
        assert res.code == 0, (card, res.issues)
    ok, states = verify(fx, strict=True)
    assert ok, states
    assert ratchet_code(fx) == 0
    text = (fx.root / "AGENTS.md").read_text()
    heads = [x for x in text.split("\n") if x.startswith("### ⟨") and " D-" in x]
    assert [h.split("⟩ ")[1][:5] for h in heads] == ["D-001", "D-002", "D-003"]
    rep = distill.check(fx.root, RID, card=None, lexicon=LEX)
    assert [c.state for c in rep.cards] == ["distilled"] * 3 and rep.code == 0


# --------------------------------------------------------------------------- manual edits


def test_deleting_the_distilled_entry_fails_verify_and_r5(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    path = fx.root / "AGENTS.md"
    lines = path.read_bytes().split(b"\n")
    head = "### ⟨U03.b2, U03.b3, U03.b4⟩ D-002".encode()
    start = next(i for i, x in enumerate(lines) if x.startswith(head))
    del lines[start : start + 5]
    path.write_bytes(b"\n".join(lines))
    ok, states = verify(fx)
    assert not ok and "pin_missing" in states
    rep = ratchet.check(fx.project, CFG, staged=False)
    assert rep.code == 3
    assert any(f.rule == "R5" and "role=invariant" in f.detail for f in rep.failures)


def test_editing_the_distilled_card_fails_verify_and_r5(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    card = fx.card_path("D-002")
    card.write_bytes(card.read_bytes().replace(b"Regla sint", b"Regla Sint", 1))
    assert not verify(fx)[0]
    rep = ratchet.check(fx.project, CFG, staged=False)
    assert any(f.rule == "R5" and "role=card" in f.detail for f in rep.failures)


def test_deleting_a_not_distilled_copy_still_fails(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    path = fx.root / "AGENTS.md"
    data = path.read_bytes()
    line = next(x for x in data.split(b"\n") if x.startswith(b"- Nunca subas el password de src"))
    path.write_bytes(data.replace(line + b"\n", b"", 1))
    assert not verify(fx)[0]
    assert ratchet_code(fx) == 3


def test_distill_event_removed_from_log_fails(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    log = fx.root / ratchet.LOG_REL
    kept = [x for x in log.read_bytes().split(b"\n") if b'"event":"distill"' not in x]
    log.write_bytes(b"\n".join(kept))
    assert not verify(fx)[0]
    assert ratchet_code(fx) == 3


def test_invariant_pin_removed_from_pins_does_not_release(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    ps = [p for p in pins(fx) if p.role != "invariant"]
    (fx.root / ratchet.PINS_REL).write_bytes(ratchet.pins_dumps(ps))
    ok, states = verify(fx)
    assert not ok and "pin_missing" in states
    rep = ratchet.check(fx.project, CFG, staged=False)
    assert any(f.rule == "R5" and "role=copy" in f.detail for f in rep.failures)


# --------------------------------------------------------------------------- unpin (2a change)


def test_plain_unpin_then_verify_strict_passes(fx: Fixture) -> None:
    p = FakePrompter(tty=True, answers=["confirmo"])
    assert ratchet.unpin(fx.project, "P-006", reason="x", prompter=p, today=NOW.date()) == 0
    ok, states = verify(fx, strict=True)
    assert ok, states
    assert "control_appended" in states


def test_unpin_before_distill_stays_released(fx: Fixture) -> None:
    p = FakePrompter(tty=True, answers=["confirmo"])
    assert ratchet.unpin(fx.project, "P-006", reason="x", prompter=p, today=NOW.date()) == 0
    git(fx.root, "commit", "-q", "-m", "unpin", "--", ratchet.LOG_REL)
    assert distill_card(fx, "D-002", commit=True).code == 0
    p6 = next(x for x in pins(fx) if x.id == "P-006")
    assert ratchet.pin_released(
        p6, ratchet.log_events((fx.root / ratchet.LOG_REL).read_bytes()), pins(fx)
    )
    assert ratchet_code(fx) == 0


# --------------------------------------------------------------------------- undo


def _outputs_sha(fx: Fixture, card: str) -> dict[str, str]:
    paths = [*OUTPUTS_ALL, str(fx.card_path(card).relative_to(fx.root))]
    return {
        p: hashlib.sha256((fx.root / p).read_bytes()).hexdigest()
        for p in paths
        if (fx.root / p).exists()
    }


def test_undo_written_restores_exact_bytes(fx: Fixture) -> None:
    # D-003 writes the zone pointer too.
    fill(fx, "D-003")
    before = _outputs_sha(fx, "D-003")
    assert distill_card(fx, "D-003").code == 0
    assert _outputs_sha(fx, "D-003") != before
    res = undo_card(fx, "D-003")
    assert res.code == 0, res.issues
    assert res.journal is not None and res.journal.state == "undone"
    assert _outputs_sha(fx, "D-003") == before
    assert git(fx.root, "branch", "--show-current").strip() == f"declutter/reorg-{RID}"
    assert verify(fx)[0] and ratchet_code(fx) == 0
    p1 = next(x for x in pins(fx) if x.unit == fx.unit("Contrato sintético 2026-10-08").id)
    evs = ratchet.log_events((fx.root / ratchet.LOG_REL).read_bytes())
    assert not ratchet.pin_released(p1, evs, pins(fx))


def test_undo_committed_unmerged_goes_back_to_base(fx: Fixture) -> None:
    fill(fx, "D-002")
    before = _outputs_sha(fx, "D-002")
    card_before = before.pop(str(fx.card_path("D-002").relative_to(fx.root)))
    assert distill_card(fx, "D-002", commit=True).code == 0
    res = undo_card(fx, "D-002")
    assert res.code == 0, res.issues
    after = _outputs_sha(fx, "D-002")
    card_after = after.pop(str(fx.card_path("D-002").relative_to(fx.root)))
    assert after == before
    assert card_after != card_before  # the filled card stays on the distill branch (§9)
    assert git(fx.root, "branch", "--show-current").strip() == f"declutter/reorg-{RID}"
    assert verify(fx, strict=True)[0] and ratchet_code(fx) == 0


def test_undo_is_lifo(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    assert distill_card(fx, "D-001").code == 0
    res = undo_card(fx, "D-002")
    assert res.code == 1 and "distill.lifo" in _keys(res)
    assert any(i.detail == "D-001" for i in res.issues)
    assert undo_card(fx, "D-001").code == 0
    assert undo_card(fx, "D-002").code == 0


def test_undo_lifo_twice_returns_to_initial_bytes(fx: Fixture) -> None:
    fill(fx, "D-002")
    fill(fx, "D-001")
    initial = tree(fx.root)
    head0 = git(fx.root, "rev-parse", "HEAD")
    assert distill_card(fx, "D-002", commit=True).code == 0
    assert distill_card(fx, "D-001").code == 0
    assert undo_card(fx, "D-002").code == 1
    assert undo_card(fx, "D-001").code == 0
    assert undo_card(fx, "D-002").code == 0
    assert git(fx.root, "rev-parse", "HEAD") == head0
    final = tree(fx.root)
    card2 = str(fx.card_path("D-002").relative_to(fx.root))
    # D-001 was filled before and travels with every switch; D-002's filled card stays on
    # its distillation branch (§9) and the base has its committed version again.
    assert {k: v for k, v in final.items() if k != card2} == {
        k: v for k, v in initial.items() if k != card2
    }
    assert (
        final[card2] == hashlib.sha256(git(fx.root, "show", f"HEAD:{card2}").encode()).hexdigest()
    )
    assert verify(fx)[0]
    assert ratchet_code(fx) == 0
    evs = ratchet.log_events((fx.root / ratchet.LOG_REL).read_bytes())
    assert [e["event"] for e in evs] == ["baseline"]
    assert all(not ratchet.pin_released(x, evs, pins(fx)) for x in pins(fx))


def test_undo_nothing_to_undo(fx: Fixture) -> None:
    res = undo_card(fx, "D-001")
    assert res.code == 0


# --------------------------------------------------------------------------- interruptions


def test_exception_before_agents_replace_rolls_back(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-002")
    before = tree(fx.root)
    real = fsutil.atomic_write_bytes

    def boom(path: Path, data: bytes, *roots: Path, **kw: object) -> None:
        if path == fx.root / "AGENTS.md":
            raise OSError("injected")
        real(path, data, *roots, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "atomic_write_bytes", boom)
    res = distill_card(fx, "D-002")
    monkeypatch.undo()
    assert res.code == 4, res.issues
    assert tree(fx.root) == before
    assert git(fx.root, "branch", "--show-current").strip() == f"declutter/reorg-{RID}"


def test_crash_before_agents_leaves_copies_and_undo_restores(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-002")
    before = tree(fx.root)
    agents = (fx.root / "AGENTS.md").read_bytes()
    real = fsutil.atomic_write_bytes

    class Crash(BaseException):
        pass

    def boom(path: Path, data: bytes, *roots: Path, **kw: object) -> None:
        if path == fx.root / "AGENTS.md":
            raise Crash
        real(path, data, *roots, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "atomic_write_bytes", boom)
    with pytest.raises(Crash):
        distill_card(fx, "D-002")
    monkeypatch.undo()
    assert (fx.root / "AGENTS.md").read_bytes() == agents
    rows = distill_apply.status(fx.root, RID)
    assert [(r.card, r.state, r.interrupted) for r in rows] == [("D-002", "writing", True)]
    res = undo_card(fx, "D-002")
    assert res.code == 0, res.issues
    assert tree(fx.root) == before


def test_concurrent_edit_between_stage_and_write(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = distill_apply._backup
    foreign = b"\nedicion ajena\n"

    def edit_then_backup(ctx: object, j: object) -> None:
        path = fx.root / "AGENTS.md"
        path.write_bytes(path.read_bytes() + foreign)
        real(ctx, j)  # type: ignore[arg-type]

    monkeypatch.setattr(distill_apply, "_backup", edit_then_backup)
    res = distill_card(fx, "D-002")
    assert res.code == 4 and "concurrent_edit" in _keys(res)
    assert (fx.root / "AGENTS.md").read_bytes().endswith(foreign)


# --------------------------------------------------------------------------- status


def test_status_lists_distills(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    rows = distill_apply.status(fx.root, RID)
    assert [(r.card, r.state, r.date) for r in rows] == [("D-002", "committed", "2026-11-04")]
    rec = json.loads((fx.root / f".declutter/distill/{RID}/D-002/record.json").read_text())
    assert rec["event"]["event"] == "distill"


# --------------------------------------------------------------------------- tighten (§7.5)


def test_tighten_only_lowers_and_is_logged(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    state0 = ratchet.ratchet_loads((fx.root / ratchet.RATCHET_REL).read_bytes())
    monkeypatch.setattr(ratchet, "measure", lambda *_a, **_k: (state0.s1_max - 2, 1000.0))
    assert distill_card(fx, "D-002", commit=True).code == 0
    raw = (fx.root / ratchet.RATCHET_REL).read_bytes()
    state = ratchet.ratchet_loads(raw)
    assert state.s1_max == state0.s1_max - 2
    assert state.s3_max_pct == state0.s3_max_pct  # never raised
    assert state.slack_lines == state0.slack_lines
    assert state.zone_max_lines == state0.zone_max_lines
    last = events(fx)[-1]
    assert last["event"] == "tighten"
    assert last["old"] == {"S1_max": state0.s1_max, "S3_max_pct": state0.s3_max_pct}
    assert last["new"] == {"S1_max": state0.s1_max - 2, "S3_max_pct": state0.s3_max_pct}
    assert last["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert last["distill"] == f"{RID}/D-002"
    assert [e["event"] for e in events(fx)].count("baseline") == 1
    monkeypatch.undo()
    assert verify(fx, strict=True)[0]
    assert ratchet_code(fx) == 0


def test_no_tighten_when_nothing_goes_down(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    raw0 = (fx.root / ratchet.RATCHET_REL).read_bytes()
    monkeypatch.setattr(ratchet, "measure", lambda *_a, **_k: (10_000, 1000.0))
    res = distill_card(fx, "D-002", commit=True)
    assert res.code == 0, res.issues
    assert (fx.root / ratchet.RATCHET_REL).read_bytes() == raw0
    assert "tighten" not in [e["event"] for e in events(fx)]
    assert res.journal is not None
    assert ratchet.RATCHET_REL not in {e.path for e in res.journal.entries}


# --------------------------------------------------------------------------- interrupted


def test_interrupted_distill_blocks_the_next_one(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(fx, "D-002")
    real = fsutil.atomic_write_bytes

    class Crash(BaseException):
        pass

    def boom(path: Path, data: bytes, *roots: Path, **kw: object) -> None:
        if path == fx.root / "AGENTS.md":
            raise Crash
        real(path, data, *roots, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "atomic_write_bytes", boom)
    with pytest.raises(Crash):
        distill_card(fx, "D-002")
    monkeypatch.undo()
    res = distill_card(fx, "D-001")
    assert res.code == 1 and "distill.interrupted_other" in _keys(res)
    res = distill_card(fx, "D-002")
    assert res.code == 1 and "distill.interrupted" in _keys(res)
    assert undo_card(fx, "D-002").code == 0


# --------------------------------------------------------------------------- merged undo


def test_undo_merged_reverts_with_confirmation(fx: Fixture) -> None:
    agents0 = (fx.root / "AGENTS.md").read_bytes()
    assert distill_card(fx, "D-002", commit=True).code == 0
    base = f"declutter/reorg-{RID}"
    git(fx.root, "switch", "-q", base)
    git(fx.root, "merge", "-q", "--ff-only", f"declutter/distill-{RID}-D-002")
    res = distill_apply.undo(
        fx.project, CFG, RID, "D-002", FakePrompter(tty=False), yes=False, now=NOW, lang="es"
    )
    assert res.code == 1  # no TTY and no --yes
    res = undo_card(fx, "D-002", yes=True)
    assert res.code == 0, res.issues
    assert (fx.root / "AGENTS.md").read_bytes() == agents0
    assert git(fx.root, "log", "-1", "--format=%s").startswith("Revertir declutter(distill")
    assert git(fx.root, "status", "--porcelain").strip() == ""
    assert verify(fx, strict=True)[0] and ratchet_code(fx) == 0


def test_repin_is_control_appended_under_strict(fx: Fixture) -> None:
    path = fx.root / "AGENTS.md"
    lines = path.read_bytes().split(b"\n")
    k = lines.index(b"- El campo password se valida aparte.")
    lines[k] = b"- El campo password se valida aparte siempre."
    path.write_bytes(b"\n".join(lines))
    pin = next(x for x in pins(fx) if x.block == "U05.b2")
    p = FakePrompter(tty=True, answers=["confirmo"])
    code = ratchet.repin(
        fx.project,
        pin.id,
        lines=(k + 1, k + 1),
        reason="x",
        prompter=p,
        lexicon=LEX,
        today=NOW.date(),
    )
    assert code == 0
    ok, states = verify(fx, strict=True)
    assert ok, states
    assert "control_appended" in states
    assert "control_edited" not in states, states


def test_run_undo_refused_while_a_distillation_is_active(fx: Fixture) -> None:
    from declutter_context.reorg import undo as reorg_undo

    assert distill_card(fx, "D-002", commit=True).code == 0
    res = reorg_undo.undo(fx.project, RID, FakePrompter(tty=False), yes=True, lang="es")
    assert res.code == 1 and {i.key for i in res.issues} == {"distill_active"}
    assert undo_card(fx, "D-002").code == 0
    res = reorg_undo.undo(fx.project, RID, FakePrompter(tty=False), yes=True, lang="es")
    assert res.code == 0, res.issues


@pytest.mark.parametrize(
    "target",
    [
        "docs/decisiones/D-002-sesion-2026-10-06.md",
        "docs/decisiones/INDICE.md",
        ".declutter/pins.json",
        ".declutter/ratchet.log",
        "AGENTS.md",
        "backup",
        "switch",
    ],
)
def test_failure_at_each_step_leaves_the_tree_identical(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    fill(fx, "D-002")
    before = tree(fx.root)
    real = fsutil.atomic_write_bytes
    folder = distill_apply.distill_dir(fx.root, RID, "D-002")

    def boom(path: Path, data: bytes, *roots: Path, **kw: object) -> None:
        if target == "backup" and path.is_relative_to(folder / "backup"):
            raise OSError("injected")
        if path == fx.root / target:
            raise OSError("injected")
        real(path, data, *roots, **kw)  # type: ignore[arg-type]

    def no_switch(_root: Path, _branch: str) -> None:
        raise distill_apply.gitops.GitError("switch", 1)

    monkeypatch.setattr(fsutil, "atomic_write_bytes", boom)
    if target == "switch":
        monkeypatch.setattr(distill_apply.gitops, "switch_create", no_switch)
    res = distill_card(fx, "D-002")
    monkeypatch.undo()
    assert res.code == 4, res.issues
    assert tree(fx.root) == before
    assert git(fx.root, "branch", "--show-current").strip() == f"declutter/reorg-{RID}"
    assert res.journal is not None and res.journal.state == "undone"


# --------------------------------------------------------------------------- forged overlay


def _append_event(fx: Fixture, obj: dict[str, object]) -> None:
    log = fx.root / ratchet.LOG_REL
    data = log.read_bytes()
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    log.write_bytes(data + line.encode())


def _run_placements(fx: Fixture) -> list[dict[str, object]]:
    raw = (fx.root / f".declutter/reorg/{RID}/placement.json").read_bytes()
    obj = json.loads(raw)
    return obj if isinstance(obj, list) else obj["placements"]


def test_forged_event_cannot_hide_an_altered_history_literal(fx: Fixture) -> None:
    hist = next(
        p
        for p in _run_placements(fx)
        if p["role"] == "primary" and p["dest"].startswith("docs/historial")
    )
    _append_event(
        fx,
        {
            "event": "distill",
            "date": "2026-11-05",
            "reorg": RID,
            "reason": "x",
            "card": "D-001",
            "unit": hist["unit"],
            "distill": f"{RID}/D-001",
            "old": {"pins": []},
            "new": {"pins": []},
            "placements": [
                {
                    "dest": hist["dest"],
                    "kind": "primary",
                    "at": hist["at"],
                    "old_sha256": hist["sha256"],
                    "old_lines": hist["at"][1] - hist["at"][0] + 1,
                    "new_sha256": None,
                    "new_lines": 0,
                }
            ],
            "generated": [],
            "files": [],
        },
    )
    path = fx.root / hist["dest"]
    lines = path.read_bytes().split(b"\n")
    k = hist["at"][0] - 1
    lines[k] = lines[k] + b" alterado"
    path.write_bytes(b"\n".join(lines))
    ok, states = verify(fx)
    assert not ok
    assert "history_altered" in states


def test_forged_lines_delta_cannot_hide_an_edit_under_strict(fx: Fixture) -> None:
    _append_event(
        fx,
        {
            "event": "distill",
            "date": "2026-11-05",
            "reorg": RID,
            "reason": "x",
            "card": "D-001",
            "unit": "U02",
            "distill": f"{RID}/D-001",
            "old": {"pins": []},
            "new": {"pins": []},
            "placements": [],
            "generated": [],
            "files": [{"path": "AGENTS.md", "lines_delta": 1}],
        },
    )
    path = fx.root / "AGENTS.md"
    path.write_bytes(path.read_bytes() + b"extra\n")
    ok, states = verify(fx, strict=True)
    assert not ok
    assert "distill_invalid" in states


def test_forged_tighten_cannot_hide_a_raised_maximum(fx: Fixture) -> None:
    path = fx.root / ratchet.RATCHET_REL
    state = ratchet.ratchet_loads(path.read_bytes())
    raised = ratchet.ratchet_dumps(
        ratchet.RatchetState(**{**state.__dict__, "s1_max": state.s1_max + 100})
    )
    path.write_bytes(raised)
    _append_event(
        fx,
        {
            "event": "tighten",
            "date": "2026-11-05",
            "reorg": RID,
            "reason": "x",
            "distill": f"{RID}/D-001",
            "old": {"S1_max": state.s1_max, "S3_max_pct": state.s3_max_pct},
            "new": {"S1_max": state.s1_max + 100, "S3_max_pct": state.s3_max_pct},
            "file_sha256": hashlib.sha256(raised).hexdigest(),
        },
    )
    ok, states = verify(fx, strict=True)
    assert not ok and "control_edited" in states


def test_undo_merged_refuses_a_dirty_index(fx: Fixture) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0
    git(fx.root, "switch", "-q", f"declutter/reorg-{RID}")
    git(fx.root, "merge", "-q", "--ff-only", f"declutter/distill-{RID}-D-002")
    (fx.root / "ajeno.txt").write_text("ajeno\n")
    git(fx.root, "add", "ajeno.txt")
    head = git(fx.root, "rev-parse", "HEAD")
    res = undo_card(fx, "D-002", yes=True)
    assert res.code == 1 and "index_not_clean" in _keys(res)
    assert git(fx.root, "rev-parse", "HEAD") == head
    assert "ajeno.txt" in git(fx.root, "diff", "--cached", "--name-only")


class _EditingPrompter(FakePrompter):
    """Edits the card while the human is typing the motive (a race with the skill)."""

    def __init__(self, fx: Fixture) -> None:
        super().__init__(tty=True, answers=["motivo", "confirmo", "n"])
        self.fx = fx

    def ask(self, prompt: str) -> str:
        if not self.prompts:
            inv = INV_ES["D-002"]
            fill(self.fx, "D-002", [inv[1], inv[0], inv[2]])
        return super().ask(prompt)


def test_card_edited_during_confirmation_aborts(fx: Fixture) -> None:
    fill(fx, "D-002")
    agents = (fx.root / "AGENTS.md").read_bytes()
    res = distill_card(fx, "D-002", prompter=_EditingPrompter(fx))
    assert res.code == 4 and "distill.card_changed" in _keys(res)
    assert (fx.root / "AGENTS.md").read_bytes() == agents
    assert "estado: pendiente-destilar" in fx.card_path("D-002").read_text()


def test_prove_catches_an_entry_that_is_not_the_cards_invariant(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the post-confirmation re-check, the proof still refuses: the entry in
    AGENTS.md must be the one the frozen card's invariant renders (entry_mismatch)."""
    fill(fx, "D-002")
    before = tree(fx.root)
    monkeypatch.setattr(distill_apply, "_same_entry", lambda *_a: True)
    res = distill_card(fx, "D-002", prompter=_EditingPrompter(fx))
    assert res.code == 4
    assert any("entry_mismatch" in i.detail for i in res.issues)
    after = tree(fx.root)
    card = str(fx.card_path("D-002").relative_to(fx.root))
    assert {k: v for k, v in after.items() if k != card} == {
        k: v for k, v in before.items() if k != card
    }


def test_lifo_holds_when_interrupted_between_journal_and_record(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert distill_card(fx, "D-002", commit=True).code == 0

    class Crash(BaseException):
        pass

    real_save = distill_apply._Ctx.save

    def save_then_crash(self: distill_apply._Ctx, j: distill_apply.Journal) -> None:
        real_save(self, j)
        if self.card == "D-001" and j.state == "staged":
            raise Crash

    monkeypatch.setattr(distill_apply._Ctx, "save", save_then_crash)
    with pytest.raises(Crash):
        distill_card(fx, "D-001")
    monkeypatch.undo()
    res = undo_card(fx, "D-002")
    assert res.code == 1 and "distill.lifo" in _keys(res)
    assert undo_card(fx, "D-001").code == 0
    assert undo_card(fx, "D-002").code == 0


def test_rollback_prints_the_cleanup_commands(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(fx, "D-002")
    real = fsutil.atomic_write_bytes

    def boom(path: Path, data: bytes, *roots: Path, **kw: object) -> None:
        if path == fx.root / "AGENTS.md":
            raise OSError("injected")
        real(path, data, *roots, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "atomic_write_bytes", boom)
    p = ok_prompter()
    res = distill_card(fx, "D-002", prompter=p)
    monkeypatch.undo()
    assert res.code == 4
    said = "\n".join(p.said)
    assert f"git tag -d declutter/pre-distill-{RID}-D-002" in said
    assert f"git branch -D declutter/distill-{RID}-D-002" in said


def test_entry_header_inside_a_fence_is_not_an_entry(fx: Fixture) -> None:
    inv = [*INV_ES["D-002"], "```", "### ⟨x⟩ D-xyz → nada", "```"]
    fill(fx, "D-002", inv)
    res = distill_card(fx, "D-002", commit=True)
    assert res.code == 0, res.issues
    res = distill_card(fx, "D-003", commit=True)  # scans past the D-002 entry's fence
    assert res.code == 0, res.issues
    text = (fx.root / "AGENTS.md").read_text()
    heads = [x for x in text.split("\n") if x.startswith("### ⟨U") and "⟩ D-" in x]
    assert [h.split("⟩ ")[1][:5] for h in heads] == ["D-002", "D-003"]
