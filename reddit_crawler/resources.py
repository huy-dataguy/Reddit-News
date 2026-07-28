"""Bộ trích xuất và phân loại tài nguyên giá trị cao (Resource & Link Extraction)

Tự động quét văn bản bài viết và cây bình luận (comments) để phát hiện:
- GitHub Repositories (github.com/user/repo)
- Research Papers (arxiv.org, huggingface.co/papers, openreview.net...)
- Technical Blogs & Substacks (substack, medium, dev.to, personal blogs)
- Tools & Model Hubs (huggingface models, spaces, Kaggle)
- Dynamic context snippets xung quanh link trong bình luận.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

URL_REGEX = re.compile(
    r'https?://[^\s<>"]+|www\.[^\s<>"]+',
    re.IGNORECASE,
)

# Các domain thông dụng hoặc cần lọc bỏ
IGNORE_DOMAINS = {
    "reddit.com", "www.reddit.com", "old.reddit.com", "i.redd.it", "v.redd.it",
    "preview.redd.it", "imgur.com", "i.imgur.com", "gfycat.com", "giphy.com",
    "twitter.com", "x.com", "t.co", "facebook.com", "instagram.com",
}


def clean_url(raw_url: str) -> tuple[str, str]:
    """Làm sạch URL, loại bỏ tracking query params và trả về (cleaned_url, domain)."""
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url
    
    # Loại bỏ ký tự thừa ở cuối link nếu bị dính dấu câu markdown
    raw_url = raw_url.rstrip(").,;:!]")
    
    try:
        parsed = urlparse(raw_url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        
        # Strip tracking parameters
        query_params = parse_qs(parsed.query)
        filtered_params = {
            k: v for k, v in query_params.items()
            if not k.lower().startswith(("utm_", "ref", "source", "fbclid", "gclid"))
        }
        new_query = urlencode(filtered_params, doseq=True)
        
        cleaned = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            "",  # strip fragment
        ))
        return cleaned, domain
    except Exception:
        return raw_url, ""


def classify_resource(url: str, domain: str) -> tuple[str, str, str]:
    """Phân loại loại tài nguyên: (resource_type, title_suggestion, description)."""
    path = urlparse(url).path.strip("/")
    
    if "github.com" in domain:
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1].replace(".git", "")
            return "github_repo", f"{owner}/{repo}", f"GitHub Repository bởi {owner}"
        return "github_repo", "GitHub", "GitHub resource"
        
    if any(paper_domain in domain for paper_domain in ("arxiv.org", "biorxiv.org", "medrxiv.org", "openreview.net")):
        return "arxiv_paper", f"Paper: {path}", "Research Paper / Academic Preprint"
        
    if "huggingface.co" in domain:
        if "papers" in path:
            return "arxiv_paper", f"HuggingFace Paper: {path}", "Research Paper trên HuggingFace"
        return "tool", f"HuggingFace: {path}", "Machine Learning Model / Space"
        
    if any(blog_domain in domain for blog_domain in ("substack.com", "medium.com", "dev.to", "hashnode.dev")):
        return "tech_blog", domain, "Technical Blog Article"
        
    if any(video_domain in domain for video_domain in ("youtube.com", "youtu.be")):
        return "video", "YouTube Video", "Technical Video / Talk"
        
    if any(doc_domain in domain for doc_domain in ("docs.", "documentation", "readthe-docs", "gitbook.io")):
        return "documentation", domain, "Developer Documentation"
        
    return "tech_blog", domain, "External Technical Link"


def extract_snippet(text: str, match_start: int, match_end: int, window: int = 120) -> str:
    """Rút trích câu/đoạn văn xung quanh đường link để lấy ngữ cảnh."""
    start = max(0, match_start - window)
    end = min(len(text), match_end + window)
    snippet = text[start:end].strip()
    # Replace linebreaks with spaces
    snippet = re.sub(r'\s+', ' ', snippet)
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def verify_url_confidence(url: str, cache: dict[str, str] | None = None) -> str:
    """Kiểm tra URL còn tồn tại bằng HEAD request. Trả verified/unverified/suspicious.
    
    Args:
        url: URL cần kiểm tra
        cache: dict_cache để tránh request trùng domain
    
    Returns:
        'verified' nếu URL trả 2xx, 'unverified' nếu không thể kiểm tra, 
        'suspicious' nếu redirect tới domain khác hoặc timeout.
    """
    if not url or not httpx:
        return "unverified"
    
    cache = cache or {}
    domain = urlparse(url).netloc.lower()
    cache_key = domain
    
    if cache_key in cache:
        return cache[cache_key]
    
    try:
        with httpx.Client(
            timeout=5.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RedditRadar/1.0)"},
        ) as client:
            response = client.head(url)
            if response.status_code < 400:
                final_domain = urlparse(str(response.url)).netloc.lower()
                if final_domain != domain and not final_domain.endswith("." + domain.lstrip("www.")):
                    result = "suspicious"
                else:
                    result = "verified"
            else:
                result = "unverified"
    except Exception:
        result = "unverified"
    
    cache[cache_key] = result
    return result


def extract_resources_from_text(
    text: str,
    *,
    post_id: str | None = None,
    comment_id: str | None = None,
    author_name: str | None = None,
    score: int = 0,
    verify: bool = False,
    url_cache: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Tìm tất cả tài nguyên/link giá trị cao trong văn bản."""
    if not text or not isinstance(text, str):
        return []
        
    resources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    
    for match in URL_REGEX.finditer(text):
        raw_url = match.group(0)
        url, domain = clean_url(raw_url)
        
        if not domain or domain in IGNORE_DOMAINS or url in seen_urls:
            continue
            
        seen_urls.add(url)
        resource_type, title, description = classify_resource(url, domain)
        context_snippet = extract_snippet(text, match.start(), match.end())
        
        # Generates deterministic short ID
        resource_id = hashlib.sha256(f"{post_id}:{comment_id}:{url}".encode()).hexdigest()[:16]
        
        # Determine confidence
        if verify and url_cache is not None:
            confidence = verify_url_confidence(url, url_cache)
        else:
            # Heuristic confidence based on domain
            if "github.com" in domain or "arxiv.org" in domain:
                confidence = "unverified"
            else:
                confidence = "unverified"
        
        resources.append({
            "resource_id": resource_id,
            "post_id": post_id,
            "comment_id": comment_id,
            "platform_id": "reddit",
            "url": url,
            "domain": domain,
            "resource_type": resource_type,
            "title": title,
            "description": description,
            "context_snippet": context_snippet,
            "author_name": author_name,
            "score": score or 0,
            "confidence": confidence,
            "extracted_at": time.time(),
        })
        
    return resources


