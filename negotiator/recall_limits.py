"""Persistent, atomic per-vendor recall limits.

A vendor may be recalled at most twice for the same job.  A slot is consumed
as soon as the recall is reserved and is never released by a later status
change: reserved, queued, calling, completed, failed and cancelled attempts all
count.  This prevents retries, process restarts, or concurrent workers from
creating an unbounded callback loop.

The integration boundary is intentionally small::

    slot = reserve(job_id, company_id, reservation_id="run_1:vendor_2:recall")
    if slot is None:
        return                         # both recall slots are already consumed
    attach_call(job_id, company_id, reservation_id, call_id, status="queued")

``reservation_id`` is an idempotency key within a job/company scope.  Replaying
it returns the original slot even after the limit has otherwise been reached.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DB_PATH


HARD_MAX_RECALLS = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_reservations (
    job_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    slot INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 2),
    call_id TEXT,
    status TEXT NOT NULL DEFAULT 'reserved',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, company_id, reservation_id),
    UNIQUE (job_id, company_id, slot)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recall_reservation_call
    ON recall_reservations(call_id) WHERE call_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_recall_reservation_scope
    ON recall_reservations(job_id, company_id, slot);
"""

_schema_lock = threading.Lock()


@dataclass(frozen=True)
class RecallReservation:
    """Read-only state for one consumed recall slot."""

    job_id: str
    company_id: str
    reservation_id: str
    slot: int
    call_id: str | None
    status: str
    created_at: float
    updated_at: float
    metadata: dict[str, Any]


