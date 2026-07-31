import sqlite3
import time
from pathlib import Path
from typing import Union, List
import uuid

def materialize_gold(db_path: Union[str, Path], source_run_id: str, periods: List[str]) -> str:
    publish_id = f"pub_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    
    conn = sqlite3.connect(str(db_path), timeout=30)
    
    # Just creating basic records in gold tables
    # For now, simplistic materialization just inserting from fact_post 
    # to mart_post_signal and mart_post_knowledge
    
    try:
        with conn:
            now_ts = time.time()
            for period in periods:
                conn.execute(
                    "INSERT INTO mart_post_signal (publish_id, period, post_id, as_of, trend_score, source_run_id) "
                    "SELECT ?, ?, post_id, ?, score, ? FROM fact_post LIMIT 100",
                    (publish_id, period, now_ts, source_run_id)
                )

            conn.execute(
                "INSERT INTO mart_post_knowledge (publish_id, post_id, analysis_json, generated_at, source_run_id) "
                "SELECT ?, post_id, '{}', ?, ? FROM fact_post LIMIT 100",
                (publish_id, now_ts, source_run_id)
            )
            
            for period in periods:
                conn.execute(f"""
                    INSERT INTO mart_digest (publish_id, period, digest_id, generated_at, source_run_id)
                    VALUES ('{publish_id}', '{period}', 'digest_{period}', {time.time()}, '{source_run_id}')
                """)
                
    finally:
        conn.close()
        
    return publish_id
