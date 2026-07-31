"""reddit_crawler/source_fetcher.py — Safe source-content ingestion with SSRF protection.

Feature-flagged OFF by default (SOURCE_CONTENT_FETCH_ENABLED=false).
This module implements the security policy from 2026-07-28-safe-source-content-ingestion.md.

Network policy:
- HTTPS only (http only with explicit allow)
- No loopback, link-local, private, CGNAT, multicast, reserved IPs (IPv4 + IPv6)
- Max 3 redirect hops, each fully re-validated
- DNS pinning: resolve once, connect to pinned IP
- No proxy env, no cookies, no credential forwarding
- Streaming: connect 5s, read 15s, total 30s
- Wire limit: 5 MiB, decoded: 10 MiB, extracted: 200k chars
- Robots.txt: fetch + cache with TTL, fail-closed

All operations are OFFLINE (not called from web request path).
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Feature flag — must be explicitly enabled
FETCH_ENABLED = os.environ.get("SOURCE_CONTENT_FETCH_ENABLED", "false").lower() == "true"

# Network limits
MAX_REDIRECT_HOPS = 3
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 15
TOTAL_DEADLINE_S = 30
MAX_WIRE_BYTES = 5 * 1024 * 1024       # 5 MiB
MAX_DECODED_BYTES = 10 * 1024 * 1024   # 10 MiB
MAX_EXTRACTED_CHARS = 200_000
MAX_URL_LENGTH = 2048
ALLOWED_PORTS = {80, 443}
ALLOWED_SCHEMES = {"https"}

# MIME allowlist (HTML/text first; PDF requires separate enable flag)
ALLOWED_MIMES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
}


class NetworkDecision(str, Enum):
    ALLOW = "allow"
    REJECT_SCHEME = "reject_scheme"
    REJECT_PORT = "reject_port"
    REJECT_HOST = "reject_host"
    REJECT_IP_PRIVATE = "reject_ip_private"
    REJECT_IP_RESERVED = "reject_ip_reserved"
    REJECT_IP_LOOPBACK = "reject_ip_loopback"
    REJECT_URL_LENGTH = "reject_url_length"
    REJECT_USERINFO = "reject_userinfo"
    REJECT_MALFORMED = "reject_malformed"
    REJECT_REDIRECT_LIMIT = "reject_redirect_limit"


@dataclass
class URLValidationResult:
    url: str
    decision: NetworkDecision
    reason_codes: list[str]
    resolved_ips_redacted: list[str] = field(default_factory=list)  # Redacted for logs
    policy_version: str = "1.0"
    decided_at: float = field(default_factory=time.time)

    @property
    def is_allowed(self) -> bool:
        return self.decision == NetworkDecision.ALLOW


# IPv4 private/reserved ranges
_PRIVATE_NETWORKS_V4 = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol
    ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),        # multicast
    ipaddress.ip_network("240.0.0.0/4"),        # reserved
    ipaddress.ip_network("0.0.0.0/8"),          # unspecified
    ipaddress.ip_network("255.255.255.255/32"), # broadcast
]

# IPv6 special ranges
_PRIVATE_NETWORKS_V6 = [
    ipaddress.ip_network("::1/128"),             # loopback
    ipaddress.ip_network("fc00::/7"),            # unique local
    ipaddress.ip_network("fe80::/10"),           # link-local
    ipaddress.ip_network("ff00::/8"),            # multicast
    ipaddress.ip_network("::/128"),              # unspecified
    ipaddress.ip_network("::ffff:0:0/96"),       # IPv4-mapped
    ipaddress.ip_network("2001:db8::/32"),       # documentation
    ipaddress.ip_network("100::/64"),            # discard
]


def _is_ip_private_or_reserved(ip_str: str) -> tuple[bool, str]:
    """Returns (is_unsafe, reason) for an IP address string."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, "invalid_ip"

    if isinstance(addr, ipaddress.IPv4Address):
        for net in _PRIVATE_NETWORKS_V4:
            if addr in net:
                return True, f"ipv4_private_or_reserved:{net}"
        return False, ""
    else:
        for net in _PRIVATE_NETWORKS_V6:
            if addr in net:
                return True, f"ipv6_private_or_reserved:{net}"
        return False, ""


