import unittest
import tempfile
import sqlite3
from pathlib import Path
from reddit_crawler.transform import transform_pending

class TestPipelineRuns(unittest.TestCase):
    def test_pipeline_run_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(db)
            with open("reddit_crawler/schema.sql") as f:
                conn.executescript(f.read())
            conn.close()
            
            transform_pending(db, Path(tmpdir))
            
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            cur.execute("SELECT status, pipeline FROM pipeline_run")
            row = cur.fetchone()
            self.assertEqual(row[0], "success")
            self.assertEqual(row[1], "transform")