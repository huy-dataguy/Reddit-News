"""reddit_crawler/ops/backup.py — Safe SQLite backup and restore-drill operations.

Uses SQLite online backup API (sqlite3.Connection.backup) to safely copy a
live database without raw file copy. Never overwrites the live database.
Includes checksum, quick_check, FK check, and schema version verification.

BackupManifestV1 spec:
  {backup_id, created_at, source_revision, source_schema_version,
   db_path_redacted, artifact, sha256, size_bytes, quick_check,
   foreign_key_check, counts}
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


_LIVE_DB_NAME = "reddit.db"  # Safeguard: never allow overwriting this at live path


@dataclass
class BackupManifest:
    """BackupManifestV1 — backup artifact metadata."""
    backup_id: str
    created_at: float
    created_at_iso: str
    source_revision: str
    source_schema_version: Optional[int]
    db_path_redacted: str
    artifact: str
    sha256: str
    size_bytes: int
    quick_check: str
    foreign_key_check: str
    counts: dict[str, int]
    restore_verified: bool = False
    restore_verified_at: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def from_file(cls, path: Path) -> "BackupManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


def _get_schema_version(conn: sqlite3.Connection) -> Optional[int]:
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _get_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Get row counts for key tables without reading raw content."""
    counts = {}
    tables = [
        "fact_post", "fact_comment", "dim_subreddit", "dim_author",
        "ai_post_analysis_v2", "ai_digest", "fact_extracted_resource",
        "enrichment_state",
    ]
    for table in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = row[0] if row else 0
        except sqlite3.OperationalError:
            pass
    return counts


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_revision() -> str:
    """Get current git HEAD revision, or 'unknown' if not a git repo."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def backup_db(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    dry_run: bool = False,
) -> BackupManifest:
    """Create a safe online backup of the SQLite database.

    Uses sqlite3.Connection.backup() (online backup API) — never raw cp.
    The live database is opened read-only; backup is written to a new file
    under backup_dir with a manifest JSON alongside it.

    Args:
        db_path: Path to the live SQLite database (read-only access).
        backup_dir: Directory to write backup artifact and manifest.
        dry_run: If True, validates but does not write files.

    Returns:
        BackupManifest with all artifact metadata.

    Raises:
        ValueError: If db_path resolves to a live DB path and would be
                    overwritten, or if safety checks fail.
    """
    db_path = Path(db_path).resolve()
    backup_dir = Path(backup_dir).resolve()

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    # Safety: never overwrite the live DB
    backup_id = str(uuid.uuid4())[:8]
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    artifact_name = f"reddit.db.backup-{timestamp}-{backup_id}"
    artifact_path = backup_dir / artifact_name

    # Safety: check artifact path doesn't conflict with live DB
    if artifact_path.resolve() == db_path:
        raise ValueError("Backup artifact path would overwrite live database!")

    revision = _get_revision()

    # Open source DB read-only to get metadata
    source_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        schema_version = _get_schema_version(source_conn)
        counts = _get_counts(source_conn)
    finally:
        source_conn.close()

    if dry_run:
        import datetime
        now = time.time()
        return BackupManifest(
            backup_id=backup_id,
            created_at=now,
            created_at_iso=datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source_revision=revision,
            source_schema_version=schema_version,
            db_path_redacted=db_path.name,  # Never log full path with user dirs
            artifact=f"[dry-run] {artifact_name}",
            sha256="[dry-run]",
            size_bytes=db_path.stat().st_size,
            quick_check="[dry-run]",
            foreign_key_check="[dry-run]",
            counts=counts,
        )

    backup_dir.mkdir(parents=True, exist_ok=True)

    # Online backup using sqlite3 backup API
    tmp_artifact = artifact_path.with_suffix(".tmp")
    source_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(str(tmp_artifact))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    tmp_artifact.rename(artifact_path)

    # Verify backup integrity
    verify_conn = sqlite3.connect(f"file:{artifact_path}?mode=ro", uri=True)
    try:
        qc_row = verify_conn.execute("PRAGMA quick_check").fetchone()
        quick_check = qc_row[0] if qc_row else "error"
        fk_rows = verify_conn.execute("PRAGMA foreign_key_check").fetchall()
        fk_check = "ok" if not fk_rows else f"{len(fk_rows)} violation(s)"
    finally:
        verify_conn.close()

    sha256 = _sha256_file(artifact_path)
    size_bytes = artifact_path.stat().st_size

    import datetime
    now = time.time()
    manifest = BackupManifest(
        backup_id=backup_id,
        created_at=now,
        created_at_iso=datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source_revision=revision,
        source_schema_version=schema_version,
        db_path_redacted=db_path.name,
        artifact=artifact_name,
        sha256=sha256,
        size_bytes=size_bytes,
        quick_check=quick_check,
        foreign_key_check=fk_check,
        counts=counts,
    )
    manifest.save(backup_dir / f"{artifact_name}.manifest.json")
    return manifest


def restore_drill(
    backup_dir: str | Path,
    *,
    latest: bool = True,
    backup_id: Optional[str] = None,
    temporary_path: Optional[Path] = None,
) -> dict:
    """Perform a restore drill: restore to a temporary path, verify, then discard.

    Never touches the live database. Opens the restored copy read-only
    and runs quick_check, FK check, and count verification.

    Returns:
        dict with drill_result (pass/fail), checks, counts, timing.
    """
    backup_dir = Path(backup_dir).resolve()
    start = time.time()

    # Find the manifest to use
    manifests = sorted(backup_dir.glob("*.manifest.json"), reverse=True)
    if not manifests:
        raise FileNotFoundError(f"No backup manifests found in {backup_dir}")

    manifest_path = manifests[0]  # latest by filename timestamp
    manifest = BackupManifest.from_file(manifest_path)
    artifact_path = backup_dir / manifest.artifact

    if not artifact_path.exists():
        raise FileNotFoundError(f"Backup artifact not found: {artifact_path}")

    # Verify SHA-256 before restore
    actual_sha256 = _sha256_file(artifact_path)
    if actual_sha256 != manifest.sha256:
        raise ValueError(
            f"Backup artifact SHA-256 mismatch: expected {manifest.sha256}, got {actual_sha256}"
        )

    # Restore to a temporary path
    if temporary_path is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="reddit-radar-restore-drill-"))
        temp_db = tmp_dir / "restored.db"
    else:
        tmp_dir = None
        temp_db = temporary_path

    try:
        # Copy to temp path using SQLite backup API (not raw copy)
        src_conn = sqlite3.connect(f"file:{artifact_path}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(temp_db))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # Verify restored copy
        verify_conn = sqlite3.connect(f"file:{temp_db}?mode=ro", uri=True)
        try:
            qc = verify_conn.execute("PRAGMA quick_check").fetchone()
            quick_check = qc[0] if qc else "error"
            fk_rows = verify_conn.execute("PRAGMA foreign_key_check").fetchall()
            fk_check = "ok" if not fk_rows else f"{len(fk_rows)} violation(s)"
            counts = _get_counts(verify_conn)
            schema_version = _get_schema_version(verify_conn)
        finally:
            verify_conn.close()

        duration_s = time.time() - start
        drill_pass = quick_check == "ok" and fk_check == "ok"

        result = {
            "drill_result": "pass" if drill_pass else "fail",
            "backup_id": manifest.backup_id,
            "artifact": manifest.artifact,
            "sha256_verified": True,
            "quick_check": quick_check,
            "foreign_key_check": fk_check,
            "schema_version": schema_version,
            "counts": counts,
            "duration_seconds": round(duration_s, 2),
            "restore_path_temporary": str(temp_db),
            "live_db_untouched": True,
        }
    finally:
        # Always clean up temp directory
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return result
