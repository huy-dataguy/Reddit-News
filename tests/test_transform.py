import unittest
import tempfile
import sqlite3
import os
from pathlib import Path
from reddit_crawler.transform import transform_pending
from reddit_crawler.bronze import BronzeWriter

class TestTransform(unittest.TestCase):
    def test_transform_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(db)
            with open("reddit_crawler/schema.sql") as f:
                conn.executescript(f.read())
            
            writer = BronzeWriter(tmpdir, "run1", "reddit", "post")
            obj = writer.write_records([{"id": "p1", "name": "t3_p1"}])
            
            # insert pending object
            conn.execute("INSERT INTO bronze_object (object_id, run_id, entity_type, relative_path, sha256) VALUES (?, ?, ?, ?, ?)", 
                        (obj.object_id, obj.run_id, obj.entity_type, obj.relative_path, obj.sha256))
            conn.commit()
            conn.close()
            
            res = transform_pending(db, Path(tmpdir))
            self.assertEqual(res.status, "success")
            
            # verify idempotent
            res2 = transform_pending(db, Path(tmpdir))
            self.assertEqual(res2.input_count, 0)