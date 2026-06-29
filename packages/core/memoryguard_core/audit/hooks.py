# SPDX-License-Identifier: Apache-2.0
"""Audit sink injection interface and OSS defaults.

Defines the :class:`AuditSink` abstract contract used by the retrieval and
ingestion paths to record auditable events, plus the OSS defaults:

* :class:`LocalJsonlAuditSink` — appends redacted JSON lines to a local audit
  log file (default ``.memoryguard/audit.jsonl``).
* :class:`NullAuditSink` — discards every event (useful for tests / opt-out).

Commercial builds inject a durable audit-DB implementation behind the same
:class:`AuditSink` interface; this module never imports any commercial package.

Security invariant
-------------------
The local sink MUST NOT persist secret values. Before an event is written it is
passed through :func:`redact_event`, which:

* masks any key whose name looks like a secret (``secret``, ``password``,
  ``token``, ``api_key``, ``key`` and common variants), at any nesting depth; and
* never logs the raw ``content`` of a memory flagged ``secret`` or ``pii`` —
  for such events only the ``memory_id`` and ``reasons`` are retained.

This module is dependency-free (Python standard library only) and part of the
Apache-2.0 OSS core.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "AuditSink",
    "LocalJsonlAuditSink",
    "NullAuditSink",
    "redact_event",
    "REDACTED",
    "DEFAULT_AUDIT_PATH",
]

# Placeholder written in place of any redacted/secret value.
REDACTED = "[REDACTED]"

# Default on-disk location for the local audit log, relative to the cwd.
DEFAULT_AUDIT_PATH = Path(".memoryguard") / "audit.jsonl"

# Substrings that, when present in a key name, mark the value as a secret.
# Matched case-insensitively against the normalized key (non-alphanumeric
# characters stripped) so that ``api-key``, ``API_KEY`` and ``apiKey`` all match.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "secret",
    "password",
    "passwd",
    "token",
    "apikey",
    "accesskey",
    "secretkey",
    "privatekey",
    "credential",
    "authorization",
    "auth",
    "key",
)

# Sensitivity tiers whose raw content must never be logged.
_PROTECTED_SENSITIVITIES = frozenset({"secret", "pii"})

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _normalize_key(key: Any) -> str:
    """Lowercase ``key`` and strip non-alphanumeric chars for matching."""

    return _NON_ALNUM.sub("", str(key).lower())


def _is_secret_key(key: Any) -> bool:
    """Return ``True`` when ``key`` looks like it holds a secret value."""

    normalized = _normalize_key(key)
    if not normalized:
        return False
    return any(pattern in normalized for pattern in _SECRET_KEY_PATTERNS)


def _redact_value(value: Any) -> Any:
    """Recursively redact secret-looking keys within ``value``."""

    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_secret_key(k) else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _sensitivity_token(value: Any) -> str:
    """Normalize a sensitivity value (enum or str) to a lowercase token."""

    # Enums expose ``.value``; fall back to the string form otherwise.
    token = getattr(value, "value", value)
    return str(token).strip().lower()


def redact_event(event: dict) -> dict:
    """Return a redacted, JSON-safe copy of ``event`` safe to persist.

    The original mapping is never mutated. Two protections are applied:

    1. Any key (at any depth) whose name looks like a secret is masked.
    2. If the event concerns a memory whose ``sensitivity`` is ``secret`` or
       ``pii``, its raw ``content`` is dropped entirely; only ``memory_id`` and
       ``reasons`` are preserved for that sensitive payload.
    """

    if not isinstance(event, dict):  # defensive: only dict events are supported
        raise TypeError(f"audit event must be a dict; got {type(event).__name__}")

    redacted = _redact_value(dict(event))

    if _event_is_sensitive(event):
        redacted = _drop_sensitive_content(redacted)

    return redacted


def _event_is_sensitive(event: dict) -> bool:
    """Detect whether ``event`` (or nested payloads) is secret/PII flagged."""

    sensitivity = event.get("sensitivity")
    if sensitivity is not None and _sensitivity_token(sensitivity) in _PROTECTED_SENSITIVITIES:
        return True

    # Allow a nested "memory" payload to carry its own sensitivity tier.
    memory = event.get("memory")
    if isinstance(memory, dict):
        nested = memory.get("sensitivity")
        if nested is not None and _sensitivity_token(nested) in _PROTECTED_SENSITIVITIES:
            return True

    return False


def _reduce_to_id_and_reasons(payload: dict) -> dict:
    """Keep only ``memory_id`` and ``reasons`` from a sensitive payload."""

    reduced: dict[str, Any] = {}
    if "memory_id" in payload:
        reduced["memory_id"] = payload["memory_id"]
    if "reasons" in payload:
        reduced["reasons"] = payload["reasons"]
    reduced["content"] = REDACTED
    reduced["sensitivity_redacted"] = True
    return reduced


def _drop_sensitive_content(event: dict) -> dict:
    """Strip raw content from a secret/PII event, keeping id + reasons."""

    result = dict(event)

    # Top-level content: replace with a marker, keep id/reasons in place.
    if "content" in result:
        result["content"] = REDACTED
        result["sensitivity_redacted"] = True

    # Nested memory payload, if present.
    memory = result.get("memory")
    if isinstance(memory, dict):
        result["memory"] = _reduce_to_id_and_reasons(memory)

    return result


class AuditSink(ABC):
    """Abstract sink for auditable events.

    Implementations persist or forward audit events. The OSS default writes
    redacted JSON lines to a local file; commercial builds inject a durable
    audit database behind this same interface.
    """

    @abstractmethod
    def record(self, event: dict) -> None:
        """Record a single audit ``event``."""

        raise NotImplementedError


class NullAuditSink(AuditSink):
    """An :class:`AuditSink` that discards every event.

    Useful for tests, ephemeral runs, or explicit audit opt-out.
    """

    def record(self, event: dict) -> None:  # noqa: D401 - intentional no-op
        return None


class LocalJsonlAuditSink(AuditSink):
    """OSS default sink: appends redacted JSON lines to a local file.

    Each :meth:`record` call writes exactly one line of JSON to the configured
    path (default :data:`DEFAULT_AUDIT_PATH`). Parent directories are created on
    first write. Every event is passed through :func:`redact_event` first, so
    secret values and the raw content of secret/PII memories are never written.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_AUDIT_PATH

    @property
    def path(self) -> Path:
        """The file this sink appends audit lines to."""

        return self._path

    def record(self, event: dict) -> None:
        safe_event = redact_event(event)
        line = json.dumps(safe_event, ensure_ascii=False, default=str, sort_keys=True)

        parent = self._path.parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")

    def read_events(self) -> Iterable[dict]:
        """Yield the persisted audit events (convenience for inspection/tests)."""

        if not self._path.exists():
            return []
        events: list[dict] = []
        with open(self._path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if raw:
                    events.append(json.loads(raw))
        return events
