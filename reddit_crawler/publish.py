import sqlite3
import time
from pathlib import Path
from typing import Union, Dict, Any

from reddit_crawler.quality import run_data_quality_checks, is_publish_safe
from reddit_crawler.contracts import ServingState

def publish_gold(db_path: Union[str, Path], publish_id: str) -> Dict[str, Any]:
    # Atomically flip serving_state
    conn = sqlite3.connect(str(db_path), timeout=30)
    
    try:
        # verify publish_id exists in marts
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM mart_post_signal WHERE publish_id = ?", (publish_id,))
        if cur.fetchone()[0] == 0:
            raise ValueError(f"No gold data found for publish_id {publish_id}")
            
        dq_results = run_data_quality_checks(db_path, publish_id)
        if not is_publish_safe(dq_results):
            raise ValueError(f"DQ checks failed for publish_id {publish_id}")
            
        with conn:
            # find source run_id
            cur.execute("SELECT source_run_id FROM mart_post_signal WHERE publish_id = ? LIMIT 1", (publish_id,))
            row = cur.fetchone()
            source_run_id = row[0] if row else None
            
            published_at = time.time()
            
            conn.execute("""
                INSERT INTO serving_state (singleton_id, current_publish_id, published_at, source_run_id)
                VALUES ('current', ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    current_publish_id=excluded.current_publish_id,
                    published_at=excluded.published_at,
                    source_run_id=excluded.source_run_id
            """, (publish_id, published_at, source_run_id))
            
        return {
            "singleton_id": "current",
            "current_publish_id": publish_id,
            "published_at": published_at,
            "source_run_id": source_run_id
        }
            
    finally:
        conn.close()
