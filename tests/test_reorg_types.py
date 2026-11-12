"""Shared reorg types, exit codes and precedence (spec §4.8, §10.5)."""

from __future__ import annotations

import dataclasses
import io

import pytest

from declutter_context.model import (
    Block,
    Config,
    Decision,
    Features,
    Pin,
    Placement,
    ProofFailure,
    ProofResult,
    ReorgConfig,
    ReorgPlan,
    SourceDoc,
    Unit,
)
from declutter_context.reorg import (
    EXIT_INTERNAL,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PROOF,
    EXIT_SECRET,
    EXIT_THRESHOLD,
    EXIT_USAGE,
    PRECEDENCE,
    Issue,
    Prompter,
    ReorgError,
    StdPrompter,
    resolve_code,
)


def _source() -> SourceDoc:
    return SourceDoc(
        path="CLAUDE.md",
        sha256="0" * 64,
        bytes=2,
        lines=(b"a",),
        bom=False,
        final_newline=True,
        eol="lf",
    )


def test_exit_code_values() -> None:
    assert (EXIT_OK, EXIT_USAGE, EXIT_INTERNAL, EXIT_THRESHOLD) == (0, 1, 2, 3)
    assert (EXIT_PROOF, EXIT_SECRET, EXIT_INTERRUPTED) == (4, 5, 130)
    assert PRECEDENCE == (5, 2, 1, 4, 3)


def test_reorg_config_defaults() -> None:
    c = ReorgConfig()
    assert c.l1_max_bytes == 12_288
    assert c.l1_max_lines == 200
    assert c.zone_max_lines == 150
    assert c.codex_warn_pct == 60
    assert c.extra_lexicon == {}


def test_config_has_reorg_field() -> None:
    assert Config().reorg == ReorgConfig()


def test_dataclasses_are_frozen() -> None:
    placement = Placement(role="primary", dest="AGENTS.md", at=(1, 1), sha256="0" * 64)
    pin = Pin(
        id="P-001",
        reorg="r",
        unit="U01",
        block=None,
        file="AGENTS.md",
        role="unit",
        categories=("clinical",),
        lines=1,
        sha256="0" * 64,
    )
    for obj, name in ((_source(), "path"), (placement, "dest"), (pin, "id")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, name, "x")


def test_all_new_types_are_frozen_dataclasses() -> None:
    for cls in (
        SourceDoc,
        Block,
        Features,
        Unit,
        Decision,
        Placement,
        Pin,
        ReorgPlan,
        ProofFailure,
        ProofResult,
        ReorgConfig,
    ):
        assert dataclasses.is_dataclass(cls)
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen, cls.__name__


def test_type_defaults() -> None:
    b = Block(id="U01.b1", kind="paragraph", start=1, end=2, key="k:abc")
    assert b.categories == frozenset()
    assert not (b.imperative or b.decision or b.trap or b.claude_only)
    f = Features()
    assert f.dated is None and f.zone is None and f.q_ratio == 0.0 and f.k == 0
    u = Unit(id="U01", source="CLAUDE.md", heading="", heading_key="U01", start=1, end=2, blocks=())
    assert u.features == Features()
    d = Decision(
        unit="U01",
        dest="L1",
        file="AGENTS.md",
        card=False,
        pointer=None,
        confidence="high",
        reasons=(),
        protected=False,
        needs_confirmation=False,
    )
    assert d.card_id is None
    p = Placement(role="primary", dest="AGENTS.md", at=(1, 1), sha256="0" * 64)
    assert p.unit is None and p.backup_sha256 is None and p.prefix_newline_added is False
    r = ProofResult(ok=True, mode="exact", failures=())
    assert r.states == ()


def test_resolve_code_precedence() -> None:
    assert resolve_code([3, 4]) == 4
    assert resolve_code([4, 1]) == 1
    assert resolve_code([1, 2]) == 2
    assert resolve_code([2, 5]) == 5
    assert resolve_code([3, 130, 5]) == 130
    assert resolve_code([]) == 0
    assert resolve_code(iter([3, 3])) == 3
    assert resolve_code([0, 0]) == 0


def test_reorg_error_code() -> None:
    err = ReorgError([Issue(3, "x"), Issue(4, "y")])
    assert err.code == 4
    assert err.issues == (Issue(3, "x"), Issue(4, "y"))
    assert isinstance(err.issues, tuple)


def test_reorg_error_message_is_metadata_only() -> None:
    err = ReorgError([Issue(4, "C2", unit="U01", detail="line 3")])
    assert "C2" in str(err)


def test_issue_defaults_and_frozen() -> None:
    i = Issue(5, "secret")
    assert i.unit is None and i.src is None and i.detail == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        i.code = 1  # type: ignore[misc]


def test_std_prompter(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    p: Prompter = StdPrompter()
    monkeypatch.setattr("sys.stdin", io.StringIO("confirmo\n"))
    assert p.ask("> ") == "confirmo"
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert p.ask("> ") == ""
    p.say("hola")
    assert "hola" in capsys.readouterr().out
    assert p.is_tty() is False
