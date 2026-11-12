"""distill_batch: batch number, batch.json and the lock (fase 3A §4, §6.3)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from declutter_context.reorg import ReorgError
from declutter_context.reorg import distill_batch as db

RID = "20261104-1530"


def _batch(n: int = 1, state: str = "running") -> db.Batch:
    return db.Batch(
        reorg=RID,
        batch=n,
        state=state,
        base_branch="main",
        base_commit="a" * 40,
        branch=db.batch_branch(RID, n),
        tag=db.batch_tag(RID, n),
        planned=("D-001", "D-002"),
        skipped_by_user=(),
        cards=(
            db.CardResult(
                "D-001", "applied", commit="b" * 40, bytes_removed=812, bytes_added=301, copies=3
            ),
            db.CardResult("D-002", "skipped", reasons=("anchor_missing",)),
        ),
        started="2026-11-11T10:00:00",
        ended=None,
    )


def test_names() -> None:
    assert db.batch_branch(RID, 3) == f"declutter/distill-auto-{RID}-3"
    assert db.batch_tag(RID, 3) == f"declutter/pre-distill-auto-{RID}-3"


def test_next_number_starts_at_1_and_follows_the_highest(tmp_path: Path) -> None:
    assert db.next_number(tmp_path, RID) == 1
    for n in (1, 4):
        db.batch_dir(tmp_path, RID, n).mkdir(parents=True)
    (tmp_path / ".declutter/distill" / RID / "auto-x").mkdir()
    assert db.next_number(tmp_path, RID) == 5


def test_batch_round_trip(tmp_path: Path) -> None:
    b = _batch()
    db.write_batch(tmp_path, b)
    assert db.read_batch(tmp_path, RID, 1) == b
    raw = (db.batch_dir(tmp_path, RID, 1) / "batch.json").read_text()
    assert '"schema": "declutter.distill-batch/1"' in raw


def test_missing_batch_is_none(tmp_path: Path) -> None:
    assert db.read_batch(tmp_path, RID, 7) is None


@pytest.mark.parametrize("patch", [{"schema": "x"}, {"state": "maybe"}, {"batch": "1"}])
def test_invalid_batch_is_rejected(tmp_path: Path, patch: dict[str, object]) -> None:

    db.write_batch(tmp_path, _batch())
    path = db.batch_dir(tmp_path, RID, 1) / "batch.json"
    obj = json.loads(path.read_text())
    obj.update(patch)
    path.write_text(json.dumps(obj))
    with pytest.raises(ReorgError):
        db.read_batch(tmp_path, RID, 1)


def test_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    db.acquire(tmp_path, RID, 2, "2026-11-11T10:00:00")
    lock = db.read_lock(tmp_path, RID)
    assert lock is not None and lock.pid == os.getpid() and lock.batch == 2
    with pytest.raises(FileExistsError):
        db.acquire(tmp_path, RID, 3, "2026-11-11T10:00:01")
    db.release(tmp_path, RID)
    assert db.read_lock(tmp_path, RID) is None
    db.release(tmp_path, RID)  # idempotent


def test_pid_alive() -> None:
    assert db.pid_alive(os.getpid())
    proc = subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert proc.returncode == 0
    assert not db.pid_alive(0)
    assert not db.pid_alive(-5)


def test_lock_with_boolean_pid_counts_as_dead(tmp_path: Path) -> None:
    path = db.lock_path(tmp_path, RID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": True, "started": "2026-11-11T10:00:00", "batch": 1}))
    lock = db.read_lock(tmp_path, RID)
    assert lock is not None and lock.pid == 0
    assert not db.pid_alive(lock.pid)
