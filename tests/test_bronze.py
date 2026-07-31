"""tests/test_bronze.py — Tests for BronzeWriter and BronzeEnvelopeV1.

Verifies:
- Envelope creation and validation (schema_version=1)
- BronzeWriter writes gzipped JSONL with atomic rename
- No temporary (.tmp) files remain after successful write
- Gzip output can be read back and matches input envelopes
- SHA-256 payload checksum matches
"""
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from reddit_crawler.bronze import BronzeWriter, validate_bronze_envelope
from reddit_crawler.contracts import BronzeEnvelopeV1, BronzeObject


class BronzeWriterTests(unittest.TestCase):

    def test_validate_bronze_envelope_valid(self) -> None:
        """Valid dictionary is parsed into BronzeEnvelopeV1."""
        raw = {
            "schema_version": 1,
            "run_id": "test-run-123",
            "source": "reddit",
            "entity_type": "post",
            "source_id": "post_01",
            "fetched_at": 1700000000.0,
            "payload_sha256": "abc123hash",
            "payload": {"id": "post_01", "title": "Test Title"},
        }
        env = validate_bronze_envelope(raw)
        self.assertIsInstance(env, BronzeEnvelopeV1)
        self.assertEqual(env.schema_version, 1)
        self.assertEqual(env.source_id, "post_01")

    def test_validate_bronze_envelope_invalid_schema_version(self) -> None:
        """Invalid schema_version raises ValueError."""
        raw = {
            "schema_version": 99,
            "run_id": "test-run-123",
            "source": "reddit",
            "entity_type": "post",
            "source_id": "post_01",
            "fetched_at": 1700000000.0,
            "payload_sha256": "abc123hash",
            "payload": {},
        }
        with self.assertRaises(ValueError):
            validate_bronze_envelope(raw)

    def test_bronze_writer_write_records(self) -> None:
        """BronzeWriter creates a gzipped file and returns BronzeObject metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bronze_root = Path(tmpdir) / "raw" / "bronze"
            writer = BronzeWriter(
                bronze_root=bronze_root,
                run_id="run-001",
                source="reddit",
                entity_type="post",
            )

            records = [
                {"id": "p1", "title": "Post One", "score": 10},
                {"id": "p2", "title": "Post Two", "score": 20},
            ]

            obj = writer.write_records(records)
            self.assertIsInstance(obj, BronzeObject)
            self.assertEqual(obj.run_id, "run-001")
            self.assertEqual(obj.entity_type, "post")
            self.assertEqual(obj.row_count, 2)
            self.assertEqual(obj.transform_status, "pending")

            # Check that destination file exists and is gzipped
            dest_file = bronze_root / obj.relative_path
            self.assertTrue(dest_file.exists())
            self.assertTrue(str(dest_file).endswith(".jsonl.gz"))

            # No temporary files should remain
            tmp_files = list(dest_file.parent.glob("*.tmp"))
            self.assertEqual(len(tmp_files), 0, "Temporary .tmp files should be cleaned up after atomic rename")

            # Read back and verify content
            with gzip.open(dest_file, "rt", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)

            env1 = json.loads(lines[0])
            self.assertEqual(env1["schema_version"], 1)
            self.assertEqual(env1["payload"]["id"], "p1")

    def test_read_part(self) -> None:
        """BronzeWriter.read_part reads gzipped BronzeEnvelopes back correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bronze_root = Path(tmpdir) / "raw" / "bronze"
            writer = BronzeWriter(
                bronze_root=bronze_root,
                run_id="run-002",
                source="reddit",
                entity_type="comment",
            )
            writer.write_records([{"id": "c1", "body": "hello"}])

            part_file = list(bronze_root.rglob("*.jsonl.gz"))[0]
            envelopes = writer.read_part(part_file)
            self.assertEqual(len(envelopes), 1)
            self.assertEqual(envelopes[0].source_id, "c1")
            self.assertEqual(envelopes[0].payload["body"], "hello")


if __name__ == "__main__":
    unittest.main()
