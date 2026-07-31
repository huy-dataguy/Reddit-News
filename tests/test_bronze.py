import unittest
import tempfile
import gzip
import json
from pathlib import Path
from reddit_crawler.bronze import BronzeWriter

class TestBronzeWriter(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = BronzeWriter(tmpdir, "run1", "reddit", "post")
            obj = writer.write_records([{"id": "p1", "title": "hello"}])
            
            self.assertTrue(obj.object_id)
            self.assertEqual(obj.row_count, 1)
            
            part_path = Path(tmpdir) / obj.relative_path
            self.assertTrue(part_path.exists())
            
            # verify gzip
            with gzip.open(part_path, "rt") as f:
                data = [json.loads(line) for line in f]
                self.assertEqual(len(data), 1)
                
            envelopes = BronzeWriter.read_part(part_path)
            self.assertEqual(len(envelopes), 1)
            self.assertEqual(envelopes[0].payload["title"], "hello")