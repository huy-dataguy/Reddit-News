"""tests/test_llm_circuit_breaker.py — Tests for LLM circuit breaker and budget manager.

Covers:
- Circuit breaker CLOSED → OPEN → HALF_OPEN → CLOSED lifecycle
- Auth failure is terminal (no auto-probe)
- Budget windows with request/token/cost limits
- Retry taxonomy classification
- Fake clock for time-sensitive behavior
"""
from __future__ import annotations

import time
import unittest

from reddit_crawler.llm_control import (
    BudgetManager,
    BudgetWindow,
    CircuitBreaker,
    CircuitBreakerState,
    LLMJobV1,
    RetryClass,
    WorkloadClass,
    WORKLOAD_PRIORITIES,
    classify_retry,
)


class CircuitBreakerTests(unittest.TestCase):

    def _make_breaker(self, threshold: int = 3, cooldown: float = 300.0) -> CircuitBreaker:
        return CircuitBreaker(
            provider="gemini",
            model_pattern="gemini-*",
            failure_threshold=threshold,
            cooldown_seconds=cooldown,
            window_seconds=600.0,
        )

    def test_initial_state_is_closed(self) -> None:
        """Circuit starts in CLOSED state and allows requests."""
        breaker = self._make_breaker()
        self.assertEqual(breaker.state, CircuitBreakerState.CLOSED)
        self.assertTrue(breaker.can_attempt())

    def test_opens_after_threshold_failures(self) -> None:
        """Circuit opens after N retryable failures."""
        breaker = self._make_breaker(threshold=3)
        fake_now = 1000.0
        for _ in range(3):
            breaker.record_failure(RetryClass.RETRYABLE, now=fake_now)
        self.assertEqual(breaker.state, CircuitBreakerState.OPEN)
        self.assertFalse(breaker.can_attempt(now=fake_now))

    def test_half_open_after_cooldown(self) -> None:
        """Circuit transitions to HALF_OPEN after cooldown period."""
        breaker = self._make_breaker(threshold=2, cooldown=60.0)
        fake_now = 1000.0
        breaker.record_failure(RetryClass.RETRYABLE, now=fake_now)
        breaker.record_failure(RetryClass.RETRYABLE, now=fake_now)
        self.assertEqual(breaker.state, CircuitBreakerState.OPEN)

        # Before cooldown — still open
        self.assertFalse(breaker.can_attempt(now=fake_now + 30.0))

        # After cooldown — should allow one probe (HALF_OPEN)
        result = breaker.can_attempt(now=fake_now + 61.0)
        self.assertTrue(result)
        self.assertEqual(breaker.state, CircuitBreakerState.HALF_OPEN)

    def test_success_closes_from_half_open(self) -> None:
        """Successful probe in HALF_OPEN closes the circuit."""
        breaker = self._make_breaker(threshold=2, cooldown=60.0)
        fake_now = 1000.0
        breaker.record_failure(RetryClass.RETRYABLE, now=fake_now)
        breaker.record_failure(RetryClass.RETRYABLE, now=fake_now)
        # Transition to half-open
        breaker.can_attempt(now=fake_now + 61.0)
        self.assertEqual(breaker.state, CircuitBreakerState.HALF_OPEN)

        # Success closes it
        breaker.record_success(now=fake_now + 62.0)
        self.assertEqual(breaker.state, CircuitBreakerState.CLOSED)
        self.assertEqual(breaker.failure_count, 0)

    def test_auth_failure_is_terminal(self) -> None:
        """Auth failure opens circuit permanently — no auto-probe."""
        breaker = self._make_breaker()
        fake_now = 1000.0
        breaker.record_failure(RetryClass.AUTH_FAILURE, now=fake_now)
        self.assertEqual(breaker.state, CircuitBreakerState.OPEN)
        self.assertTrue(breaker.auth_failure)

        # Even after long cooldown — still blocked (requires operator action)
        self.assertFalse(breaker.can_attempt(now=fake_now + 9999.0))

    def test_policy_rejection_does_not_increment_failure_count(self) -> None:
        """Policy/schema rejections (400) don't count toward circuit threshold."""
        breaker = self._make_breaker(threshold=3)
        fake_now = 1000.0
        for _ in range(5):
            breaker.record_failure(RetryClass.POLICY_REJECTION, now=fake_now)
        # Should NOT have opened — policy rejections don't count
        self.assertEqual(breaker.state, CircuitBreakerState.CLOSED)

    def test_to_dict_has_required_fields(self) -> None:
        """Circuit breaker serializes with all required fields."""
        breaker = self._make_breaker()
        d = breaker.to_dict()
        required = {"provider", "model_pattern", "state", "failure_count", "auth_failure"}
        for key in required:
            self.assertIn(key, d)


