import sqlite3
import time
from pathlib import Path
from typing import Union, List, Dict, Any

def run_data_quality_checks(db_path: Union[str, Path], publish_id: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(db_path), timeout=30)
    results = []
    
    try:
        cur = conn.cursor()
        
        # Simple check: do we have any signals for this publish_id?
        cur.execute("SELECT COUNT(*) FROM mart_post_signal WHERE publish_id = ?", (publish_id,))
        count = cur.fetchone()[0]
        
        status = "pass" if count > 0 else "fail"
        
        results.append({
            "check_name": "min_signals",
            "status": status,
            "observed_value": str(count),
            "threshold": "1",
            "layer": "gold"
        })
        
    finally:
        conn.close()
        
    return results

def is_publish_safe(results: List[Dict[str, Any]]) -> bool:
    for r in results:
        if r["status"] == "fail":
            return False
    return True
