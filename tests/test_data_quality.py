import unittest
import tempfile
import sqlite3
from pathlib import Path
from reddit_crawler.quality import run_data_quality_checks, is_publish_safe
from reddit_crawler.marts import materialize_gold

class TestDataQuality(unittest.TestCase):
    def test_dq(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(db)
            with open("reddit_crawler/schema.sql") as f:
                conn.executescript(f.read())
            conn.execute("INSERT INTO fact_post (post_id, score) VALUES ('p1', 10)")
            conn.commit()
            conn.close()
            
            pub_id = materialize_gold(db, "run1", ["day"])
            res = run_data_quality_checks(db, pub_id)
            self.assertTrue(is_publish_safe(res))