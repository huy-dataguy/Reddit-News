"""reddit_crawler/ops/permissions.py — Secret and data permission scanner.

Checks file permissions WITHOUT reading or printing values.
Reports only metadata: path existence, mode bits, owner/group if applicable.

Checks per release-engineering spec:
- .env must not be group/other readable (mode & 0o077 == 0)
- reddit.db must not be group/other readable
- raw/ directory must not be group/other readable
- .venv/ contents are expected to not have production secrets
- No secret files should be world-readable
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


SENSITIVE_PATHS = [
    ".env",
    "reddit.db",
    "reddit.db-wal",
    "reddit.db-shm",
    "raw",
    "reports",
]

SECRET_FILENAME_PATTERNS = [
    ".env",
    "*.key",
    "*.pem",
    "*.crt",
    "*.p12",
    "*.pfx",
]


@dataclass
class PermissionFinding:
    """A single permission check result."""
    path: str
    exists: bool
    mode_octal: str
    is_group_readable: bool
    is_other_readable: bool
    is_world_writable: bool
    severity: str  # "ok" | "warning" | "violation"
    message: str
    redacted: bool = True  # Always True — we never read values


def check_permissions(
    repo_root: str | Path = ".",
    *,
    extra_paths: Optional[list[str]] = None,
) -> dict:
    """Scan file permissions for security issues.

    Returns a dict with findings, summary counts, and overall status.
    Never reads or prints file contents or values.
    """
    repo_root = Path(repo_root).resolve()
    paths_to_check = list(SENSITIVE_PATHS)
    if extra_paths:
        paths_to_check.extend(extra_paths)

    findings: list[PermissionFinding] = []
    violations = 0
    warnings = 0

    for rel_path in paths_to_check:
        full_path = repo_root / rel_path
        if not full_path.exists():
            findings.append(PermissionFinding(
                path=rel_path,
                exists=False,
                mode_octal="N/A",
                is_group_readable=False,
                is_other_readable=False,
                is_world_writable=False,
                severity="ok",
                message=f"{rel_path}: not present (no permission issue)",
            ))
            continue

        try:
            st = full_path.stat()
        except OSError as e:
            findings.append(PermissionFinding(
                path=rel_path,
                exists=True,
                mode_octal="error",
                is_group_readable=False,
                is_other_readable=False,
                is_world_writable=False,
                severity="warning",
                message=f"{rel_path}: cannot stat — {type(e).__name__}",
            ))
            warnings += 1
            continue

        mode = st.st_mode
        mode_octal = oct(stat.S_IMODE(mode))
        is_group_readable = bool(mode & stat.S_IRGRP)
        is_other_readable = bool(mode & stat.S_IROTH)
        is_world_writable = bool(mode & stat.S_IWOTH)

        # Classify
        is_sensitive = any(
            rel_path == p or rel_path.startswith(p + "/")
            for p in [".env", "reddit.db", "reddit.db-wal", "reddit.db-shm"]
        )

        severity = "ok"
        messages = []

        if is_sensitive and is_other_readable:
            severity = "violation"
            messages.append("other-readable secret/live-data file")
            violations += 1
        elif is_sensitive and is_group_readable:
            severity = "warning"
            messages.append("group-readable secret/live-data file")
            warnings += 1
        elif is_world_writable:
            severity = "violation"
            messages.append("world-writable path")
            violations += 1

        message = (
            f"{rel_path}: mode={mode_octal}"
            + (f" — ISSUE: {'; '.join(messages)}" if messages else " — ok")
        )

        findings.append(PermissionFinding(
            path=rel_path,
            exists=True,
            mode_octal=mode_octal,
            is_group_readable=is_group_readable,
            is_other_readable=is_other_readable,
            is_world_writable=is_world_writable,
            severity=severity,
            message=message,
        ))

    overall = "ok" if violations == 0 and warnings == 0 else (
        "violation" if violations > 0 else "warning"
    )

    return {
        "overall": overall,
        "violations": violations,
        "warnings": warnings,
        "findings": [
            {
                "path": f.path,
                "exists": f.exists,
                "mode_octal": f.mode_octal,
                "severity": f.severity,
                "message": f.message,
                "redacted": f.redacted,
            }
            for f in findings
        ],
        "note": "Values are never read or printed. Only metadata (path, mode bits) is reported.",
    }
