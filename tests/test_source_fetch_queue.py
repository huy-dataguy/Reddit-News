"""tests/test_source_fetch_queue.py — Tests for SourceFetchQueue lease/idempotency semantics.

Verifies:
- Idempotent enqueue (same URL → same candidate_id)
- Claim atomicity and lease ownership
- Stale lease recovery (expired leases re-queued)
- Success/failure completion semantics
- Dead-letter after max_attempts
- Policy rejection is terminal
- No concurrent claim race (both workers cannot claim same item)
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from reddit_crawler.source_queue import (
    CandidateState,
    SourceFetchQueue,
    _idempotency_key,
)


def _make_queue() -> tuple[SourceFetchQueue, str]:
    """Create a fresh queue backed by a temp database."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "fetch_queue.db")
    return SourceFetchQueue(db_path), tmpdir


class IdempotencyTests(unittest.TestCase):

    def test_enqueue_same_url_idempotent(self) -> None:
        """Enqueueing the same URL twice returns same candidate_id."""
        queue, _ = _make_queue()
        cid1, is_new1 = queue.enqueue("https://example.com/article", policy_version="1.0")
        cid2, is_new2 = queue.enqueue("https://example.com/article", policy_version="1.0")
        self.assertEqual(cid1, cid2)
        self.assertTrue(is_new1)
        self.assertFalse(is_new2)

    def test_different_urls_different_candidates(self) -> None:
        """Different URLs produce different candidate IDs."""
        queue, _ = _make_queue()
        cid1, _ = queue.enqueue("https://example.com/a")
        cid2, _ = queue.enqueue("https://example.com/b")
        self.assertNotEqual(cid1, cid2)

    def test_different_policy_version_different_candidate(self) -> None:
        """Same URL but different policy version creates a new candidate."""
        queue, _ = _make_queue()
        cid1, _ = queue.enqueue("https://example.com/a", policy_version="1.0")
        cid2, is_new = queue.enqueue("https://example.com/a", policy_version="2.0")
        self.assertNotEqual(cid1, cid2)
        self.assertTrue(is_new)

    def test_idempotency_key_deterministic(self) -> None:
        """_idempotency_key is deterministic for same (url, policy)."""
        key1 = _idempotency_key("https://example.com/", "1.0")
        key2 = _idempotency_key("https://example.com/", "1.0")
        key3 = _idempotency_key("https://example.com/", "2.0")
        self.assertEqual(key1, key2)
        self.assertNotEqual(key1, key3)


class ClaimTests(unittest.TestCase):

    def test_claim_returns_candidate(self) -> None:
        """claim() returns a candidate dict when one is available."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/a")
        candidate = queue.claim("worker-1")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["state"], CandidateState.LEASED)
        self.assertEqual(candidate["lease_owner"], "worker-1")

    def test_claim_empty_queue_returns_none(self) -> None:
        """claim() returns None when queue is empty."""
        queue, _ = _make_queue()
        result = queue.claim("worker-1")
        self.assertIsNone(result)

    def test_two_workers_cannot_claim_same_item(self) -> None:
        """Concurrent claims: only one worker gets the item."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/only-one")

        c1 = queue.claim("worker-1")
        c2 = queue.claim("worker-2")

        # First worker got it, second got nothing
        self.assertIsNotNone(c1)
        self.assertIsNone(c2)

    def test_claim_sets_lease_expiry(self) -> None:
        """Claimed candidate has lease_expires_at set in the future."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/b")
        now = time.time()
        candidate = queue.claim("worker-1", lease_seconds=60.0, now=now)
        self.assertAlmostEqual(candidate["lease_expires_at"], now + 60.0, delta=1.0)

    def test_pending_count_decreases_after_claim(self) -> None:
        """Claiming reduces the pending count."""
        queue, _ = _make_queue()
        queue.enqueue("https://a.com/1")
        queue.enqueue("https://a.com/2")
        self.assertEqual(queue.pending_count(), 2)
        queue.claim("worker-1")
        self.assertEqual(queue.pending_count(), 1)


class LeaseRecoveryTests(unittest.TestCase):

    def test_stale_lease_reclaimed(self) -> None:
        """Expired leases are automatically reclaimed on next claim call."""
        queue, _ = _make_queue()
        # Use real time for enqueue (queue sets next_attempt_at=time.time())
        # For claim, we simulate old claim at time T and recovery at T+lease+1
        queue.enqueue("https://example.com/recovery")
        real_now = time.time()

        # Claim with a very short lease using a fake "now" that is close to real time
        candidate = queue.claim("worker-crashed", lease_seconds=30.0, now=real_now)
        self.assertIsNotNone(candidate)

        # After lease expiry: use real_now + lease_duration + 1
        later_now = real_now + 31.0
        recovered = queue.claim("worker-2", now=later_now)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["lease_owner"], "worker-2")

    def test_active_lease_not_reclaimed(self) -> None:
        """Active (non-expired) leases are not reclaimed."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/active")
        fake_now = 1000.0

        queue.claim("worker-1", lease_seconds=300.0, now=fake_now)
        # Before expiry
        recovered = queue.claim("worker-2", now=fake_now + 100.0)
        self.assertIsNone(recovered)  # Should not reclaim


