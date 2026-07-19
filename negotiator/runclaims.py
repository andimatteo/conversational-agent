"""Atomic, persistent ownership claims for job-scoped call runs.

``callrunner`` keeps an in-process thread map for responsiveness, but that map
cannot prevent duplicate runs across workers or after a process restart.  This
module provides the small SQLite lease/fencing primitive needed at that
boundary:

* at most one ``active`` claim exists for a job (enforced by a partial unique
  index, not by a read-then-write convention);
* an optional idempotency key always resolves to the same run;
* expired leases can be reclaimed/replaced atomically after a restart; and
* a rotated owner token fences the old worker from heartbeating or completing
  a reclaimed run.

The intended integration is deliberately small::

    claim = claim_run(job_id, run_id=run_id, idempotency_key=request_key)
    if not claim.acquired:
        return claim                 # do not launch another worker
    try:
        start_worker(claim.run_id, claim.owner_token)
    except Exception:
        finish_run(claim.job_id, claim.run_id, claim.owner_token, "failed")
        raise

Long-running workers should call :func:`heartbeat_run` before the lease
expires and :func:`finish_run` exactly once on a terminal path.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .config import DB_PATH


TerminalStatus = Literal["completed", "failed", "cancelled"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_run_claims (
    run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    idempotency_key TEXT,
    owner_token TEXT NOT NULL,
    status TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    claimed_at REAL NOT NULL,
    heartbeat_at REAL NOT NULL,
    lease_seconds REAL NOT NULL,
    lease_expires_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL,
    replaced_by_run_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_run_claim_active_job
    ON call_run_claims(job_id) WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_run_claim_idempotency
    ON call_run_claims(job_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_call_run_claim_job_updated
    ON call_run_claims(job_id, updated_at DESC);
"""

_schema_lock = threading.Lock()


@dataclass(frozen=True)
class RunClaim:
    """Result of a claim attempt.

    ``owner_token`` is only returned to the caller that acquired ownership.
    Conflict/idempotent replays never receive another worker's fencing token.
    """

    acquired: bool
    reason: str
    job_id: str
    run_id: str
    owner_token: str | None
    status: str
    generation: int
    lease_expires_at: float
    idempotency_key: str | None = None
    restarted: bool = False
    previous_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunClaimState:
    """Read-only claim state; intentionally excludes the owner token."""

    job_id: str
    run_id: str
    status: str
    generation: int
    claimed_at: float
    heartbeat_at: float
    lease_expires_at: float
    finished_at: float | None
    idempotency_key: str | None
    replaced_by_run_id: str | None
    metadata: dict[str, Any]


