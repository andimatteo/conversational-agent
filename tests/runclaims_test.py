"""Concurrency and restart tests for the persistent call-run claim store."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

from negotiator.runclaims import RunClaimStore


def _parallel_claims(
    store: RunClaimStore,
    count: int,
    *,
    shared_key: str | None = None,
    run_prefix: str = "parallel",
):
    barrier = threading.Barrier(count)
    results = []
    errors = []
    lock = threading.Lock()

    def worker(index: int):
        try:
            barrier.wait()
            result = store.claim(
                "job_parallel",
                run_id=f"run_{run_prefix}_{index}",
                idempotency_key=shared_key or f"request_{index}",
                stale_after=60,
                now=100,
            )
            with lock:
                results.append(result)
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
    temp_dir = Path(tempfile.mkdtemp(prefix="runclaims-test-"))
    db_path = temp_dir / "claims.db"
    store = RunClaimStore(db_path, busy_timeout_ms=10_000)

    # Different concurrent requests cannot both acquire one job.
    results = _parallel_claims(store, 20)
    owners = [result for result in results if result.acquired]
    assert len(owners) == 1, owners
    assert all(result.run_id == owners[0].run_id for result in results if not result.acquired)
    assert {result.reason for result in results if not result.acquired} == {"active_conflict"}
    assert all(result.owner_token is None for result in results if not result.acquired)
    assert store.finish(
        owners[0].job_id, owners[0].run_id, owners[0].owner_token, "completed", now=101
    )

    # One idempotency key has one stable run id even when callers proposed
    # different ids; completed replays cannot relaunch it.
    replay_results = _parallel_claims(
        store, 16, shared_key="same-request", run_prefix="replay"
    )
    replay_owner = next(result for result in replay_results if result.acquired)
    assert len({result.run_id for result in replay_results}) == 1
    assert sum(result.acquired for result in replay_results) == 1
    assert {result.reason for result in replay_results if not result.acquired} == {
        "idempotent_active"
    }
    assert store.finish(
        replay_owner.job_id, replay_owner.run_id, replay_owner.owner_token,
        "completed", now=102,
    )
    terminal_replay = store.claim(
        "job_parallel", idempotency_key="same-request", stale_after=60, now=103
    )
    assert not terminal_replay.acquired
    assert terminal_replay.reason == "idempotent_terminal"
    assert terminal_replay.run_id == replay_owner.run_id
    looked_up = store.by_idempotency("job_parallel", "same-request")
    assert looked_up.run_id == replay_owner.run_id and looked_up.status == "completed"

    # A heartbeat extends the lease. A different request is blocked until the
    # extended expiry, then atomically replaces the stale run.
    first = store.claim(
        "job_restart", run_id="run_old", idempotency_key="old-request",
        stale_after=10, metadata={"phase": "quote"}, now=200,
    )
    assert first.acquired and store.heartbeat(
        first.job_id, first.run_id, first.owner_token, stale_after=20, now=205
    )
    blocked = store.claim(
        "job_restart", run_id="run_too_early", idempotency_key="early",
        stale_after=10, now=224,
    )
    assert not blocked.acquired and blocked.run_id == first.run_id
    replacement = store.claim(
        "job_restart", run_id="run_new", idempotency_key="new-request",
        stale_after=10, now=226,
    )
    assert replacement.acquired and replacement.restarted
    assert replacement.reason == "stale_replaced"
    assert replacement.previous_run_id == first.run_id
    old_state = store.get(first.run_id)
    assert old_state.status == "stale" and old_state.replaced_by_run_id == replacement.run_id
    # The old process cannot revive or finish the replacement after restart.
    assert not store.heartbeat(first.job_id, first.run_id, first.owner_token, now=227)
    assert not store.finish(first.job_id, first.run_id, first.owner_token, "failed", now=227)

    # Replaying the *same* expired idempotent request reclaims the same run id
    # but rotates the fencing token and generation.
    assert store.finish(
        replacement.job_id, replacement.run_id, replacement.owner_token,
        "cancelled", now=228,
    )
    resumable = store.claim(
        "job_resume", run_id="run_resume", idempotency_key="resume-request",
        stale_after=5, now=300,
    )
    resumed = store.claim(
        "job_resume", run_id="ignored_new_id", idempotency_key="resume-request",
        stale_after=5, now=306,
    )
    assert resumed.acquired and resumed.restarted
    assert resumed.reason == "stale_reclaimed"
    assert resumed.run_id == resumable.run_id and resumed.generation == 2
    assert resumed.owner_token != resumable.owner_token
    assert not store.heartbeat(
        resumable.job_id, resumable.run_id, resumable.owner_token, now=307
    )
    assert store.finish(
        resumed.job_id, resumed.run_id, resumed.owner_token, "completed", now=307
    )

    # Startup sweep is explicit and idempotent.
    swept = store.claim("job_sweep", run_id="run_sweep", stale_after=2, now=400)
    assert swept.acquired
    assert store.expire_stale(now=403) == [swept.run_id]
    assert store.expire_stale(now=404) == []
    assert store.get(swept.run_id).status == "stale"

    # Schema-level invariants remain present even for writers outside this API.
    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
        active_count = connection.execute(
            """SELECT COUNT(*) FROM call_run_claims
               WHERE job_id='job_parallel' AND status='active'"""
        ).fetchone()[0]
        assert active_count == 0

    print("RUN CLAIMS TEST PASSED: atomic ownership, idempotency, stale restart, fencing")


if __name__ == "__main__":
    main()
