import unittest
import tempfile
import sqlite3
from pathlib import Path
from reddit_crawler.publish import publish_gold
from reddit_crawler.marts import materialize_gold

class TestPublish(unittest.TestCase):
    def test_publish_gold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(db)
            with open("reddit_crawler/schema.sql") as f:
                conn.executescript(f.read()); conn.execute("INSERT INTO fact_post (post_id, score) VALUES (\"p1\", 10)"); conn.commit()
            conn.close()
            
            pub_id = materialize_gold(db, "run1", ["day"])
            res = publish_gold(db, pub_id)
            self.assertEqual(res["current_publish_id"], pub_id)