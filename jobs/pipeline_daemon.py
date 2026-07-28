"""Daemon chạy song song 2 pha:
Pha 1: Crawl dữ liệu mới từ Reddit liên tục (incremental)
Pha 2: Enrich bài báo, comments và chạy AI Analysis (Gemini) cho các bài viết hot
"""

import sys
import time
import subprocess
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pipeline_daemon")

PYTHON_BIN = sys.executable

def run_cmd(cmd_list):
    try:
        res = subprocess.run([PYTHON_BIN, "cli.py"] + cmd_list, capture_output=True, text=True)
        logger.info(f"Executed cli.py {' '.join(cmd_list)} -> exit {res.returncode}")
        if res.stdout:
            logger.info(f"STDOUT: {res.stdout.strip()[:300]}")
        if res.stderr:
            logger.warning(f"STDERR: {res.stderr.strip()[:300]}")
    except Exception as e:
        logger.error(f"Error running {' '.join(cmd_list)}: {e}")

def run_crawler_loop():
    logger.info("Bắt đầu Pha 1: Crawl loop...")
    while True:
        logger.info("--> Running Pha 1 (incremental crawl)...")
        run_cmd(["incremental", "--max-per-sub", "20"])
        time.sleep(300) # 5 min rest

def run_enrich_loop():
    logger.info("Bắt đầu Pha 2: Enrich & AI loop...")
    while True:
        logger.info("--> Running Pha 2 (enrich articles & comments)...")
        run_cmd(["enrich", "--kind", "both", "--limit", "30", "--depth", "2"])
        
        logger.info("--> Running Pha 2 (Gemini analyze top)...")
        run_cmd(["analyze-top", "--provider", "gemini", "--limit", "5"])
        
        logger.info("--> Running Pha 2 (Gemini AI digest)...")
        run_cmd(["ai-digest", "--provider", "gemini"])
        
        time.sleep(120) # 2 min rest

if __name__ == "__main__":
    import threading
    
    t1 = threading.Thread(target=run_crawler_loop, daemon=True)
    t2 = threading.Thread(target=run_enrich_loop, daemon=True)
    
    t1.start()
    t2.start()
    
    logger.info("Đã khởi chạy 2 pha (Crawl & Enrich/AI) song song!")
    t1.join()
    t2.join()
