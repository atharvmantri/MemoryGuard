# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard audit package.

Exposes the :class:`AuditSink` injection interface and its OSS defaults
(:class:`LocalJsonlAuditSink`, :class:`NullAuditSink`). Commercial builds inject
a durable audit-DB implementation behind the same interface; this package never
imports any commercial code.
"""

from __future__ import annotations

from memoryguard_core.audit.hooks import (
    DEFAULT_AUDIT_PATH,
    REDACTED,
    AuditSink,
    LocalJsonlAuditSink,
    NullAuditSink,
    redact_event,
)

__all__ = [
    "AuditSink",
    "LocalJsonlAuditSink",
    "NullAuditSink",
    "redact_event",
    "REDACTED",
    "DEFAULT_AUDIT_PATH",
]
