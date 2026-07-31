"""reddit_crawler/evidence.py — Evidence chunking, citation grounding, and claim verification.

Part of Wave 3 (Grounded Social Briefs).
Per LLM Workload & Quality spec:
- Claims must map to explicit evidence IDs with bounded quotes
- Unsupported claims or fabricated URLs transition output to rejected_quality / insufficient_evidence
- Exact cache hit requires matching evidence hash and prompt policy
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class EvidenceChunk:
    evidence_id: str         # SHA-256 snippet hash
    document_id: str         # post_id or source document ID
    source_url: str
    quote: str               # Bounded quote (max 500 chars)
    context: str             # Section/paragraph context
    authority: str = "medium"  # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GroundedClaim:
    claim_id: str
    statement: str
    evidence_ids: list[str]
    confidence: float
    decision: str  # "ready" | "draft" | "insufficient_evidence"

    def to_dict(self) -> dict:
        return asdict(self)


def create_evidence_chunk(
    document_id: str,
    source_url: str,
    quote: str,
    context: str = "",
    authority: str = "medium",
) -> EvidenceChunk:
    """Create a bounded evidence chunk with deterministic SHA-256 evidence_id."""
    bounded_quote = quote.strip()[:500]
    payload = f"{document_id}::{source_url}::{bounded_quote}"
    evidence_id = "ev_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return EvidenceChunk(
        evidence_id=evidence_id,
        document_id=document_id,
        source_url=source_url,
        quote=bounded_quote,
        context=context[:200],
        authority=authority,
    )


class QualityValidator:
    """Runtime hard quality gate for LLM outputs before publish."""

    def __init__(self, allowed_urls: Optional[set[str]] = None):
        self.allowed_urls = allowed_urls or set()

    def validate_urls(self, text: str) -> list[str]:
        """Find any URLs in text that are not in the allowed source URLs set."""
        if not self.allowed_urls:
            return []
        url_pattern = re.compile(r"https?://[^\s<>()\]\[\"']+", re.IGNORECASE)
        found_urls = set(url_pattern.findall(text))
        fabricated = []
        for url in found_urls:
            clean_url = url.rstrip(".,);]>\"'")
            if clean_url not in self.allowed_urls:
                fabricated.append(clean_url)
        return fabricated

    def validate_grounding(
        self,
        claims: list[GroundedClaim],
        available_evidence_ids: set[str],
    ) -> dict:
        """Verify claim-evidence coverage and decision invariants.

        Returns dict:
        {
            "pass": bool,
            "decision": "ready" | "draft" | "insufficient_evidence",
            "reasons": list[str]
        }
        """
        reasons = []
        if not claims:
            return {
                "pass": False,
                "decision": "insufficient_evidence",
                "reasons": ["no_claims_provided"],
            }

        unsupported_count = 0
        for claim in claims:
            missing_ev = [ev for ev in claim.evidence_ids if ev not in available_evidence_ids]
            if missing_ev or not claim.evidence_ids:
                unsupported_count += 1
                reasons.append(f"claim_{claim.claim_id}_lacks_evidence:{missing_ev}")

        coverage = (len(claims) - unsupported_count) / len(claims) if claims else 0.0

        if coverage < 0.95:
            decision = "insufficient_evidence" if coverage == 0 else "draft"
            return {
                "pass": False,
                "decision": decision,
                "reasons": [f"coverage_below_threshold:{coverage:.2f}"] + reasons,
            }

        return {
            "pass": True,
            "decision": "ready",
            "reasons": [],
        }
