"""reddit_crawler/ops/release.py — Release manifest creation and validation.

ReleaseManifestV1 spec (from internal-production-program):
  {release_id, revision, lock_digests, schema_version, bronze_contract_version,
   gold_snapshot_id, unit_digests, checks, backup, restore_drill,
   observation_window, deviations, go_live_approved_by, go_live_approved_at}

Release manifests are stored at:
  reports/operations/release/<release_id>/manifest.json

They are immutable after sign-off and must not contain secrets, raw payloads,
or credentials of any kind.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return "NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_revision() -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _get_lock_digests(repo_root: Path) -> dict[str, str]:
    """Compute SHA-256 of lock files — never reads .env or secrets."""
    lock_files = {
        "requirements.lock": repo_root / "requirements.lock",
        "package-lock.json": repo_root / "web" / "frontend" / "package-lock.json",
    }
    return {name: _sha256_file(path) for name, path in lock_files.items()}


def _get_unit_digests(systemd_dir: Path) -> dict[str, str]:
    """Compute SHA-256 of all systemd unit files."""
    digests = {}
    if not systemd_dir.exists():
        return digests
    for f in sorted(systemd_dir.glob("*.service")) + sorted(systemd_dir.glob("*.timer")):
        digests[f.name] = _sha256_file(f)
    return digests


@dataclass
class ReleaseManifest:
    """ReleaseManifestV1 — immutable release evidence record."""
    release_id: str
    revision: str
    created_at: float
    created_at_iso: str
    lock_digests: dict[str, str]
    schema_version: Optional[int]
    bronze_contract_version: Optional[str]
    gold_snapshot_id: Optional[str]
    unit_digests: dict[str, str]
    checks: dict[str, Any]
    backup: Optional[dict]
    restore_drill: Optional[dict]
    observation_window: Optional[dict]
    deviations: list[str]
    go_live_approved_by: Optional[str]
    go_live_approved_at: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        """Save manifest to path. Validates no secret patterns first."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        text = json.dumps(data, indent=2)
        _assert_no_secret_patterns(text)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def from_file(cls, path: Path) -> "ReleaseManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


SECRET_PATTERNS = [
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "sk-",
    "AIza",
    "reddit_secret",
    "TELEGRAM_BOT_TOKEN",
    "REPORT_WEBHOOK_URL",
    "password",
    "bearer",
    "Authorization:",
]


def _assert_no_secret_patterns(text: str) -> None:
    """Raise ValueError if text contains known secret patterns."""
    text_lower = text.lower()
    for pattern in SECRET_PATTERNS:
        if pattern.lower() in text_lower:
            # Check for actual values (not just key names in allowed contexts)
            # This is a heuristic — full secrets scanner in test_secrets.py
            if "=" in text or '"' in text:
                # Only flag if it looks like an assignment or JSON value
                import re
                if re.search(rf'["\s]{re.escape(pattern)}["\s]*[:=]', text, re.IGNORECASE):
                    raise ValueError(
                        f"Potential secret pattern '{pattern}' found in manifest text. "
                        "Do not store credentials in release manifests."
                    )


def create_release_manifest(
    repo_root: str | Path = ".",
    *,
    schema_version: Optional[int] = None,
    checks: Optional[dict] = None,
    backup: Optional[dict] = None,
    restore_drill: Optional[dict] = None,
    deviations: Optional[list[str]] = None,
    go_live_approved_by: Optional[str] = None,
    go_live_approved_at: Optional[str] = None,
) -> ReleaseManifest:
    """Create a new release manifest for the current repository state.

    The manifest records revision, lock digests, unit digests and any
    evidence provided. It does NOT contain secrets, credentials, or raw payloads.
    """
    import datetime
    repo_root = Path(repo_root).resolve()
    release_id = str(uuid.uuid4())[:12]
    now = time.time()
    now_iso = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    revision = _get_revision()
    lock_digests = _get_lock_digests(repo_root)
    unit_digests = _get_unit_digests(repo_root / "deploy" / "systemd")

    return ReleaseManifest(
        release_id=release_id,
        revision=revision,
        created_at=now,
        created_at_iso=now_iso,
        lock_digests=lock_digests,
        schema_version=schema_version,
        bronze_contract_version=None,
        gold_snapshot_id=None,
        unit_digests=unit_digests,
        checks=checks or {},
        backup=backup,
        restore_drill=restore_drill,
        observation_window=None,
        deviations=deviations or [],
        go_live_approved_by=go_live_approved_by,
        go_live_approved_at=go_live_approved_at,
    )


def get_manifest_path(
    repo_root: str | Path,
    release_id: str,
) -> Path:
    return Path(repo_root) / "reports" / "operations" / "release" / release_id / "manifest.json"
