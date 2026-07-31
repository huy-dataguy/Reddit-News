"""reddit_crawler/source_queue.py — Source content fetch queue with lease/idempotency/retry.

Implements the fetch candidate queue from safe-source-content-ingestion spec:
- Candidates with states: discovered → policy_pending → queued → leased → fetching → succeeded/failed
- Lease ownership prevents concurrent duplicate fetches
- Idempotency key: canonical_url + policy_version
- Crash recovery: stale leases expire automatically (default 5 min)
- Dead-letter after max_attempts exceeded
- No network calls in this module

All DB operations use temp databases in tests. Never reads live reddit.db.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class CandidateState(str, Enum):
    DISCOVERED = "discovered"
    POLICY_PENDING = "policy_pending"
    QUEUED = "queued"
    LEASED = "leased"
    FETCHING = "fetching"
    SUCCEEDED = "succeeded"
    REJECTED_POLICY = "rejected_policy"
    DEFERRED = "deferred"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    DEAD_LETTER = "dead_letter"
    EXTRACTED = "extracted"
    DQ_PASSED = "dq_passed"
    DQ_FAILED = "dq_failed"


# States that allow re-queuing
RETRYABLE_STATES = {CandidateState.FAILED_RETRYABLE, CandidateState.DEFERRED}

# Terminal states (no further processing)
TERMINAL_STATES = {
    CandidateState.REJECTED_POLICY,
    CandidateState.FAILED_TERMINAL,
    CandidateState.DEAD_LETTER,
    CandidateState.DQ_FAILED,
    CandidateState.DQ_PASSED,
    CandidateState.SUCCEEDED,
}

DEFAULT_LEASE_SECONDS = 300  # 5 minutes
DEFAULT_MAX_ATTEMPTS = 3


SOURCE_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_candidate (
    candidate_id        TEXT PRIMARY KEY,
    canonical_url       TEXT NOT NULL,
    discovered_from     TEXT,       -- JSON list of source post/comment IDs
    source_authority    TEXT,       -- "high" | "medium" | "low" | "unknown"
    priority            INTEGER DEFAULT 50,
    policy_version      TEXT NOT NULL,
    state               TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    attempts            INTEGER DEFAULT 0,
    max_attempts        INTEGER DEFAULT 3,
    next_attempt_at     REAL,
    lease_owner         TEXT,
    lease_expires_at    REAL,
    last_error          TEXT,       -- sanitized error string
    fetch_record_id     TEXT,       -- references source_fetch_record.fetch_id
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_candidate_state ON source_candidate(state, priority DESC, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_source_candidate_idempotency ON source_candidate(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_source_candidate_lease ON source_candidate(lease_expires_at) WHERE state = 'leased';

CREATE TABLE IF NOT EXISTS source_fetch_record (
    fetch_id            TEXT PRIMARY KEY,
    candidate_id        TEXT NOT NULL REFERENCES source_candidate(candidate_id),
    request_url         TEXT NOT NULL,
    redirect_chain      TEXT,       -- JSON list of {url, status, decision}
    final_url           TEXT,
    http_status         INTEGER,
    mime_type           TEXT,
    wire_bytes          INTEGER,
    decoded_bytes       INTEGER,
    body_sha256         TEXT,
    bronze_uri          TEXT,
    error_code          TEXT,
    error_class         TEXT,       -- retryable | policy_rejection | auth_failure | terminal
    fetched_at          REAL NOT NULL,
    policy_version      TEXT NOT NULL
);
"""


