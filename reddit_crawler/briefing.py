"""reddit_crawler/briefing.py — Grounded Vietnamese social brief builder.

Part of Wave 3 (Grounded Social Briefs).
Features:
- Structured Vietnamese social brief layout
- Explicit claim → evidence quote mapping
- Citation validation to prevent hallucinated URLs/claims
- Fallback to "insufficient_evidence" when claims lack citations
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from .evidence import EvidenceChunk, GroundedClaim, QualityValidator


@dataclass
class GroundedSocialBrief:
    brief_id: str
    headline: str
    summary_vi: str
    claims: list[GroundedClaim]
    evidence_chunks: list[EvidenceChunk]
    source_urls: list[str]
    status: str   # "ready" | "draft" | "insufficient_evidence"
    quality_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "brief_id": self.brief_id,
            "headline": self.headline,
            "summary_vi": self.summary_vi,
            "claims": [c.to_dict() for c in self.claims],
            "evidence_chunks": [e.to_dict() for e in self.evidence_chunks],
            "source_urls": self.source_urls,
            "status": self.status,
            "quality_reasons": self.quality_reasons,
        }


def build_grounded_brief(
    brief_id: str,
    headline: str,
    summary_vi: str,
    claims: list[GroundedClaim],
    evidence_chunks: list[EvidenceChunk],
    source_urls: list[str],
) -> GroundedSocialBrief:
    """Build a GroundedSocialBrief and run runtime quality validation.

    If any claims lack evidence or contain unverified URLs, status is set to
    "draft" or "insufficient_evidence" automatically.
    """
    validator = QualityValidator(allowed_urls=set(source_urls))

    # Check for hallucinated URLs in text
    fabricated_urls = validator.validate_urls(summary_vi)
    if fabricated_urls:
        return GroundedSocialBrief(
            brief_id=brief_id,
            headline=headline,
            summary_vi=summary_vi,
            claims=claims,
            evidence_chunks=evidence_chunks,
            source_urls=source_urls,
            status="draft",
            quality_reasons=[f"fabricated_urls_detected:{fabricated_urls}"],
        )

    # Check grounding coverage
    avail_ev_ids = {e.evidence_id for e in evidence_chunks}
    g_res = validator.validate_grounding(claims, avail_ev_ids)

    return GroundedSocialBrief(
        brief_id=brief_id,
        headline=headline,
        summary_vi=summary_vi,
        claims=claims,
        evidence_chunks=evidence_chunks,
        source_urls=source_urls,
        status=g_res["decision"] if g_res["pass"] else g_res["decision"],
        quality_reasons=g_res["reasons"],
    )
