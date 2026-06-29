# SPDX-License-Identifier: Apache-2.0
"""Composite ingestion inspector — the OSS default ``IngestionInspector``.

:class:`CompositeIngestionInspector` chains an ordered list of
:class:`~memoryguard_core.retrieval.policy_filter.IngestionInspector`
implementations, threading the (possibly mutated) :class:`MemoryRecord` through
each one in turn. The OSS default chains the two local, dependency-free
detectors:

#. :class:`~memoryguard_models.poison_detector.basic.BasicPoisonDetector` —
   flags prompt-injection/poisoning, downgrades ``trust_score`` and routes the
   record to review (``status = DISPUTED``), and
#. :class:`~memoryguard_models.sensitive_data.basic.BasicSensitiveDataDetector`
   — elevates ``sensitivity`` (``secret``/``pii``) for secrets/keys/PII.

This composite is the default ``IngestionInspector`` wired in by the engine
composition root. Commercial builds inject richer inspectors behind the same
interface; this module never imports any commercial package.

Ingested content is always treated strictly as **data**: each inspector only
*matches/annotates* the content and never executes or interprets it as an
instruction (Requirements 24.1, 25.1, 9.4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from memoryguard_core.models import MemoryRecord
from memoryguard_core.retrieval.policy_filter import IngestionInspector

__all__ = ["CompositeIngestionInspector"]


def _default_inspectors() -> list[IngestionInspector]:
    """Build the OSS default inspector chain.

    Imported lazily so that constructing the composite (and importing this
    module) does not create a hard import-time dependency on the models package
    for callers that always inject their own inspector list.
    """

    from memoryguard_models.poison_detector.basic import BasicPoisonDetector
    from memoryguard_models.sensitive_data.basic import BasicSensitiveDataDetector

    return [BasicPoisonDetector(), BasicSensitiveDataDetector()]


class CompositeIngestionInspector(IngestionInspector):
    """Chain several :class:`IngestionInspector` instances into one.

    Each inspector's :meth:`inspect` is called in order, and the (possibly
    mutated) record returned by one inspector is passed to the next, so flags
    from every inspector accumulate on the same record.

    Args:
        inspectors: Ordered inspectors to run. When ``None`` (the default), the
            OSS default chain ``[BasicPoisonDetector(),
            BasicSensitiveDataDetector()]`` is built. An explicit (possibly
            empty) iterable is used as-is, making the chain fully injectable.

    The composite treats content strictly as data; it delegates to its member
    inspectors and never executes ingested content.
    """

    def __init__(
        self,
        inspectors: Iterable[IngestionInspector] | None = None,
    ) -> None:
        if inspectors is None:
            self._inspectors: list[IngestionInspector] = _default_inspectors()
        else:
            self._inspectors = list(inspectors)

    @property
    def inspectors(self) -> Sequence[IngestionInspector]:
        """The ordered inspectors this composite runs (read-only view)."""

        return tuple(self._inspectors)

    def inspect(self, record: MemoryRecord) -> MemoryRecord:
        """Run every member inspector in order, threading the record through.

        Returns the final (possibly mutated) record. With an empty chain the
        record is returned unchanged.
        """

        for inspector in self._inspectors:
            record = inspector.inspect(record)
        return record