class CompletionTests(unittest.TestCase):

    def test_success_marks_succeeded(self) -> None:
        """Completing with success=True marks candidate SUCCEEDED."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/success")
        candidate = queue.claim("worker-1")
        result = queue.complete(candidate["candidate_id"], "worker-1", success=True)
        self.assertTrue(result)
        stats = queue.stats()
        self.assertIn(CandidateState.SUCCEEDED, stats)

    def test_failure_retryable_requeues(self) -> None:
        """Retryable failure re-queues the candidate."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/retry", max_attempts=3)
        candidate = queue.claim("worker-1")
        result = queue.complete(
            candidate["candidate_id"], "worker-1",
            success=False, error_code="rate_limited", error_class="retryable",
        )
        self.assertTrue(result)
        stats = queue.stats()
        # Should be re-queued
        self.assertIn(CandidateState.QUEUED, stats)

    def test_max_attempts_dead_letters(self) -> None:
        """Candidate enters DEAD_LETTER after max_attempts failures."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/dead", max_attempts=2)
        real_now = time.time()

        # First claim + failure
        c = queue.claim("worker-1", now=real_now)
        if c:
            queue.complete(c["candidate_id"], "worker-1", success=False,
                          error_code="server_error", error_class="retryable",
                          now=real_now, next_attempt_after=real_now + 1)

            # Second claim + failure (max_attempts=2, now at attempt 2)
            c2 = queue.claim("worker-2", now=real_now + 2)
            if c2:
                queue.complete(c2["candidate_id"], "worker-2", success=False,
                              error_code="server_error", error_class="retryable",
                              now=real_now + 2)

        stats = queue.stats()
        # Should end up in dead_letter or failed_terminal
        has_terminal = (
            CandidateState.DEAD_LETTER in stats
            or CandidateState.FAILED_TERMINAL in stats
        )
        self.assertTrue(has_terminal, f"Expected dead_letter/failed_terminal in stats: {stats}")

    def test_policy_rejection_is_terminal(self) -> None:
        """Policy rejection moves candidate to REJECTED_POLICY (terminal)."""
        queue, _ = _make_queue()
        cid, _ = queue.enqueue("https://192.168.1.1/private")
        queue.reject_policy(cid, "ip_private_rejected")
        stats = queue.stats()
        self.assertIn(CandidateState.REJECTED_POLICY, stats)
        # Should not be in queued anymore
        self.assertNotIn(CandidateState.QUEUED, stats)

    def test_wrong_worker_cannot_complete(self) -> None:
        """Worker cannot complete a lease owned by another worker."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/ownership")
        candidate = queue.claim("worker-1")
        result = queue.complete(candidate["candidate_id"], "worker-IMPOSTER", success=True)
        self.assertFalse(result)


class StatsTests(unittest.TestCase):

    def test_stats_returns_state_counts(self) -> None:
        """stats() returns dict with state → count."""
        queue, _ = _make_queue()
        queue.enqueue("https://a.com/1")
        queue.enqueue("https://b.com/2")
        stats = queue.stats()
        self.assertIsInstance(stats, dict)
        self.assertIn(CandidateState.QUEUED, stats)
        self.assertEqual(stats[CandidateState.QUEUED], 2)

    def test_pending_count_respects_next_attempt_at(self) -> None:
        """pending_count() excludes candidates not yet ready (deferred)."""
        queue, _ = _make_queue()
        queue.enqueue("https://example.com/ready")
        # Manually set next_attempt_at far in the future
        import sqlite3
        conn = sqlite3.connect(queue.db_path)
        conn.execute(
            "UPDATE source_candidate SET next_attempt_at = ? WHERE canonical_url = ?",
            (time.time() + 9999, "https://example.com/ready"),
        )
        conn.commit()
        conn.close()
        # Should not count as pending
        self.assertEqual(queue.pending_count(), 0)


if __name__ == "__main__":
    unittest.main()