def scan_and_store_resources(db_path: str | Path = "reddit.db") -> dict[str, int]:
    """Quét toàn bộ comment và post trong SQLite database để trích xuất tài nguyên."""
    from reddit_crawler.storage import Storage
    
    store = Storage(str(db_path))
    store.conn.row_factory = sqlite3.Row
    extracted_count = 0
    post_count = 0
    comment_count = 0
    
    try:
        # 1. Quét từ fact_comment
        comments = store.conn.execute(
            """
            SELECT comment_id, post_id, author_name, body, score
            FROM fact_comment
            WHERE body LIKE '%http%' AND body IS NOT NULL
            """
        ).fetchall()
        
        for c in comments:
            comment_count += 1
            res_list = extract_resources_from_text(
                c["body"],
                post_id=c["post_id"],
                comment_id=c["comment_id"],
                author_name=c["author_name"],
                score=c["score"] or 0,
            )
            for res in res_list:
                store.upsert_extracted_resource(res)
                extracted_count += 1
                
        # 2. Quét từ fact_post
        posts = store.conn.execute(
            """
            SELECT post_id, author_name, selftext, score
            FROM fact_post
            WHERE selftext LIKE '%http%' AND selftext IS NOT NULL
            """
        ).fetchall()
        
        for p in posts:
            post_count += 1
            res_list = extract_resources_from_text(
                p["selftext"],
                post_id=p["post_id"],
                comment_id=None,
                author_name=p["author_name"],
                score=p["score"] or 0,
            )
            for res in res_list:
                store.upsert_extracted_resource(res)
                extracted_count += 1
                
        store.commit()
    finally:
        store.close()
        
    return {
        "scanned_comments": comment_count,
        "scanned_posts": post_count,
        "total_extracted_resources": extracted_count,
    }


def get_top_extracted_resources(
    db_path: str | Path = "reddit.db",
    resource_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Truy vấn các tài nguyên giá trị cao được cộng đồng chia sẻ nhiều nhất."""
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    
    try:
        query = """
            SELECT r.*, p.title AS post_title, s.display_name AS subreddit
            FROM fact_extracted_resource r
            LEFT JOIN fact_post p ON p.post_id = r.post_id
            LEFT JOIN dim_subreddit s ON s.subreddit_id = p.subreddit_id
        """
        params: list[Any] = []
        if resource_type and resource_type != "all":
            query += " WHERE r.resource_type = ?"
            params.append(resource_type)
            
        query += " ORDER BY r.score DESC, r.extracted_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