class BudgetManagerTests(unittest.TestCase):

    def test_unlimited_budget_always_allows(self) -> None:
        """Budget manager with no limits always allows requests."""
        manager = BudgetManager()
        allowed, reason = manager.can_run("current_brief")
        self.assertTrue(allowed)

    def test_daily_request_limit_enforced(self) -> None:
        """Budget manager blocks requests when daily limit is exceeded."""
        manager = BudgetManager(daily_request_limit=3)
        now = 1000.0
        for _ in range(3):
            allowed, _ = manager.can_run("current_analysis", now=now)
            self.assertTrue(allowed)
            manager.consume()

        # 4th request should be blocked
        allowed, reason = manager.can_run("current_analysis", now=now)
        self.assertFalse(allowed)
        self.assertIn("request_limit_exceeded", reason)

    def test_daily_cost_limit_enforced(self) -> None:
        """Budget blocks when cost ceiling is reached."""
        manager = BudgetManager(daily_cost_limit_usd=0.10)
        now = 1000.0
        manager.consume(tokens=1000, cost_usd=0.09)
        # Trying to spend 0.02 when 0.01 left
        allowed, reason = manager.can_run("digest", cost_usd=0.02, now=now)
        self.assertFalse(allowed)
        self.assertIn("cost_limit_exceeded", reason)

    def test_window_resets_after_expiry(self) -> None:
        """Budget window resets after 24 hours."""
        window = BudgetWindow(
            window_name="day",
            window_seconds=86400,
            request_limit=2,
            token_limit=None,
            cost_limit_usd=None,
            window_start=0.0,  # 1970 — guaranteed expired
        )
        window.request_count = 2
        # Force reset by querying after window expiry
        allowed, _ = window.can_reserve(requests=1, now=time.time())
        self.assertTrue(allowed)  # Reset should have occurred
        self.assertEqual(window.request_count, 0)

    def test_status_returns_dict(self) -> None:
        """BudgetManager.status() returns a serializable dict."""
        manager = BudgetManager(daily_request_limit=100)
        status = manager.status()
        self.assertIsInstance(status, dict)
        self.assertIn("daily", status)


class RetryTaxonomyTests(unittest.TestCase):

    def test_rate_limited_is_retryable(self) -> None:
        self.assertEqual(classify_retry("rate_limited"), RetryClass.RETRYABLE)

    def test_http_429_is_retryable(self) -> None:
        self.assertEqual(classify_retry("quota_exceeded", 429), RetryClass.RETRYABLE)

    def test_401_is_auth_failure(self) -> None:
        self.assertEqual(classify_retry("unauthorized", 401), RetryClass.AUTH_FAILURE)

    def test_403_is_auth_failure(self) -> None:
        self.assertEqual(classify_retry("forbidden", 403), RetryClass.AUTH_FAILURE)

    def test_400_is_policy_rejection(self) -> None:
        self.assertEqual(classify_retry("bad_request", 400), RetryClass.POLICY_REJECTION)

    def test_503_is_retryable(self) -> None:
        self.assertEqual(classify_retry("service_unavailable", 503), RetryClass.RETRYABLE)

    def test_quality_fail_is_quality_failure(self) -> None:
        self.assertEqual(classify_retry("quality_fail"), RetryClass.QUALITY_FAILURE)

    def test_unknown_error_is_terminal(self) -> None:
        self.assertEqual(classify_retry("unexpected_error", 500), RetryClass.TERMINAL)


class LLMJobCreationTests(unittest.TestCase):

    def test_job_creation_has_idempotency_key(self) -> None:
        """LLMJobV1.create() produces deterministic idempotency key."""
        job1 = LLMJobV1.create(
            workload=WorkloadClass.CURRENT_BRIEF,
            subject_type="post",
            subject_id="abc123",
            input_ref="silver://post/abc123",
            input_sha256="deadbeef" * 8,
            prompt_id="brief-v1",
            prompt_sha256="cafebabe" * 8,
            model_policy_id="gemini-standard",
        )
        job2 = LLMJobV1.create(
            workload=WorkloadClass.CURRENT_BRIEF,
            subject_type="post",
            subject_id="abc123",
            input_ref="silver://post/abc123",
            input_sha256="deadbeef" * 8,
            prompt_id="brief-v1",
            prompt_sha256="cafebabe" * 8,
            model_policy_id="gemini-standard",
        )
        # Same inputs → same idempotency key
        self.assertEqual(job1.idempotency_key, job2.idempotency_key)

    def test_different_inputs_different_idempotency_key(self) -> None:
        """Different input hashes produce different idempotency keys."""
        job1 = LLMJobV1.create(
            workload=WorkloadClass.CURRENT_BRIEF,
            subject_type="post", subject_id="abc123",
            input_ref="ref1", input_sha256="aaa",
            prompt_id="p1", prompt_sha256="ppp",
            model_policy_id="m1",
        )
        job2 = LLMJobV1.create(
            workload=WorkloadClass.CURRENT_BRIEF,
            subject_type="post", subject_id="abc123",
            input_ref="ref1", input_sha256="bbb",  # Different input hash
            prompt_id="p1", prompt_sha256="ppp",
            model_policy_id="m1",
        )
        self.assertNotEqual(job1.idempotency_key, job2.idempotency_key)

    def test_priority_matches_workload_class(self) -> None:
        """Job priority is set from workload class priority table."""
        job = LLMJobV1.create(
            workload=WorkloadClass.HISTORICAL_BACKLOG,
            subject_type="post", subject_id="old1",
            input_ref="ref", input_sha256="aaa",
            prompt_id="p1", prompt_sha256="ppp",
            model_policy_id="m1",
        )
        self.assertEqual(job.priority, WORKLOAD_PRIORITIES[WorkloadClass.HISTORICAL_BACKLOG])

    def test_job_starts_in_pending_state(self) -> None:
        """Newly created job starts in PENDING state."""
        job = LLMJobV1.create(
            workload=WorkloadClass.DIGEST,
            subject_type="digest", subject_id="2026-07-31",
            input_ref="ref", input_sha256="aaa",
            prompt_id="p1", prompt_sha256="ppp",
            model_policy_id="m1",
        )
        from reddit_crawler.llm_control import JobState
        self.assertEqual(job.state, JobState.PENDING)


if __name__ == "__main__":
    unittest.main()