class RunClaimStore:
    """SQLite-backed run lease store.

    The store enables WAL once per instance so normal dashboard reads can
    continue while a claim transaction commits.  ``busy_timeout`` absorbs
    short write contention; the actual claim is serialized with
    ``BEGIN IMMEDIATE`` and remains only a few SQL statements long.
    """

    def __init__(self, db_path: str | Path = DB_PATH, *, busy_timeout_ms: int = 5000):
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.db_path = Path(db_path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        # ``synchronous`` is connection-local (unlike journal_mode), so apply
        # it to every short-lived claim connection.
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _ensure_schema(self) -> None:
        if self._ready:
            return
        with _schema_lock:
            if self._ready:
                return
            connection = self._connect()
            try:
                # WAL is persistent for the database and safe with SQLite's
                # local-file locking. NORMAL (configured in _connect) keeps
                # atomic commits while avoiding a full fsync per heartbeat.
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(_SCHEMA)
            finally:
                connection.close()
            self._ready = True

    def _write(self, operation):
        self._ensure_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate(
        job_id: str,
        run_id: str | None,
        idempotency_key: str | None,
        stale_after: float,
    ) -> tuple[str, str | None, str | None, float]:
        job_id = (job_id or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        run_id = (run_id or "").strip() or None
        idempotency_key = (idempotency_key or "").strip() or None
        if idempotency_key and len(idempotency_key) > 128:
            raise ValueError("idempotency_key cannot exceed 128 characters")
        stale_after = float(stale_after)
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        return job_id, run_id, idempotency_key, stale_after

    @staticmethod
    def _is_stale(row: sqlite3.Row, timestamp: float) -> bool:
        return float(row["lease_expires_at"]) <= timestamp

    @staticmethod
    def _claim_result(
        row: sqlite3.Row,
        *,
        acquired: bool,
        reason: str,
        owner_token: str | None = None,
        restarted: bool = False,
        previous_run_id: str | None = None,
    ) -> RunClaim:
        return RunClaim(
            acquired=acquired,
            reason=reason,
            job_id=row["job_id"],
            run_id=row["run_id"],
            owner_token=owner_token if acquired else None,
            status=row["status"],
            generation=int(row["generation"]),
            lease_expires_at=float(row["lease_expires_at"]),
            idempotency_key=row["idempotency_key"],
            restarted=restarted,
            previous_run_id=previous_run_id,
        )

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        run_id: str,
        idempotency_key: str | None,
        owner_token: str,
        timestamp: float,
        stale_after: float,
        metadata_json: str,
    ) -> sqlite3.Row:
        connection.execute(
            """INSERT INTO call_run_claims
               (run_id, job_id, idempotency_key, owner_token, status,
                generation, claimed_at, heartbeat_at, lease_seconds,
                lease_expires_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, 'active', 1, ?, ?, ?, ?, ?, ?)""",
            (run_id, job_id, idempotency_key, owner_token, timestamp, timestamp,
             stale_after, timestamp + stale_after, timestamp, metadata_json),
        )
        return connection.execute(
            "SELECT * FROM call_run_claims WHERE run_id=?", (run_id,)
        ).fetchone()

    @staticmethod
    def _reclaim(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        owner_token: str,
        timestamp: float,
        stale_after: float,
        metadata_json: str | None,
    ) -> sqlite3.Row:
        metadata = metadata_json if metadata_json is not None else row["metadata"]
        connection.execute(
            """UPDATE call_run_claims
               SET owner_token=?, generation=generation+1, heartbeat_at=?,
                   lease_seconds=?, lease_expires_at=?, updated_at=?,
                   finished_at=NULL, replaced_by_run_id=NULL, metadata=?
               WHERE run_id=? AND status='active'""",
            (owner_token, timestamp, stale_after, timestamp + stale_after,
             timestamp, metadata, row["run_id"]),
        )
        return connection.execute(
            "SELECT * FROM call_run_claims WHERE run_id=?", (row["run_id"],)
        ).fetchone()

    def claim(
        self,
        job_id: str,
        *,
        run_id: str | None = None,
        idempotency_key: str | None = None,
        stale_after: float = 900,
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> RunClaim:
        """Atomically acquire the sole active run lease for ``job_id``.

        A replay with an idempotency key returns the original run and never
        creates another.  If that exact run's lease expired, it is reclaimed
        with the same run id and a new owner token.  A different request may
        replace an expired run with a new run id.
        """

        job_id, run_id, idempotency_key, stale_after = self._validate(
            job_id, run_id, idempotency_key, stale_after
        )
        timestamp = float(time.time() if now is None else now)
        requested_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        owner_token = secrets.token_urlsafe(24)
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))

        def operation(connection: sqlite3.Connection) -> RunClaim:
            if idempotency_key is not None:
                replay = connection.execute(
                    """SELECT * FROM call_run_claims
                       WHERE job_id=? AND idempotency_key=?""",
                    (job_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if replay["status"] == "active" and self._is_stale(replay, timestamp):
                        reclaimed = self._reclaim(
                            connection, replay, owner_token=owner_token,
                            timestamp=timestamp, stale_after=stale_after,
                            metadata_json=metadata_json if metadata is not None else None,
                        )
                        return self._claim_result(
                            reclaimed, acquired=True, reason="stale_reclaimed",
                            owner_token=owner_token, restarted=True,
                            previous_run_id=replay["run_id"],
                        )
                    reason = ("idempotent_active" if replay["status"] == "active"
                              else "idempotent_terminal")
                    return self._claim_result(replay, acquired=False, reason=reason)

            same_run = connection.execute(
                "SELECT * FROM call_run_claims WHERE run_id=?", (requested_run_id,)
            ).fetchone()
            if same_run is not None:
                if same_run["job_id"] != job_id:
                    raise ValueError("run_id already belongs to another job")
                if same_run["status"] == "active" and self._is_stale(same_run, timestamp):
                    reclaimed = self._reclaim(
                        connection, same_run, owner_token=owner_token,
                        timestamp=timestamp, stale_after=stale_after,
                        metadata_json=metadata_json if metadata is not None else None,
                    )
                    return self._claim_result(
                        reclaimed, acquired=True, reason="stale_reclaimed",
                        owner_token=owner_token, restarted=True,
                        previous_run_id=same_run["run_id"],
                    )
                reason = "run_active" if same_run["status"] == "active" else "run_terminal"
                return self._claim_result(same_run, acquired=False, reason=reason)

            active = connection.execute(
                """SELECT * FROM call_run_claims
                   WHERE job_id=? AND status='active' LIMIT 1""",
                (job_id,),
            ).fetchone()
            previous_run_id = None
            restarted = False
            if active is not None:
                if not self._is_stale(active, timestamp):
                    return self._claim_result(active, acquired=False, reason="active_conflict")
                previous_run_id = active["run_id"]
                restarted = True
                connection.execute(
                    """UPDATE call_run_claims
                       SET status='stale', finished_at=?, updated_at=?,
                           replaced_by_run_id=?
                       WHERE run_id=? AND status='active'""",
                    (timestamp, timestamp, requested_run_id, previous_run_id),
                )

            inserted = self._insert(
                connection,
                job_id=job_id,
                run_id=requested_run_id,
                idempotency_key=idempotency_key,
                owner_token=owner_token,
                timestamp=timestamp,
                stale_after=stale_after,
                metadata_json=metadata_json,
            )
            return self._claim_result(
                inserted,
                acquired=True,
                reason="stale_replaced" if restarted else "acquired",
                owner_token=owner_token,
                restarted=restarted,
                previous_run_id=previous_run_id,
            )

        return self._write(operation)

    def heartbeat(
        self,
        job_id: str,
        run_id: str,
        owner_token: str,
        *,
        stale_after: float | None = None,
        now: float | None = None,
    ) -> bool:
        """Extend a lease if and only if the caller still owns its token."""

        if not (job_id or "").strip() or not (run_id or "").strip() \
                or not (owner_token or "").strip():
            raise ValueError("job_id, run_id and owner_token are required")
        if stale_after is not None and float(stale_after) <= 0:
            raise ValueError("stale_after must be positive")
        timestamp = float(time.time() if now is None else now)

        def operation(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                """SELECT lease_seconds FROM call_run_claims
                   WHERE job_id=? AND run_id=? AND owner_token=? AND status='active'""",
                (job_id, run_id, owner_token),
            ).fetchone()
            if row is None:
                return False
            lease_seconds = float(stale_after if stale_after is not None else row["lease_seconds"])
            cursor = connection.execute(
                """UPDATE call_run_claims
                   SET heartbeat_at=?, lease_seconds=?, lease_expires_at=?, updated_at=?
                   WHERE job_id=? AND run_id=? AND owner_token=? AND status='active'""",
                (timestamp, lease_seconds, timestamp + lease_seconds, timestamp,
                 job_id, run_id, owner_token),
            )
            return cursor.rowcount == 1

        return self._write(operation)

    def finish(
        self,
        job_id: str,
        run_id: str,
        owner_token: str,
        status: TerminalStatus,
        *,
        now: float | None = None,
    ) -> bool:
        """Release ownership with a terminal status, guarded by fencing token.

        Repeating the same finish is harmless and returns ``False``; it cannot
        overwrite a replacement worker's state.
        """

        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("status must be completed, failed or cancelled")
        if not (job_id or "").strip() or not (run_id or "").strip() \
                or not (owner_token or "").strip():
            raise ValueError("job_id, run_id and owner_token are required")
        timestamp = float(time.time() if now is None else now)

        def operation(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """UPDATE call_run_claims
                   SET status=?, finished_at=?, updated_at=?
                   WHERE job_id=? AND run_id=? AND owner_token=? AND status='active'""",
                (status, timestamp, timestamp, job_id, run_id, owner_token),
            )
            return cursor.rowcount == 1

        return self._write(operation)

    def expire_stale(self, *, job_id: str | None = None, now: float | None = None) -> list[str]:
        """Mark expired leases stale, useful as an explicit startup sweep."""

        timestamp = float(time.time() if now is None else now)
        if job_id is not None and not job_id.strip():
            raise ValueError("job_id cannot be blank")

        def operation(connection: sqlite3.Connection) -> list[str]:
            sql = """SELECT run_id FROM call_run_claims
                     WHERE status='active' AND lease_expires_at<=?"""
            params: list[Any] = [timestamp]
            if job_id is not None:
                sql += " AND job_id=?"
                params.append(job_id)
            run_ids = [row["run_id"] for row in connection.execute(sql, params).fetchall()]
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                connection.execute(
                    f"""UPDATE call_run_claims
                        SET status='stale', finished_at=?, updated_at=?
                        WHERE status='active' AND run_id IN ({placeholders})""",
                    [timestamp, timestamp, *run_ids],
                )
            return run_ids

        return self._write(operation)

    @staticmethod
    def _state(row: sqlite3.Row | None) -> RunClaimState | None:
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return RunClaimState(
            job_id=row["job_id"],
            run_id=row["run_id"],
            status=row["status"],
            generation=int(row["generation"]),
            claimed_at=float(row["claimed_at"]),
            heartbeat_at=float(row["heartbeat_at"]),
            lease_expires_at=float(row["lease_expires_at"]),
            finished_at=float(row["finished_at"]) if row["finished_at"] is not None else None,
            idempotency_key=row["idempotency_key"],
            replaced_by_run_id=row["replaced_by_run_id"],
            metadata=metadata,
        )

    def get(self, run_id: str) -> RunClaimState | None:
        """Read a claim by run id without exposing its owner token."""

        self._ensure_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM call_run_claims WHERE run_id=?", (run_id,)
            ).fetchone()
            return self._state(row)
        finally:
            connection.close()

    def active_for_job(self, job_id: str) -> RunClaimState | None:
        """Return the current active lease, including an expired unswept one."""

        self._ensure_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM call_run_claims
                   WHERE job_id=? AND status='active' LIMIT 1""",
                (job_id,),
            ).fetchone()
            return self._state(row)
        finally:
            connection.close()

    def by_idempotency(self, job_id: str, idempotency_key: str) -> RunClaimState | None:
        """Resolve a previous request before recomputing mutable eligibility.
        This lets an HTTP retry return its original run even after that run has
        completed all vendors."""
        job_id = (job_id or "").strip()
        idempotency_key = (idempotency_key or "").strip()
        if not job_id or not idempotency_key:
            raise ValueError("job_id and idempotency_key are required")
        if len(idempotency_key) > 128:
            raise ValueError("idempotency_key cannot exceed 128 characters")
        self._ensure_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM call_run_claims
                   WHERE job_id=? AND idempotency_key=?""",
                (job_id, idempotency_key),
            ).fetchone()
            return self._state(row)
        finally:
            connection.close()


_default_store = RunClaimStore()


def claim_run(job_id: str, **kwargs) -> RunClaim:
    """Claim through the process-default store (``config.DB_PATH``)."""

    return _default_store.claim(job_id, **kwargs)


def heartbeat_run(job_id: str, run_id: str, owner_token: str, **kwargs) -> bool:
    return _default_store.heartbeat(job_id, run_id, owner_token, **kwargs)


def finish_run(
    job_id: str,
    run_id: str,
    owner_token: str,
    status: TerminalStatus,
    **kwargs,
) -> bool:
    return _default_store.finish(job_id, run_id, owner_token, status, **kwargs)


def find_idempotent_run(job_id: str, idempotency_key: str) -> RunClaimState | None:
    return _default_store.by_idempotency(job_id, idempotency_key)
