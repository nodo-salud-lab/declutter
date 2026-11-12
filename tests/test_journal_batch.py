"""Journal.batch (fase 3A §4.3): optional, serialized only when present."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from declutter_context.reorg import ReorgError
from declutter_context.reorg.apply import Journal, read_journal, write_journal


def test_journal_without_batch_serializes_as_before(tmp_path: Path) -> None:
    write_journal(tmp_path, Journal(state="staged", base_branch=None, entries=()), tmp_path)
    obj = json.loads((tmp_path / "journal.json").read_text())
    assert "batch" not in obj


def test_journal_batch_round_trips(tmp_path: Path) -> None:
    j = Journal(state="staged", base_branch="main", entries=(), batch=3)
    write_journal(tmp_path, j, tmp_path)
    back = read_journal(tmp_path, tmp_path)
    assert back is not None and back.batch == 3


@pytest.mark.parametrize("bad", ["3", True, 0, -1, 1.5])
def test_journal_batch_invalid_is_rejected(tmp_path: Path, bad: object) -> None:
    raw = {"state": "staged", "base_branch": None, "entries": [], "batch": bad}
    (tmp_path / "journal.json").write_text(json.dumps(raw))
    with pytest.raises(ReorgError):
        read_journal(tmp_path, tmp_path)
