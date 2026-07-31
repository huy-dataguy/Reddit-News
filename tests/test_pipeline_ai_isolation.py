"""tests/test_pipeline_ai_isolation.py — Tests ensuring crawl pipeline is independent of LLM/AI.

Per spec: "Crawl/checkpoint/Silver transform không phụ thuộc provider AI."
These tests verify that:
1. The crawl path does NOT import from llm.py, gemini_backlog.py, or ai_analysis modules
2. The web/API path does NOT call source_fetcher (no live fetch in web request)
3. The backup/restore path does NOT require AI providers
4. Circuit breaker open does NOT block incremental crawl
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class CrawlAIIsolationTests(unittest.TestCase):
    """Verify crawl module does not directly depend on AI modules."""

    def _get_imports(self, module_path: Path) -> set[str]:
        """Parse a Python file and extract top-level import names."""
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
        except SyntaxError:
            return set()

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return imports

    def test_crawl_module_no_llm_import(self) -> None:
        """reddit_crawler/crawl.py does not import llm or gemini modules at top level."""
        crawl_path = Path(__file__).parent.parent / "reddit_crawler" / "crawl.py"
        if not crawl_path.exists():
            self.skipTest("crawl.py not found")
        imports = self._get_imports(crawl_path)
        ai_modules = {"llm", "gemini_backlog", "openai", "google"}
        intersection = imports & ai_modules
        self.assertFalse(
            intersection,
            f"crawl.py should not import AI modules at top level: {intersection}"
        )

    def test_storage_module_no_llm_import(self) -> None:
        """reddit_crawler/storage.py does not import llm modules."""
        storage_path = Path(__file__).parent.parent / "reddit_crawler" / "storage.py"
        if not storage_path.exists():
            self.skipTest("storage.py not found")
        imports = self._get_imports(storage_path)
        ai_modules = {"llm", "gemini_backlog"}
        intersection = imports & ai_modules
        self.assertFalse(
            intersection,
            f"storage.py imports AI modules: {intersection}"
        )

    def test_source_queue_no_ai_import(self) -> None:
        """source_queue.py does not import AI/LLM modules."""
        queue_path = Path(__file__).parent.parent / "reddit_crawler" / "source_queue.py"
        if not queue_path.exists():
            self.skipTest("source_queue.py not found")
        imports = self._get_imports(queue_path)
        ai_modules = {"llm", "llm_control", "gemini_backlog", "openai"}
        intersection = imports & ai_modules
        self.assertFalse(
            intersection,
            f"source_queue.py imports AI modules: {intersection}"
        )


class CircuitBreakerIsolationTests(unittest.TestCase):
    """Verify circuit breaker open doesn't block crawl operations."""

    def test_circuit_breaker_open_does_not_block_budget_reservation(self) -> None:
        """Open circuit breaker for AI doesn't affect budget manager for crawl."""
        from reddit_crawler.llm_control import CircuitBreaker, BudgetManager, RetryClass

        # Open the circuit breaker
        breaker = CircuitBreaker("gemini", "gemini-*", failure_threshold=1, cooldown_seconds=9999.0)
        breaker.record_failure(RetryClass.RETRYABLE, now=1000.0)
        self.assertFalse(breaker.can_attempt(now=1000.0))

        # Budget manager for crawl should be completely independent
        budget = BudgetManager(daily_request_limit=1000)
        allowed, reason = budget.can_run("crawl_incremental")
        self.assertTrue(allowed, "Budget should allow crawl even when AI circuit is open")

    def test_backup_ops_no_llm_dependency(self) -> None:
        """Backup/restore operations don't require AI providers."""
        from reddit_crawler.ops.backup import backup_db, restore_drill
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            backup_dir = os.path.join(tmpdir, "backups")

            # Create minimal DB
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE test_table (id TEXT PRIMARY KEY)")
            conn.commit()
            conn.close()

            # Backup should work without AI
            manifest = backup_db(db_path, backup_dir)
            self.assertEqual(manifest.quick_check, "ok")

            # Restore drill should work without AI
            result = restore_drill(backup_dir)
            self.assertEqual(result["drill_result"], "pass")


class WebAIIsolationTests(unittest.TestCase):
    """Verify web API path does not trigger AI or source fetch operations."""

    def test_web_app_no_source_fetch_import(self) -> None:
        """web/app.py does not import source_fetcher at module level."""
        web_app_path = Path(__file__).parent.parent / "web" / "app.py"
        if not web_app_path.exists():
            self.skipTest("web/app.py not found")

        source = web_app_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        top_level_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    top_level_imports.add(node.module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level_imports.add(alias.name)

        unsafe_modules = {"source_fetcher", "source_queue"}
        intersection = {m for m in top_level_imports if any(u in m for u in unsafe_modules)}
        self.assertFalse(
            intersection,
            f"web/app.py imports source fetch modules: {intersection}"
        )

    def test_ops_module_no_network_calls_in_capacity(self) -> None:
        """capacity_check does not make network calls."""
        from reddit_crawler.ops.capacity import capacity_check

        # Should complete without network calls or errors
        result = capacity_check(".")
        self.assertIsNotNone(result)
        self.assertGreater(result.total_bytes, 0)

    def test_permissions_module_never_reads_env_values(self) -> None:
        """check_permissions returns mode bits only — never env values."""
        from reddit_crawler.ops.permissions import check_permissions
        import json

        result = check_permissions(".")
        result_text = json.dumps(result)

        # Should not contain common secret patterns
        for pattern in ["sk-", "AIza", "bearer", "password"]:
            self.assertNotIn(pattern.lower(), result_text.lower(),
                f"Permission check output contains potential secret pattern: {pattern}")

        # Must confirm redacted flag
        for finding in result["findings"]:
            self.assertTrue(finding["redacted"], "Permission findings must always be redacted=True")


if __name__ == "__main__":
    unittest.main()
