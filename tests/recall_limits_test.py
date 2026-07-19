"""Concurrency tests for the persistent two-recall safety guard."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

from negotiator.recall_limits import HARD_MAX_RECALLS, RecallLimitStore


def _parallel_unique_reservations(db_path: Path, count: int):
    barrier = threading.Barrier(count)
    results: list[tuple[str, int | None]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(index: int):
        try:
            # Separate instances model independent API/worker processes sharing
            # only SQLite; correctness must not depend on an in-memory lock.
            store = RecallLimitStore(db_path, busy_timeout_ms=10_000)
            reservation_id = f"request_{index}"
            barrier.wait()
            slot = store.reserve(
                "job_parallel", "company_parallel", reservation_id, now=100
            )
            with lock:
                results.append((reservation_id, slot))
        except Exception as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    return results


def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="recall-limits-test-"))
    db_path = temp_dir / "recalls.db"
    store = RecallLimitStore(db_path, busy_timeout_ms=10_000)

    # Many simultaneous, distinct retry decisions consume exactly two slots.
    results = _parallel_unique_reservations(db_path, 32)
    winners = [(reservation_id, slot) for reservation_id, slot in results
               if slot is not None]
    assert len(winners) == HARD_MAX_RECALLS, winners
    assert {slot for _, slot in winners} == {1, 2}
    assert sum(slot is None for _, slot in results) == 30
    assert len(store.for_company("job_parallel", "company_parallel")) == 2

    # Idempotent replays keep returning their original slots even after the
    # pair is exhausted; they never count as another recall.
    for reservation_id, expected_slot in winners:
        assert store.reserve(
            "job_parallel", "company_parallel", reservation_id, now=101
        ) == expected_slot
    assert len(store.for_company("job_parallel", "company_parallel")) == 2

    first_id, first_slot = winners[0]
    second_id, second_slot = winners[1]
    assert store.attach_call(
        "job_parallel", "company_parallel", first_id, "call_first",
        status="calling", now=102,
    )
    # Reattaching the same call is harmless; attaching another one is not.
    assert store.attach_call(
        "job_parallel", "company_parallel", first_id, "call_first",
        status="completed", now=103,
    )
    try:
        store.attach_call(
            "job_parallel", "company_parallel", first_id, "call_other"
        )
        raise AssertionError("a reservation accepted a second call id")
    except ValueError:
        pass
    assert store.set_status(
        "job_parallel", "company_parallel", second_id, "failed", now=104
    )
    state = store.get("job_parallel", "company_parallel", first_id)
    assert state.slot == first_slot
    assert state.call_id == "call_first" and state.status == "completed"

    # Terminal and failed states still consume both slots. No retry loop can
    # reopen capacity merely by changing call status.
    assert store.reserve(
        "job_parallel", "company_parallel", "request_after_terminal", now=105
    ) is None
    states = store.for_company("job_parallel", "company_parallel")
    assert {row.status for row in states} == {"completed", "failed"}
    assert {row.slot for row in states} == {first_slot, second_slot}

    # Capacity is isolated by both dimensions: another job or another company
    # has its own two recalls, even when the idempotency key is identical.
    for scope in (("job_other", "company_parallel"),
                  ("job_parallel", "company_other")):
        job_id, company_id = scope
        assert store.reserve(job_id, company_id, "shared-key") == 1
        assert store.reserve(job_id, company_id, "second") == 2
        assert store.reserve(job_id, company_id, "third") is None
        assert store.reserve(job_id, company_id, "shared-key") == 1

    # Caller-supplied configuration can lower, but never raise, the hard cap.
    assert store.reserve("job_lower", "company", "one", max_recalls=1) == 1
    assert store.reserve("job_lower", "company", "two", max_recalls=1) is None
    assert store.reserve("job_clamped", "company", "one", max_recalls=99) == 1
    assert store.reserve("job_clamped", "company", "two", max_recalls=99) == 2
    assert store.reserve("job_clamped", "company", "three", max_recalls=99) is None

    # Missing reservation updates fail safely and do not create a slot.
    assert not store.attach_call("missing_job", "missing_company", "missing", "call_x")
    assert not store.set_status("missing_job", "missing_company", "missing", "failed")
    assert store.for_company("missing_job", "missing_company") == []

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        rows = connection.execute(
            """SELECT slot, status FROM recall_reservations
               WHERE job_id='job_parallel' AND company_id='company_parallel'
               ORDER BY slot"""
        ).fetchall()
        assert len(rows) == 2

    print("RECALL LIMITS TEST PASSED: atomic cap, idempotency, status counting, isolation")


if __name__ == "__main__":
    main()
