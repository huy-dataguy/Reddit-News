import gzip
import json
import hashlib
import os
from pathlib import Path
from typing import List, Dict, Any, Tuple
from dataclasses import asdict
from datetime import datetime, timezone

from reddit_crawler.contracts import BronzeEnvelopeV1, BronzeObject, validate_bronze_envelope

class BronzeWriter:
    def __init__(self, bronze_root: Path, run_id: str, source: str, entity_type: str):
        self.bronze_root = Path(bronze_root)
        self.run_id = run_id
        self.source = source
        self.entity_type = entity_type

    def write_records(self, records: List[Dict[str, Any]]) -> BronzeObject:
        if not records:
            raise ValueError("No records to write")
            
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        rel_dir = Path(f"source={self.source}/entity={self.entity_type}/date={date_str}/run={self.run_id}")
        out_dir = self.bronze_root / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Simple part naming
        part_name = "part-00001.jsonl.gz"
        final_path = out_dir / part_name
        tmp_path = out_dir / f"{part_name}.tmp"
        
        min_fetched_at = float("inf")
        max_fetched_at = float("-inf")
        row_count = 0
        
        hasher = hashlib.sha256()
        
        with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
            for record in records:
                # payload hash
                payload_bytes = json.dumps(record, sort_keys=True).encode('utf-8')
                payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
                
                # Try to extract source_id and fetched_at
                source_id = str(record.get("id", record.get("name", "unknown")))
                fetched_at = float(record.get("fetched_at", datetime.now(timezone.utc).timestamp()))
                
                min_fetched_at = min(min_fetched_at, fetched_at)
                max_fetched_at = max(max_fetched_at, fetched_at)
                
                env = BronzeEnvelopeV1(
                    schema_version=1,
                    run_id=self.run_id,
                    source=self.source,
                    entity_type=self.entity_type,
                    source_id=source_id,
                    fetched_at=fetched_at,
                    payload_sha256=payload_sha256,
                    payload=record
                )
                
                line = json.dumps(asdict(env)) + "\n"
                f.write(line)
                hasher.update(line.encode('utf-8'))
                row_count += 1
                
            f.flush()
            os.fsync(f.fileno())
            
        os.rename(tmp_path, final_path)
        
        file_sha256 = hasher.hexdigest()
        
        # deterministic: SHA-256 of (source/entity/run/path/hash)
        obj_id_str = f"{self.source}/{self.entity_type}/{self.run_id}/{str(rel_dir / part_name)}/{file_sha256}"
        object_id = hashlib.sha256(obj_id_str.encode('utf-8')).hexdigest()
        
        return BronzeObject(
            object_id=object_id,
            run_id=self.run_id,
            entity_type=self.entity_type,
            relative_path=str(rel_dir / part_name),
            sha256=file_sha256,
            row_count=row_count,
            min_fetched_at=min_fetched_at,
            max_fetched_at=max_fetched_at,
            transform_status="pending",
            transformed_at=None
        )
        
    @staticmethod
    def read_part(path: Path) -> List[BronzeEnvelopeV1]:
        results = []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                env = validate_bronze_envelope(data)
                results.append(env)
        return results


class BronzeLegacyImporter:
    def __init__(self, raw_dir: Path, bronze_root: Path):
        self.raw_dir = Path(raw_dir)
        self.bronze_root = Path(bronze_root)
        
    def import_file(self, jsonl_path: Path, *, dry_run: bool = True, limit: int = 0) -> Dict[str, Any]:
        processed = 0
        skipped = 0
        errors = 0
        seen_hashes = set()
        
        with open(jsonl_path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break
                
                if not line.strip():
                    continue
                
                line_bytes = line.encode('utf-8')
                line_hash = hashlib.sha256(line_bytes).hexdigest()
                if line_hash in seen_hashes:
                    skipped += 1
                    continue
                seen_hashes.add(line_hash)
                
                try:
                    record = json.loads(line)
                    # We might write these via BronzeWriter if not dry_run, but the spec says:
                    # Returns summary
                    processed += 1
                except json.JSONDecodeError:
                    errors += 1
                    
        return {
            "processed": processed,
            "skipped_duplicate": skipped,
            "errors": errors
        }
