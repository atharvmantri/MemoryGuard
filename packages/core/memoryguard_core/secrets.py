# SPDX-License-Identifier: Apache-2.0
"""Deterministic secret detection/redaction helpers for local OSS paths."""

from __future__ import annotations

import re

__all__ = ["contains_secret", "redact_text"]

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*"
        r"(?:is|:|=)\s*['\"]?[^'\"\s,;]+"
    ),
    re.compile(r"(?i)\b(database_url|db_url)\b\s*(?:is|:|=)\s*['\"]?[^'\"\s]+"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@[^/\s]+"),
)


def contains_secret(text: object) -> bool:
    """Return True when ``text`` contains a common secret-looking pattern."""

    value = str(text)
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def redact_text(text: object) -> str:
    """Replace common secret-looking values in ``text`` with ``[REDACTED]``."""

    redacted = str(text)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_replacement, redacted)
    return redacted


def _replacement(match: re.Match[str]) -> str:
    groups = match.groups()
    if groups and groups[0] and re.match(r"(?i)^[a-z_ -]*(key|secret|token|password|passwd|pwd|url)", groups[0]):
        return f"{groups[0]} [REDACTED]"
    return "[REDACTED]"
