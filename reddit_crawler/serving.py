import os
import sqlite3
from pathlib import Path
from typing import Union, List, Dict, Any, Optional

class ServingRepository:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self.mode = os.environ.get("REDDIT_SERVING_MODE", "legacy")
        
    def _get_conn(self):
        # read only conn? just normal conn
        return sqlite3.connect(self.db_path, timeout=30)
        
    def health(self) -> Dict[str, Any]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            
            # just some basic counts
            counts = {}
            try:
                cur.execute("SELECT COUNT(*) FROM bronze_object")
                counts["bronze_objects"] = cur.fetchone()[0]
            except sqlite3.OperationalError:
                pass
                
            try:
                cur.execute("SELECT COUNT(*) FROM fact_post")
                counts["silver_posts"] = cur.fetchone()[0]
            except sqlite3.OperationalError:
                pass
                
            return {
                "status": "ok",
                "mode": self.mode,
                "counts": counts
            }
        finally:
            conn.close()
            
    def current_publish_id(self) -> Optional[str]:
        if self.mode == "legacy":
            return None
            
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT current_publish_id FROM serving_state WHERE singleton_id = 'current'")
            row = cur.fetchone()
            return row[0] if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
            
    def today_signal(self, period: str = "day", limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            if self.mode == "mart":
                pub_id = self.current_publish_id()
                if not pub_id:
                    return []
                cur.execute("""
                    SELECT s.post_id, s.trend_score, p.title, p.url 
                    FROM mart_post_signal s
                    JOIN fact_post p ON s.post_id = p.post_id
                    WHERE s.publish_id = ? AND s.period = ?
                    ORDER BY s.trend_score DESC LIMIT ?
                """, (pub_id, period, limit))
                return [dict(row) for row in cur.fetchall()]
            else:
                # legacy
                cur.execute("""
                    SELECT post_id, score as trend_score, title, url
                    FROM fact_post
                    ORDER BY score DESC LIMIT ?
                """, (limit,))
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
            
    def knowledge_feed(self, period: str = "day", limit: int = 20) -> List[Dict[str, Any]]:
        return []
        
    def post_detail(self, post_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM fact_post WHERE post_id = ?", (post_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
            
    def story_candidate(self, period: str = "week") -> Optional[Dict[str, Any]]:
        return None
