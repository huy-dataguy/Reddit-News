import json
import logging
import sqlite3
from pathlib import Path
from typing import Union, List, Dict
import time
from datetime import datetime

from reddit_crawler.contracts import BronzeEnvelopeV1, RunResult
from reddit_crawler.bronze import BronzeWriter
from reddit_crawler.storage import Storage

logger = logging.getLogger(__name__)

def transform_pending(db_path: Union[str, Path], bronze_root: Path, *, limit: int = 0) -> RunResult:
    """Transform pending Bronze objects to Silver (idempotent)."""
    started_at = time.time()
    import uuid
    run_id = f"tx-{int(started_at)}-{uuid.uuid4().hex[:8]}"
    
    input_count = 0
    output_count = 0
    errors: List[str] = []
    
    storage = Storage(db_path=str(db_path), raw_dir=str(bronze_root))
    conn = storage.conn
    
    # Init run
    with conn:
        conn.execute(
            "INSERT INTO pipeline_run (run_id, pipeline, status, started_at) VALUES (?, ?, ?, ?)",
            (run_id, "transform", "running", started_at)
        )
        
    try:
        # Find pending objects
        cur = conn.cursor()
        cur.execute(
            "SELECT object_id, relative_path, entity_type FROM bronze_object WHERE transform_status = 'pending'"
            + (f" LIMIT {limit}" if limit else "")
        )
        pending = cur.fetchall()
        
        for obj_id, rel_path, entity_type in pending:
            part_path = bronze_root / rel_path
            
            # Claim object
            with conn:
                conn.execute(
                    "UPDATE bronze_object SET transform_status = 'running' WHERE object_id = ?",
                    (obj_id,)
                )
            
            # Transform
            try:
                envelopes = BronzeWriter.read_part(part_path)
                input_count += len(envelopes)
                
                # Silver upsert
                # Need to use storage methods for upserting depending on entity_type
                # E.g. save_posts, save_comments
                for env in envelopes:
                    if env.entity_type == "post":
                        storage.upsert_post(env.payload)
                    elif env.entity_type == "comment":
                        link_id = env.payload.get("link_id")
                        from reddit_crawler.storage import _short_id
                        post_id = _short_id(link_id) if link_id else None
                        if post_id:
                            storage.upsert_comment(env.payload, post_id)
                    elif env.entity_type == "subreddit":
                        storage.upsert_subreddit(env.payload)
                    elif env.entity_type == "user":
                        storage.upsert_author(env.payload)
                        
                output_count += len(envelopes)
                    
                with conn:
                    conn.execute(
                        "UPDATE bronze_object SET transform_status = 'success', transformed_at = ? WHERE object_id = ?",
                        (time.time(), obj_id)
                    )
            except Exception as e:
                errors.append(f"Error processing {obj_id}: {str(e)}")
                with conn:
                    conn.execute(
                        "UPDATE bronze_object SET transform_status = 'failed', transformed_at = ? WHERE object_id = ?",
                        (time.time(), obj_id)
                    )
                    
        status = "success" if not errors else "failed"
        
    except Exception as e:
        errors.append(str(e))
        status = "failed"
        
    finished_at = time.time()
    
    with conn:
        conn.execute(
            """UPDATE pipeline_run 
               SET status = ?, finished_at = ?, input_count = ?, output_count = ?, error_summary = ? 
               WHERE run_id = ?""",
            (status, finished_at, input_count, output_count, json.dumps(errors), run_id)
        )
        
    return RunResult(
        run_id=run_id,
        pipeline="transform",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        input_count=input_count,
        output_count=output_count,
        errors=errors
    )