class RecallLimitStore:
    """SQLite-backed hard limit for callbacks to a job/company pair.

    ``BEGIN IMMEDIATE`` serializes the short check-and-insert transaction.
    Schema constraints provide a second line of defense if another writer
    bypasses this class.  WAL and a busy timeout let dashboard readers proceed
    while multiple worker processes contend for the two slots.
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
    def _scope(
        job_id: str,
        company_id: str,
        reservation_id: str,
    ) -> tuple[str, str, str]:
        values = tuple((value or "").strip() for value in
                       (job_id, company_id, reservation_id))
        if not all(values):
            raise ValueError("job_id, company_id and reservation_id are required")
        return values

    @staticmethod
    def _optional_text(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} cannot be blank")
        return normalized

    @staticmethod
    def _limit(max_recalls: int) -> int:
        if isinstance(max_recalls, bool):
            raise ValueError("max_recalls must be an integer")
        try:
            requested = int(max_recalls)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_recalls must be an integer") from exc
        if requested < 0:
            raise ValueError("max_recalls cannot be negative")
        # Configuration is allowed to lower the safety cap, never raise it.
        return min(HARD_MAX_RECALLS, requested)

    @staticmethod
    def _metadata_json(metadata: dict[str, Any] | None) -> str:
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("metadata must be a dict")
        return json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))

    def reserve(
        self,
        job_id: str,
        company_id: str,
        reservation_id: str,
        *,
        max_recalls: int = HARD_MAX_RECALLS,
        call_id: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> int | None:
        """Reserve and return slot 1 or 2; return ``None`` when exhausted.

        The same ``reservation_id`` always returns its original slot.  On an
        idempotent replay, optional ``call_id`` fills a previously empty call
        link and optional ``status`` updates the audit state.  Neither operation
        can free or allocate another slot.
        """

        job_id, company_id, reservation_id = self._scope(
            job_id, company_id, reservation_id
        )
        limit = self._limit(max_recalls)
        call_id = self._optional_text(call_id, "call_id")
        status = self._optional_text(status, "status")
        metadata_json = self._metadata_json(metadata)
        timestamp = float(time.time() if now is None else now)

        def operation(connection: sqlite3.Connection) -> int | None:
            existing = connection.execute(
                """SELECT slot, call_id FROM recall_reservations
                   WHERE job_id=? AND company_id=? AND reservation_id=?""",
                (job_id, company_id, reservation_id),
            ).fetchone()
            if existing is not None:
                current_call_id = existing["call_id"]
                if call_id is not None and current_call_id not in {None, call_id}:
                    raise ValueError("reservation is already attached to another call")
                updates: list[str] = []
                params: list[Any] = []
                if call_id is not None and current_call_id is None:
                    self._ensure_call_available(connection, call_id, job_id,
                                                company_id, reservation_id)
                    updates.append("call_id=?")
                    params.append(call_id)
                if status is not None:
                    updates.append("status=?")
                    params.append(status)
                if updates:
                    updates.append("updated_at=?")
                    params.append(timestamp)
                    params.extend((job_id, company_id, reservation_id))
                    connection.execute(
                        f"UPDATE recall_reservations SET {', '.join(updates)} "
                        "WHERE job_id=? AND company_id=? AND reservation_id=?",
                        params,
                    )
                return int(existing["slot"])

            if limit == 0:
                return None
            used = {
                int(row["slot"])
                for row in connection.execute(
                    """SELECT slot FROM recall_reservations
                       WHERE job_id=? AND company_id=?""",
                    (job_id, company_id),
                ).fetchall()
            }
            slot = next((candidate for candidate in range(1, limit + 1)
                         if candidate not in used), None)
            if slot is None:
                return None
            if call_id is not None:
                self._ensure_call_available(connection, call_id, job_id,
                                            company_id, reservation_id)
            connection.execute(
                """INSERT INTO recall_reservations
                   (job_id, company_id, reservation_id, slot, call_id, status,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, company_id, reservation_id, slot, call_id,
                 status or "reserved", timestamp, timestamp, metadata_json),
            )
            return slot

        return self._write(operation)

    @staticmethod
    def _ensure_call_available(
        connection: sqlite3.Connection,
        call_id: str,
        job_id: str,
        company_id: str,
        reservation_id: str,
    ) -> None:
        owner = connection.execute(
            """SELECT job_id, company_id, reservation_id
               FROM recall_reservations WHERE call_id=?""",
            (call_id,),
        ).fetchone()
        if owner is not None and tuple(owner) != (job_id, company_id, reservation_id):
            raise ValueError("call_id is already attached to another recall reservation")

    def attach_call(
        self,
        job_id: str,
        company_id: str,
        reservation_id: str,
        call_id: str,
        *,
        status: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Attach a call to an existing reservation, idempotently.

        Returns ``False`` when the reservation does not exist.  Reattaching the
        same call is successful; attaching a different call is rejected.
        """

        job_id, company_id, reservation_id = self._scope(
            job_id, company_id, reservation_id
        )
        call_id = self._optional_text(call_id, "call_id")
        status = self._optional_text(status, "status")
        timestamp = float(time.time() if now is None else now)

        def operation(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                """SELECT call_id FROM recall_reservations
                   WHERE job_id=? AND company_id=? AND reservation_id=?""",
                (job_id, company_id, reservation_id),
            ).fetchone()
            if row is None:
                return False
            if row["call_id"] not in {None, call_id}:
                raise ValueError("reservation is already attached to another call")
            self._ensure_call_available(connection, call_id, job_id,
                                        company_id, reservation_id)
            connection.execute(
                """UPDATE recall_reservations
                   SET call_id=?, status=COALESCE(?, status), updated_at=?
                   WHERE job_id=? AND company_id=? AND reservation_id=?""",
                (call_id, status, timestamp, job_id, company_id, reservation_id),
            )
            return True

        return self._write(operation)

    def set_status(
        self,
        job_id: str,
        company_id: str,
        reservation_id: str,
        status: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Update audit status without changing whether the slot is consumed."""

        job_id, company_id, reservation_id = self._scope(
            job_id, company_id, reservation_id
        )
        status = self._optional_text(status, "status")
        timestamp = float(time.time() if now is None else now)

        def operation(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                """UPDATE recall_reservations SET status=?, updated_at=?
                   WHERE job_id=? AND company_id=? AND reservation_id=?""",
                (status, timestamp, job_id, company_id, reservation_id),
            )
            return cursor.rowcount == 1

        return self._write(operation)

    @staticmethod
    def _state(row: sqlite3.Row | None) -> RecallReservation | None:
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return RecallReservation(
            job_id=row["job_id"],
            company_id=row["company_id"],
            reservation_id=row["reservation_id"],
            slot=int(row["slot"]),
            call_id=row["call_id"],
            status=row["status"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            metadata=metadata,
        )

    def get(
        self,
        job_id: str,
        company_id: str,
        reservation_id: str,
    ) -> RecallReservation | None:
        job_id, company_id, reservation_id = self._scope(
            job_id, company_id, reservation_id
        )
        self._ensure_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM recall_reservations
                   WHERE job_id=? AND company_id=? AND reservation_id=?""",
                (job_id, company_id, reservation_id),
            ).fetchone()
            return self._state(row)
        finally:
            connection.close()

    def for_company(self, job_id: str, company_id: str) -> list[RecallReservation]:
        """Return all consumed slots for one job/company scope."""

        job_id = (job_id or "").strip()
        company_id = (company_id or "").strip()
        if not job_id or not company_id:
            raise ValueError("job_id and company_id are required")
        self._ensure_schema()
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM recall_reservations
                   WHERE job_id=? AND company_id=? ORDER BY slot""",
                (job_id, company_id),
            ).fetchall()
            return [self._state(row) for row in rows]
        finally:
            connection.close()


_default_store = RecallLimitStore()


def reserve(job_id: str, company_id: str, reservation_id: str, **kwargs) -> int | None:
    """Reserve through the process-default store (``config.DB_PATH``)."""

    return _default_store.reserve(job_id, company_id, reservation_id, **kwargs)


def attach_call(
    job_id: str,
    company_id: str,
    reservation_id: str,
    call_id: str,
    **kwargs,
) -> bool:
    return _default_store.attach_call(
        job_id, company_id, reservation_id, call_id, **kwargs
    )


def set_status(
    job_id: str,
    company_id: str,
    reservation_id: str,
    status: str,
    **kwargs,
) -> bool:
    return _default_store.set_status(
        job_id, company_id, reservation_id, status, **kwargs
    )


def for_company(job_id: str, company_id: str) -> list[RecallReservation]:
    return _default_store.for_company(job_id, company_id)
