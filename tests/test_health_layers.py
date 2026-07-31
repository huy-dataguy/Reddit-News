import unittest
import tempfile
import sqlite3
from pathlib import Path
from reddit_crawler.serving import ServingRepository

class TestHealthLayers(unittest.TestCase):
    def test_health(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(db)
            with open("reddit_crawler/schema.sql") as f:
                conn.executescript(f.read())
            conn.close()
            
            repo = ServingRepository(db)
            res = repo.health()
            self.assertIn("silver_posts", res["counts"])