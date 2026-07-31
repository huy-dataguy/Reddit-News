"""tests/test_release_manifest.py — Tests for ReleaseManifestV1 and ops release module.

Verifies:
- Manifest creation records correct revision/lock digests/unit digests
- Secret pattern scanner rejects credentials
- Manifest save/load roundtrip works
- Required fields are present
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reddit_crawler.ops.release import (
    ReleaseManifest,
    _assert_no_secret_patterns,
    create_release_manifest,
    get_manifest_path,
)


class ReleaseManifestTests(unittest.TestCase):

    def _repo_root(self) -> Path:
        return Path(__file__).parent.parent

    def test_manifest_creation(self) -> None:
        """create_release_manifest produces a valid manifest with required fields."""
        manifest = create_release_manifest(repo_root=self._repo_root())
        self.assertIsInstance(manifest.release_id, str)
        self.assertTrue(len(manifest.release_id) >= 8)
        self.assertIsInstance(manifest.revision, str)
        self.assertIsInstance(manifest.lock_digests, dict)
        self.assertIsInstance(manifest.unit_digests, dict)
        self.assertIsInstance(manifest.created_at, float)
        self.assertTrue(manifest.created_at > 0)
        self.assertIsNone(manifest.go_live_approved_by)
        self.assertIsNone(manifest.go_live_approved_at)

    def test_manifest_to_dict_has_required_fields(self) -> None:
        """Manifest dict contains all ReleaseManifestV1 required keys."""
        manifest = create_release_manifest(repo_root=self._repo_root())
        d = manifest.to_dict()
        required_keys = {
            "release_id", "revision", "created_at", "created_at_iso",
            "lock_digests", "schema_version", "unit_digests",
            "checks", "backup", "restore_drill", "observation_window",
            "deviations", "go_live_approved_by", "go_live_approved_at",
        }
        for key in required_keys:
            self.assertIn(key, d, f"Missing required key: {key}")

    def test_lock_digests_present(self) -> None:
        """Lock digests include requirements.lock entry."""
        manifest = create_release_manifest(repo_root=self._repo_root())
        self.assertIn("requirements.lock", manifest.lock_digests)
        # Digest should be a hex string or NOT_FOUND if file missing
        digest = manifest.lock_digests["requirements.lock"]
        self.assertIsInstance(digest, str)
        self.assertTrue(len(digest) > 0)

    def test_manifest_save_and_load_roundtrip(self) -> None:
        """Manifest survives save/load roundtrip with identical content."""
        manifest = create_release_manifest(
            repo_root=self._repo_root(),
            checks={"backend": "pass", "frontend": "pass"},
            deviations=["test deviation"],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            manifest.save(path)
            self.assertTrue(path.exists())
            loaded = ReleaseManifest.from_file(path)
            self.assertEqual(manifest.release_id, loaded.release_id)
            self.assertEqual(manifest.revision, loaded.revision)
            self.assertEqual(manifest.checks, loaded.checks)
            self.assertEqual(manifest.deviations, loaded.deviations)

    def test_manifest_json_valid(self) -> None:
        """Saved manifest is valid JSON parseable with standard library."""
        manifest = create_release_manifest(repo_root=self._repo_root())
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            manifest.save(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)
            self.assertEqual(data["release_id"], manifest.release_id)

    def test_secret_scanner_rejects_api_key(self) -> None:
        """Secret pattern scanner raises on OPENAI_API_KEY assignment."""
        bad_text = '{"OPENAI_API_KEY": "sk-abc123"}'
        with self.assertRaises(ValueError):
            _assert_no_secret_patterns(bad_text)

    def test_secret_scanner_accepts_clean_manifest(self) -> None:
        """Secret pattern scanner accepts manifest text without credentials."""
        clean_text = json.dumps({
            "release_id": "abc123",
            "revision": "deadbeef",
            "checks": {"backend": "pass"},
        })
        # Should not raise
        _assert_no_secret_patterns(clean_text)

    def test_get_manifest_path_structure(self) -> None:
        """get_manifest_path returns path under reports/operations/release/<id>/."""
        path = get_manifest_path("/some/repo", "test-release-id")
        self.assertIn("operations", str(path))
        self.assertIn("release", str(path))
        self.assertIn("test-release-id", str(path))
        self.assertTrue(str(path).endswith("manifest.json"))

    def test_manifest_with_deviations(self) -> None:
        """Manifest records deviation strings correctly."""
        manifest = create_release_manifest(
            repo_root=self._repo_root(),
            deviations=["Known: LLM circuit breaker open during test", "Known: no golden stories yet"],
        )
        self.assertEqual(len(manifest.deviations), 2)
        self.assertIn("LLM circuit breaker", manifest.deviations[0])


if __name__ == "__main__":
    unittest.main()