def _idempotency_key(canonical_url: str, policy_version: str) -> str:
    """Compute deterministic idempotency key for a (url, policy) pair."""
    payload = f"{canonical_url}::{policy_version}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class SourceFetchQueue:
    """SQLite-backed fetch queue with lease semantics.

    Never reads or writes to the live reddit.db unless explicitly passed its path.
    Default: uses a provided db_path (tests use tempfiles).
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SOURCE_QUEUE_SCHEMA)

    def enqueue(
        self,
        canonical_url: str,
        *,
        discovered_from: Optional[list[str]] = None,
        source_authority: str = "unknown",
        priority: int = 50,
        policy_version: str = "1.0",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> tuple[str, bool]:
        """Enqueue a URL for fetching. Idempotent — returns (candidate_id, is_new).

        If the URL already exists (same idempotency key), returns existing ID.
        """
        idem_key = _idempotency_key(canonical_url, policy_version)
        now = time.time()

        with self._conn() as conn:
            # Check existing
            existing = conn.execute(
                "SELECT candidate_id, state FROM source_candidate WHERE idempotency_key = ?",
                (idem_key,),
            ).fetchone()

            if existing:
                return existing["candidate_id"], False

            candidate_id = str(uuid.uuid4())[:12]
            conn.execute(
                """INSERT INTO source_candidate
                   (candidate_id, canonical_url, discovered_from, source_authority, priority,
                    policy_version, state, idempotency_key, attempts, max_attempts,
                    next_attempt_at, lease_owner, lease_expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL, ?, ?)""",
                (
                    candidate_id, canonical_url,
                    json.dumps(discovered_from or []),
                    source_authority, priority,
                    policy_version,
                    CandidateState.QUEUED,
                    idem_key, max_attempts,
                    now,  # next_attempt_at = now (immediately ready)
                    now, now,
                ),
            )
            return candidate_id, True

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        now: Optional[float] = None,
    ) -> Optional[dict]:
        """Claim a candidate for processing (atomic lease acquisition).

        Selects the highest-priority queued candidate that:
        - Is in QUEUED state
        - next_attempt_at <= now

        Returns candidate dict or None if queue is empty.
        Also reclaims stale leases (lease_expires_at < now).
        """
        now = now or time.time()
        lease_expires = now + lease_seconds

        with self._conn() as conn:
            # First: reclaim stale leases (set to queued for immediate retry)
            conn.execute(
                """UPDATE source_candidate
                   SET state = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                       next_attempt_at = ?, updated_at = ?
                   WHERE state = 'leased' AND lease_expires_at < ?""",
                (now, now, now),
            )

            # Claim highest-priority ready candidate
            candidate = conn.execute(
                """SELECT * FROM source_candidate
                   WHERE state = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1""",
                (now,),
            ).fetchone()

            if not candidate:
                return None

            conn.execute(
                """UPDATE source_candidate
                   SET state = 'leased', lease_owner = ?, lease_expires_at = ?,
                       attempts = attempts + 1, updated_at = ?
                   WHERE candidate_id = ? AND state = 'queued'""",
                (worker_id, lease_expires, now, candidate["candidate_id"]),
            )

            # Re-fetch after update
            updated = conn.execute(
                "SELECT * FROM source_candidate WHERE candidate_id = ?",
                (candidate["candidate_id"],),
            ).fetchone()

            if updated and updated["lease_owner"] == worker_id:
                return dict(updated)
            return None

    def complete(
        self,
        candidate_id: str,
        worker_id: str,
        *,
        success: bool,
        error_code: Optional[str] = None,
        error_class: Optional[str] = None,
        fetch_record_id: Optional[str] = None,
        next_attempt_after: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Mark a leased candidate as succeeded or failed.

        Returns True if the candidate was updated (owned by this worker).
        """
        now = now or time.time()

        with self._conn() as conn:
            # Verify ownership
            candidate = conn.execute(
                "SELECT * FROM source_candidate WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()

            if not candidate or candidate["lease_owner"] != worker_id:
                return False  # Not owned by this worker

            if success:
                new_state = CandidateState.SUCCEEDED
                last_error = None
            else:
                # Determine if terminal or retryable
                attempts = candidate["attempts"]
                max_attempts = candidate["max_attempts"]
                is_terminal = (
                    error_class in ("policy_rejection", "auth_failure", "terminal")
                    or attempts >= max_attempts
                )
                if is_terminal:
                    new_state = CandidateState.DEAD_LETTER if attempts >= max_attempts else CandidateState.FAILED_TERMINAL
                else:
                    new_state = CandidateState.FAILED_RETRYABLE
                last_error = error_code

            conn.execute(
                """UPDATE source_candidate
                   SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                       last_error = ?, fetch_record_id = ?,
                       next_attempt_at = ?, updated_at = ?
                   WHERE candidate_id = ? AND lease_owner = ?""",
                (
                    new_state, last_error, fetch_record_id,
                    next_attempt_after, now,
                    candidate_id, worker_id,
                ),
            )

            # Re-queue retryable
            if new_state == CandidateState.FAILED_RETRYABLE:
                retry_at = next_attempt_after or (now + 300)
                conn.execute(
                    "UPDATE source_candidate SET state = 'queued', next_attempt_at = ? WHERE candidate_id = ?",
                    (retry_at, candidate_id),
                )
            return True

    def reject_policy(self, candidate_id: str, reason: str, *, now: Optional[float] = None) -> None:
        """Mark a candidate as rejected by policy (terminal)."""
        now = now or time.time()
        with self._conn() as conn:
            conn.execute(
                """UPDATE source_candidate
                   SET state = 'rejected_policy', last_error = ?,
                       lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                   WHERE candidate_id = ?""",
                (reason[:500], now, candidate_id),
            )

    def stats(self) -> dict:
        """Return queue statistics (no sensitive data)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) as cnt FROM source_candidate GROUP BY state"
            ).fetchall()
        return {row["state"]: row["cnt"] for row in rows}

    def pending_count(self, *, now: Optional[float] = None) -> int:
        """Return count of queued candidates ready to process."""
        now = now or time.time()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM source_candidate WHERE state = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)",
                (now,),
            ).fetchone()
        return row[0] if row else 0
