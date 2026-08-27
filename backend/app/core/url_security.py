"""Validation helpers for user-configurable outbound service URLs."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def _host_matches(host: str, allowed_hosts: set[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == item or host.endswith(f".{item}") for item in allowed_hosts)


def validate_service_url(
    value: str,
    *,
    allowed_hosts: set[str],
    allow_private_ip: bool = False,
) -> str:
    """Return a normalized URL or raise ValueError when the target is unsafe."""
    raw = (value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https service URLs are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Service URL must contain a host and no embedded credentials")

    host = parsed.hostname.lower().rstrip(".")
    if not _host_matches(host, {h.lower().rstrip(".") for h in allowed_hosts if h}):
        raise ValueError(f"Host '{host}' is not in the configured allowlist")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not allow_private_ip and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("Private, loopback, link-local, and reserved IP targets are not allowed")
    return raw


def configured_host_set(raw: str) -> set[str]:
    return {item.strip().lower() for item in (raw or "").split(",") if item.strip()}
