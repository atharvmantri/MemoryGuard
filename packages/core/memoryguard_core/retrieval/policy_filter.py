# SPDX-License-Identifier: Apache-2.0
"""Policy and ingestion-inspection injection interfaces and OSS defaults.

Defines two abstract contracts used by the retrieval and ingestion paths, plus
their OSS defaults:

* :class:`PolicyProvider` — decides whether a memory record may be used for a
  given retrieval context. OSS default :class:`AllowAllPolicy` allows everything.
* :class:`IngestionInspector` — inspects a record at ingestion time and may flag
  its sensitivity/status. OSS default :class:`NoOpInspector` returns the record
  unchanged.

Commercial builds inject a real policy engine and security inspector
(poisoning/PII detection) behind these same interfaces. This module never
imports any commercial package and uses the Python standard library only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from memoryguard_core.models import MemoryRecord, MemoryStatus, Sensitivity

__all__ = [
    "PolicyProvider",
    "AllowAllPolicy",
    "IngestionInspector",
    "NoOpInspector",
]

# Re-exported for callers that select/flag sensitivity or status via this module
# (kept referenced so the imports document the inspector contract surface).
_ = (Sensitivity, MemoryStatus)


class PolicyProvider(ABC):
    """Abstract policy decision point for retrieval.

    Implementations decide whether ``record`` may be used in the context
    ``ctx`` (e.g. workspace, actor, scope). The OSS default allows everything;
    commercial builds inject a policy engine behind this interface.
    """

    @abstractmethod
    def evaluate(self, record: MemoryRecord, ctx: dict) -> tuple[bool, list[str]]:
        """Return ``(allowed, reasons)`` for using ``record`` under ``ctx``.

        ``allowed`` is ``True`` when the record may be used. ``reasons`` is a
        list of human-readable strings explaining the decision (it may be empty
        when allowed with no commentary).
        """

        raise NotImplementedError


class AllowAllPolicy(PolicyProvider):
    """OSS default :class:`PolicyProvider` that allows every record."""

    def evaluate(self, record: MemoryRecord, ctx: dict) -> tuple[bool, list[str]]:
        return True, []


class IngestionInspector(ABC):
    """Abstract ingestion-time inspector.

    Implementations may inspect a :class:`MemoryRecord` and return a (possibly
    modified) record — for example flagging ``sensitivity`` (PII/secret) or
    transitioning ``status`` (e.g. to ``disputed``) for poisoned content. The
    OSS default is a no-op; commercial builds inject poison/PII detectors.
    """

    @abstractmethod
    def inspect(self, record: MemoryRecord) -> MemoryRecord:
        """Inspect ``record`` and return a record (possibly flagged)."""

        raise NotImplementedError


class NoOpInspector(IngestionInspector):
    """OSS default :class:`IngestionInspector` that returns the record as-is."""

    def inspect(self, record: MemoryRecord) -> MemoryRecord:
        return record
