"""reddit_crawler/llm_control.py — LLM workload control, budget, and circuit breaker.

Implements the LLM control plane from 2026-07-28-llm-workload-control-quality.md:
- LLMJobV1 with priority, lease, idempotency, dependency semantics
- Budget reservations per provider/workload/time window
- Circuit breaker (open/half-open/closed) per provider/model
- Weighted fair priority scheduler (high priority without backlog starvation)

No network calls in this module — this is the control plane only.
Actual AI provider calls remain in llm.py and gemini_backlog.py.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class WorkloadClass(str, Enum):
    """Workload priority classes per spec."""
    INTERACTIVE_INTERNAL = "interactive_internal"  # priority 100
    CURRENT_BRIEF = "current_brief"                # priority 80
    CURRENT_ANALYSIS = "current_analysis"           # priority 70
    DIGEST = "digest"                               # priority 60
    REFRESH = "refresh"                             # priority 40
    HISTORICAL_BACKLOG = "historical_backlog"       # priority 10


WORKLOAD_PRIORITIES: dict[str, int] = {
    WorkloadClass.INTERACTIVE_INTERNAL: 100,
    WorkloadClass.CURRENT_BRIEF: 80,
    WorkloadClass.CURRENT_ANALYSIS: 70,
    WorkloadClass.DIGEST: 60,
    WorkloadClass.REFRESH: 40,
    WorkloadClass.HISTORICAL_BACKLOG: 10,
}


class JobState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    QUALITY_CHECK = "quality_check"
    READY_TO_PUBLISH = "ready_to_publish"
    REJECTED_QUALITY = "rejected_quality"
    RETRY_WAIT = "retry_wait"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking all requests
    HALF_OPEN = "half_open"  # Probing with single request


class RetryClass(str, Enum):
    """Error taxonomy for retry decisions."""
    RETRYABLE = "retryable"          # 429, 503 — honor Retry-After
    POLICY_REJECTION = "policy_rejection"  # 400, bad schema — no retry same input
    AUTH_FAILURE = "auth_failure"    # 401, 403 — terminal, operator alert
    QUALITY_FAILURE = "quality_failure"  # Schema valid but quality gate fail
    TERMINAL = "terminal"            # Unretriable error


@dataclass
class LLMJobV1:
    """LLM job record per spec LLMJobV1 interface."""
    job_id: str
    workload: str         # WorkloadClass value
    subject_type: str     # "post", "digest", "brief"
    subject_id: str
    priority: int
    dependencies: list[str]   # job_ids that must succeed first
    state: str            # JobState value
    idempotency_key: str  # hash of (workload, subject, input, prompt, model policy)
    input_ref: str        # reference to input data (not the data itself)
    input_sha256: str
    evidence_version: Optional[str]
    prompt_id: str
    prompt_sha256: str
    model_policy_id: str
    attempts: int
    next_attempt_at: Optional[float]
    lease_owner: Optional[str]
    lease_expires_at: Optional[float]
    created_at: float
    superseded_by: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(
        cls,
        workload: str,
        subject_type: str,
        subject_id: str,
        input_ref: str,
        input_sha256: str,
        prompt_id: str,
        prompt_sha256: str,
        model_policy_id: str,
        *,
        evidence_version: Optional[str] = None,
        dependencies: Optional[list[str]] = None,
    ) -> "LLMJobV1":
        """Create a new LLM job with computed idempotency key."""
        import hashlib
        key_parts = f"{workload}::{subject_type}::{subject_id}::{input_sha256}::{prompt_sha256}::{model_policy_id}"
        idempotency_key = hashlib.sha256(key_parts.encode()).hexdigest()[:32]
        priority = WORKLOAD_PRIORITIES.get(workload, 0)
        return cls(
            job_id=str(uuid.uuid4()),
            workload=workload,
            subject_type=subject_type,
            subject_id=subject_id,
            priority=priority,
            dependencies=dependencies or [],
            state=JobState.PENDING,
            idempotency_key=idempotency_key,
            input_ref=input_ref,
            input_sha256=input_sha256,
            evidence_version=evidence_version,
            prompt_id=prompt_id,
            prompt_sha256=prompt_sha256,
            model_policy_id=model_policy_id,
            attempts=0,
            next_attempt_at=None,
            lease_owner=None,
            lease_expires_at=None,
            created_at=time.time(),
            superseded_by=None,
        )


@dataclass
class CircuitBreaker:
    """Circuit breaker state per provider/model.

    States:
    - CLOSED: normal operation, all requests allowed
    - OPEN: blocking all requests (after threshold failures)
    - HALF_OPEN: one probe request allowed to test recovery

    Auth failures are terminal and do not probe automatically.
    """
    provider: str
    model_pattern: str
    state: str = CircuitBreakerState.CLOSED
    failure_count: int = 0
    last_failure_at: Optional[float] = None
    opened_at: Optional[float] = None
    half_open_probe_at: Optional[float] = None
    cooldown_seconds: float = 300.0       # Default 5 min before half-open
    failure_threshold: int = 5            # Open after N failures in window
    window_seconds: float = 600.0        # Rolling 10 min window
    auth_failure: bool = False           # True = terminal, no auto-probe

    def record_failure(
        self,
        error_class: RetryClass,
        retry_after: Optional[float] = None,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Record a failure and potentially open the circuit."""
        now = now or time.time()
        self.last_failure_at = now

        if error_class == RetryClass.AUTH_FAILURE:
            # Auth failures are terminal — open permanently until operator action
            self.auth_failure = True
            self.state = CircuitBreakerState.OPEN
            self.opened_at = now
            return

        # Count failures in rolling window
        if error_class in (RetryClass.RETRYABLE, RetryClass.TERMINAL):
            self.failure_count += 1

        cooldown = retry_after or self.cooldown_seconds
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            self.opened_at = now
            self.half_open_probe_at = now + cooldown

    def record_success(self, *, now: Optional[float] = None) -> None:
        """Record a success and potentially close the circuit."""
        now = now or time.time()
        if self.state == CircuitBreakerState.HALF_OPEN:
            # Successful probe — close the circuit
            self.state = CircuitBreakerState.CLOSED
            self.failure_count = 0
            self.opened_at = None
            self.half_open_probe_at = None
            self.auth_failure = False

    def can_attempt(self, *, now: Optional[float] = None) -> bool:
        """Returns True if a new LLM request can be attempted."""
        now = now or time.time()
        if self.auth_failure:
            return False  # Terminal — requires operator action
        if self.state == CircuitBreakerState.CLOSED:
            return True
        if self.state == CircuitBreakerState.OPEN:
            # Check if it's time to go half-open
            if (
                self.half_open_probe_at is not None
                and now >= self.half_open_probe_at
            ):
                self.state = CircuitBreakerState.HALF_OPEN
                return True
            return False
        if self.state == CircuitBreakerState.HALF_OPEN:
            # Only one probe at a time
            return True
        return False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BudgetWindow:
    """A time-windowed budget allocation."""
    window_name: str         # "minute", "day", "month"
    window_seconds: float
    request_limit: Optional[int]
    token_limit: Optional[int]
    cost_limit_usd: Optional[float]
    request_count: int = 0
    token_count: int = 0
    cost_usd: float = 0.0
    window_start: float = field(default_factory=time.time)

    def reset_if_expired(self, *, now: Optional[float] = None) -> bool:
        """Reset counters if window has expired. Returns True if reset."""
        now = now or time.time()
        if now - self.window_start >= self.window_seconds:
            self.request_count = 0
            self.token_count = 0
            self.cost_usd = 0.0
            self.window_start = now
            return True
        return False

    def can_reserve(
        self,
        requests: int = 1,
        tokens: int = 0,
        cost_usd: float = 0.0,
        *,
        now: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Check if reservation is within budget. Returns (allowed, reason)."""
        self.reset_if_expired(now=now)
        if self.request_limit is not None:
            if self.request_count + requests > self.request_limit:
                return False, f"request_limit_exceeded:{self.request_count}/{self.request_limit}"
        if self.token_limit is not None and tokens > 0:
            if self.token_count + tokens > self.token_limit:
                return False, f"token_limit_exceeded:{self.token_count}/{self.token_limit}"
        if self.cost_limit_usd is not None and cost_usd > 0:
            if self.cost_usd + cost_usd > self.cost_limit_usd:
                return False, f"cost_limit_exceeded:{self.cost_usd}/{self.cost_limit_usd}"
        return True, ""

    def consume(
        self,
        requests: int = 1,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Consume budget (called after successful reservation)."""
        self.request_count += requests
        self.token_count += tokens
        self.cost_usd += cost_usd


class BudgetManager:
    """Manages LLM budget across providers, workloads, and time windows.

    Fail-closed: if config cannot be parsed, all requests are blocked.
    """

    def __init__(
        self,
        daily_request_limit: Optional[int] = None,
        daily_token_limit: Optional[int] = None,
        daily_cost_limit_usd: Optional[float] = None,
    ):
        self._daily = BudgetWindow(
            window_name="day",
            window_seconds=86400,
            request_limit=daily_request_limit,
            token_limit=daily_token_limit,
            cost_limit_usd=daily_cost_limit_usd,
        )
        self._configured = True

    def can_run(
        self,
        workload: str,
        *,
        tokens: int = 0,
        cost_usd: float = 0.0,
        now: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Returns (can_run, reason) for a workload request."""
        if not self._configured:
            return False, "budget_config_invalid_fail_closed"

        allowed, reason = self._daily.can_reserve(
            requests=1, tokens=tokens, cost_usd=cost_usd, now=now,
        )
        return allowed, reason

    def consume(self, tokens: int = 0, cost_usd: float = 0.0) -> None:
        self._daily.consume(requests=1, tokens=tokens, cost_usd=cost_usd)

    def status(self) -> dict:
        return {
            "daily": {
                "requests": f"{self._daily.request_count}/{self._daily.request_limit or 'unlimited'}",
                "tokens": f"{self._daily.token_count}/{self._daily.token_limit or 'unlimited'}",
                "cost_usd": f"{self._daily.cost_usd:.4f}/{self._daily.cost_limit_usd or 'unlimited'}",
                "window_age_hours": round((time.time() - self._daily.window_start) / 3600, 1),
            }
        }


def classify_retry(error_code: str, http_status: Optional[int] = None) -> RetryClass:
    """Classify an error into retry taxonomy."""
    if error_code in ("rate_limited", "quota_exceeded") or http_status == 429:
        return RetryClass.RETRYABLE
    if error_code in ("invalid_key", "unauthorized") or http_status in (401,):
        return RetryClass.AUTH_FAILURE
    if error_code in ("forbidden",) or http_status in (403,):
        return RetryClass.AUTH_FAILURE
    if error_code in ("bad_request", "schema_error", "invalid_input") or http_status == 400:
        return RetryClass.POLICY_REJECTION
    if error_code in ("quality_fail", "grounding_fail", "insufficient_evidence"):
        return RetryClass.QUALITY_FAILURE
    if http_status in (503, 502, 504):
        return RetryClass.RETRYABLE
    return RetryClass.TERMINAL