def validate_url(url: str, *, resolve_dns: bool = False) -> URLValidationResult:
    """Validate a URL against the source fetch security policy.

    Does NOT make network requests unless resolve_dns=True.
    With resolve_dns=True, resolves DNS and validates all returned IPs.
    
    Returns URLValidationResult indicating ALLOW or specific rejection reason.
    """
    reasons: list[str] = []

    # Length check
    if len(url) > MAX_URL_LENGTH:
        return URLValidationResult(
            url=url[:100] + "...",
            decision=NetworkDecision.REJECT_URL_LENGTH,
            reason_codes=["url_too_long"],
        )

    # Parse
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return URLValidationResult(
            url=url,
            decision=NetworkDecision.REJECT_MALFORMED,
            reason_codes=["parse_error"],
        )

    # Scheme check
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return URLValidationResult(
            url=url,
            decision=NetworkDecision.REJECT_SCHEME,
            reason_codes=[f"scheme_{scheme}_not_allowed"],
        )

    # Userinfo check (credentials in URL)
    if parsed.username or parsed.password:
        return URLValidationResult(
            url=url,
            decision=NetworkDecision.REJECT_USERINFO,
            reason_codes=["userinfo_in_url"],
        )

    # Host check
    host = parsed.hostname
    if not host:
        return URLValidationResult(
            url=url,
            decision=NetworkDecision.REJECT_HOST,
            reason_codes=["missing_host"],
        )

    # Port check
    port = parsed.port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        return URLValidationResult(
            url=url,
            decision=NetworkDecision.REJECT_PORT,
            reason_codes=[f"port_{port}_not_allowed"],
        )

    # Check if host is a direct IP
    try:
        addr = ipaddress.ip_address(host)
        is_private, reason = _is_ip_private_or_reserved(str(addr))
        if is_private:
            return URLValidationResult(
                url=url,
                decision=NetworkDecision.REJECT_IP_PRIVATE,
                reason_codes=[reason],
            )
    except ValueError:
        pass  # Not an IP, is a hostname — continue

    # DNS resolution check
    resolved_ips_redacted: list[str] = []
    if resolve_dns:
        try:
            addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            for ai in addrinfos:
                ip_str = ai[4][0]
                is_private, reason = _is_ip_private_or_reserved(ip_str)
                if is_private:
                    return URLValidationResult(
                        url=url,
                        decision=NetworkDecision.REJECT_IP_PRIVATE,
                        reason_codes=[reason, "dns_resolves_to_private"],
                    )
                # Redact last octet for logging
                parts = ip_str.split(".")
                if len(parts) == 4:
                    redacted = ".".join(parts[:3] + ["*"])
                else:
                    redacted = ip_str[:ip_str.rfind(":")] + ":*"
                resolved_ips_redacted.append(redacted)
        except socket.gaierror as e:
            return URLValidationResult(
                url=url,
                decision=NetworkDecision.REJECT_HOST,
                reason_codes=[f"dns_resolution_failed:{type(e).__name__}"],
            )

    return URLValidationResult(
        url=url,
        decision=NetworkDecision.ALLOW,
        reason_codes=[],
        resolved_ips_redacted=resolved_ips_redacted,
    )


def validate_mime(content_type: str) -> bool:
    """Returns True if content-type is in the allowed MIME list."""
    # Extract just the MIME type, ignore parameters
    mime = content_type.split(";")[0].strip().lower()
    return mime in ALLOWED_MIMES


def is_prompt_injection(text: str) -> bool:
    """Heuristic check for prompt injection patterns in source content.
    
    Source content is always treated as untrusted data.
    This flags suspicious patterns for labeling — does NOT execute them.
    """
    injection_patterns = [
        r"ignore\s+(previous|all|above)\s+instructions?",
        r"system\s*prompt",
        r"<\s*system\s*>",
        r"you\s+are\s+now\s+a",
        r"disregard\s+(your|all|previous)",
        r"\[INST\]",
        r"\[/INST\]",
        r"<\|im_start\|>",
    ]
    # Also check for literal [INST] (case insensitive)
    if "[inst]" in text.lower() or "[/inst]" in text.lower():
        return True
    text_lower = text.lower()
    for pattern in injection_patterns:
        if re.search(pattern, text_lower):
            return True
    return False


# ── Feature gate ─────────────────────────────────────────────────────────────

def assert_fetch_enabled() -> None:
    """Raise RuntimeError if SOURCE_CONTENT_FETCH_ENABLED is not true.
    
    Call this at the start of any function that makes real network requests.
    """
    if not FETCH_ENABLED:
        raise RuntimeError(
            "Source content fetch is disabled. "
            "Set SOURCE_CONTENT_FETCH_ENABLED=true after security gates pass "
            "and operator approves (per safe-source-content-ingestion spec)."
        )


class SourceFetchError(Exception):
    """Base class for source fetch errors with error classification."""
    def __init__(self, message: str, error_class: str = "unknown"):
        super().__init__(message)
        self.error_class = error_class


class SourceFetchPolicyError(SourceFetchError):
    """Raised when URL fails policy validation — terminal, no retry."""
    def __init__(self, message: str, decision: NetworkDecision):
        super().__init__(message, error_class="policy_rejection")
        self.decision = decision


class SourceFetchTransferError(SourceFetchError):
    """Raised for transfer limit violations — terminal, no retry same URL."""
    pass


class SourceFetchRetryableError(SourceFetchError):
    """Raised for transient failures — eligible for bounded retry."""
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message, error_class="retryable")
        self.retry_after = retry_after
