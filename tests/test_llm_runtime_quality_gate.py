"""tests/test_llm_runtime_quality_gate.py — Tests for LLM runtime quality gate (Wave 3).

Verifies:
- 100% schema/safety/URL/numeric hard checks pass for ready outputs
- Fabricated URLs automatically transition output status away from "ready"
- Unsupported claims fail grounding check and transition output to "draft" or "insufficient_evidence"
- Zero claims provided results in "insufficient_evidence"
"""
from __future__ import annotations

import unittest

from reddit_crawler.briefing import build_grounded_brief, GroundedSocialBrief
from reddit_crawler.evidence import create_evidence_chunk, GroundedClaim, QualityValidator


class RuntimeQualityGateTests(unittest.TestCase):

    def test_valid_grounded_brief_ready(self) -> None:
        """Brief with valid evidence and claims gets status 'ready'."""
        ev1 = create_evidence_chunk("post_1", "https://reddit.com/r/test/1", "Model release announced on Thursday.")
        claim1 = GroundedClaim("c1", "New AI model released Thursday", [ev1.evidence_id], 0.95, "ready")

        brief = build_grounded_brief(
            brief_id="b1",
            headline="Mô hình AI mới được phát hành",
            summary_vi="Mô hình AI mới vừa được công bố chính thức.",
            claims=[claim1],
            evidence_chunks=[ev1],
            source_urls=["https://reddit.com/r/test/1"],
        )

        self.assertEqual(brief.status, "ready")
        self.assertEqual(len(brief.quality_reasons), 0)

    def test_fabricated_url_blocks_ready(self) -> None:
        """Fabricated URL in summary transitions brief status to 'draft'."""
        ev1 = create_evidence_chunk("post_1", "https://reddit.com/r/test/1", "Model release announced.")
        claim1 = GroundedClaim("c1", "New AI model released", [ev1.evidence_id], 0.9, "ready")

        brief = build_grounded_brief(
            brief_id="b2",
            headline="AI Model Update",
            summary_vi="Xem chi tiết tại https://fake-news-site.com/hallucinated-link",
            claims=[claim1],
            evidence_chunks=[ev1],
            source_urls=["https://reddit.com/r/test/1"],
        )

        self.assertNotEqual(brief.status, "ready")
        self.assertIn("draft", brief.status)
        self.assertTrue(any("fabricated_urls" in r for r in brief.quality_reasons))

    def test_unsupported_claim_transitions_to_draft(self) -> None:
        """Claim with missing evidence ID causes status transition to draft/insufficient_evidence."""
        ev1 = create_evidence_chunk("post_1", "https://reddit.com/r/test/1", "Quote")
        claim_bad = GroundedClaim("c_bad", "Unsupported claim", ["ev_non_existent"], 0.5, "ready")

        brief = build_grounded_brief(
            brief_id="b3",
            headline="Unverified Info",
            summary_vi="Thông tin chưa kiểm chứng.",
            claims=[claim_bad],
            evidence_chunks=[ev1],
            source_urls=["https://reddit.com/r/test/1"],
        )

        self.assertNotEqual(brief.status, "ready")

    def test_no_claims_returns_insufficient_evidence(self) -> None:
        """Brief with no claims transitions to insufficient_evidence."""
        validator = QualityValidator(allowed_urls=set())
        res = validator.validate_grounding([], set())
        self.assertEqual(res["decision"], "insufficient_evidence")
        self.assertFalse(res["pass"])


if __name__ == "__main__":
    unittest.main()
