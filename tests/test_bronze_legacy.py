import unittest
import tempfile
import json
from pathlib import Path
from reddit_crawler.bronze import BronzeLegacyImporter

class TestBronzeLegacy(unittest.TestCase):
    def test_import_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = BronzeLegacyImporter(tmpdir, tmpdir)
            jsonl = Path(tmpdir) / "data.jsonl"
            with open(jsonl, "w") as f:
                f.write(json.dumps({"id": "p1"}) + "\n")
                f.write(json.dumps({"id": "p1"}) + "\n") # duplicate
                f.write(json.dumps({"id": "p2"}) + "\n")
                
            res = importer.import_file(jsonl)
            self.assertEqual(res["processed"], 2)
            self.assertEqual(res["skipped_duplicate"], 1)
            self.assertEqual(res["errors"], 0)