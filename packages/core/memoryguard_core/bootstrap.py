# SPDX-License-Identifier: Apache-2.0
"""OSS composition root â€” wire a fully local :class:`MemoryGuardEngine`.

:func:`build_local_engine` is the single place where the OSS local-first engine
is assembled from its parts. It selects the OSS local defaults for every model
component (no learned/hosted/commercial implementation is ever imported) and
enforces the local-first invariant of the platform: the **only** outbound call
any component may make is to the local on-device embedder, and that embedder's
default backend itself performs no network I/O (design *Core Principle:
Local-First Intelligence*).

Wiring (all Apache-2.0 OSS defaults):

* :class:`~memoryguard_core.store.sqlite_store.SqliteStore` â€” local SQLite store
  (``":memory:"`` or a filesystem path).
* :class:`~memoryguard_models.embedder.local_embedder.LocalEmbedder` â€” on-device
  384-dim deterministic embedder (no external API), registered in the model
  registry + served by the local inference runner.
* :class:`~memoryguard_models.reranker.heuristic.HeuristicReranker` â€” Stage-2
  reranker.
* :class:`~memoryguard_core.trust.scoring.DeterministicTrustModel` +
  :class:`~memoryguard_core.trust.contradiction.RuleContradictionModel` wired
  into a :class:`~memoryguard_core.trust.engine.TrustEngine`.
* :class:`~memoryguard_models.registry.LocalFileModelRegistry` +
  :class:`~memoryguard_serving.local_runner.LocalInferenceRunner` resolved
  through a :class:`~memoryguard_models.loader.ModelLoader` (registry-backed,
  on-device, no network). The loader is seeded with the OSS local defaults for
  every model task and the active :class:`FeatureFlags`, so each task resolves
  to its OSS local default while the task's commercial flag is off
  (Requirements 28.1, 28.3).
* :class:`~memoryguard_core.ingestion.inspectors.CompositeIngestionInspector` â€”
  OSS default chain (basic poison + sensitive-data detection).
* :class:`~memoryguard_core.retrieval.policy_filter.AllowAllPolicy` â€” OSS policy.
* an :class:`AuditSink`: :class:`LocalJsonlAuditSink` when ``audit_path`` is
  given, else :class:`NullAuditSink`.
* :class:`~memoryguard_core.retrieval.service.RetrievalService` over the store,
  embedder, and reranker.
* :class:`~memoryguard_core.flags.FeatureFlags` â€” defaults to
  :meth:`FeatureFlags.from_env`, with every commercial flag off so only OSS
  defaults are selected.

Because commercial flags default off, no commercial interface implementation is
selected; the resulting engine runs entirely on OSS local defaults with zero
external LLM API calls (Requirements 1.6, 16.5, 17.2, 17.3, 28.1, 28.3).

This module is part of the Apache-2.0 OSS core. It MUST NOT import from any
commercial package; the OSS ``memoryguard_models`` / ``memoryguard_serving``
local defaults imported here are themselves Apache-2.0 OSS and are permitted at
the composition root by the design's open-core boundary rules.
"""

from __future__ import annotations

from typing import Optional

from memoryguard_core.audit.hooks import (
    AuditSink,
    LocalJsonlAuditSink,
    NullAuditSink,
)
from memoryguard_core.engine import MemoryGuardEngine
from memoryguard_core.flags import FeatureFlags
from memoryguard_core.ingestion.inspectors import CompositeIngestionInspector
from memoryguard_core.retrieval.policy_filter import AllowAllPolicy, PolicyProvider
from memoryguard_core.retrieval.service import RetrievalService
from memoryguard_core.store.base import MemoryStore
from memoryguard_core.store.sqlite_store import SqliteStore
from memoryguard_core.trust.contradiction import RuleContradictionModel
from memoryguard_core.trust.engine import TrustEngine
from memoryguard_core.trust.scoring import DeterministicTrustModel
from memoryguard_models.embedder.local_embedder import (
    LocalEmbedder,
    register_local_embedder,
)
from memoryguard_models.loader import ModelLoader
from memoryguard_models.poison_detector import BasicPoisonDetector
from memoryguard_models.registry import LocalFileModelRegistry
from memoryguard_models.reranker.heuristic import HeuristicReranker
from memoryguard_models.sensitive_data import BasicSensitiveDataDetector

__all__ = ["build_local_engine"]

#: Default store location for the OSS local engine: an ephemeral in-memory store.
DEFAULT_DB_PATH = ":memory:"


def build_local_engine(
    db_path: str = DEFAULT_DB_PATH,
    *,
    flags: Optional[FeatureFlags] = None,
    audit_path: Optional[str] = None,
    store: Optional[MemoryStore] = None,
    audit: Optional[AuditSink] = None,
    policy: Optional[PolicyProvider] = None,
) -> MemoryGuardEngine:
    """Construct a fully wired, local-first :class:`MemoryGuardEngine`.

    Args:
        db_path: SQLite store location â€” a filesystem path, or ``":memory:"``
            (the default) for an ephemeral in-memory store. Ignored when an
            explicit ``store`` is supplied.
        flags: optional :class:`FeatureFlags`. When omitted, flags are read from
            the environment via :meth:`FeatureFlags.from_env` (commercial flags
            default off, so only OSS local defaults are selected).
        audit_path: optional path for the local JSONL audit log. When provided a
            :class:`LocalJsonlAuditSink` is used (it redacts secret values); when
            omitted a :class:`NullAuditSink` is used (no files written).
        store: optional pre-built :class:`MemoryStore` backend. When provided it
            is used as-is (e.g. a ``PostgresStore`` injected by the hosted API's
            composition root when ``cloud_store`` is enabled) and ``db_path`` is
            ignored; otherwise a local :class:`SqliteStore` at ``db_path`` is
            created. The store backend is the only swappable piece â€” every model
            component remains an OSS local default regardless.
        audit: optional pre-built :class:`AuditSink`. When provided it is used
            as-is (e.g. the commercial durable audit sink injected by the hosted
            API's composition root when ``audit_log`` is enabled), **replacing**
            the local JSONL/null sink. The injected sink is a core ``AuditSink``
            instance, so this composition root never imports a commercial package
            to honor the open-core boundary; ``audit_path`` is ignored when an
            explicit ``audit`` is supplied. When omitted, a
            :class:`LocalJsonlAuditSink` (if ``audit_path`` is given) or a
            :class:`NullAuditSink` is used.
        policy: optional pre-built :class:`PolicyProvider`. When provided it is
            used as-is replacing the OSS default
            :class:`AllowAllPolicy`. The injected provider is a core
            ``PolicyProvider`` instance, so this composition root never imports a
            commercial package and the open-core boundary holds. When omitted the
            permissive OSS :class:`AllowAllPolicy` is used.

    Returns:
        A :class:`MemoryGuardEngine` wired entirely from OSS local defaults.

    Local-first guarantee:
        Every model component is an on-device OSS default. The only outbound call
        any component makes is to the local :class:`LocalEmbedder`, whose default
        backend performs no network I/O. No commercial/learned/hosted model is
        imported or selected while the commercial flags are off. When a ``store``
        is injected its own connectivity (e.g. a cloud database) is the
        deployment operator's responsibility and is outside the model path.
    """

    active_flags = flags if flags is not None else FeatureFlags.from_env()

    # --- Store: injected backend (e.g. PostgresStore) or local SQLite. ---
    store = store if store is not None else SqliteStore(db_path)

    # --- Model layer: OSS local defaults (no external API, no network). ---
    embedder = LocalEmbedder()
    reranker = HeuristicReranker()
    trust_model = DeterministicTrustModel()
    contradiction_model = RuleContradictionModel(embedder=embedder)

    # Registry + on-device inference runner, resolved through the loader. The
    # local embedder is registered in the registry (so it is discoverable by
    # model_id/version) and served in-process by the runner (no artifact files,
    # no network). The loader is seeded with the OSS local defaults + active
    # flags so each task resolves to its OSS default while its commercial flag
    # is off.
    loader = _build_local_model_loader(
        active_flags,
        embedder=embedder,
        reranker=reranker,
        trust_model=trust_model,
        contradiction_model=contradiction_model,
    )

    # --- Trust engine: deterministic scorer + rule contradiction model. ---
    trust_engine = TrustEngine(
        trust_model=trust_model,
        contradiction_model=contradiction_model,
    )

    # --- Injection interfaces: OSS defaults. -----------------------------
    inspector = CompositeIngestionInspector()  # basic poison + sensitive-data
    # An injected provider may replace the permissive default.\n    # otherwise fall back to the OSS AllowAllPolicy. The injected value is a core
    # ``PolicyProvider`` â€” the open-core boundary holds.
    policy = policy if policy is not None else AllowAllPolicy()
    # An injected sink (e.g. the commercial durable audit sink) replaces the
    # local JSONL/null sink in cloud mode; otherwise fall back to the OSS
    # defaults. The injected value is a core ``AuditSink`` â€” the boundary holds.
    if audit is not None:
        audit_sink: AuditSink = audit
    elif audit_path is not None:
        audit_sink = LocalJsonlAuditSink(audit_path)
    else:
        audit_sink = NullAuditSink()

    # --- Retrieval service over the store + embedder + reranker. ---------
    retrieval = RetrievalService(
        store,
        embedder,
        reranker,
        policy=policy,
        audit=audit_sink,
    )

    engine = MemoryGuardEngine(
        store,
        embedder,
        trust_engine,
        retrieval,
        active_flags,
        audit=audit_sink,
        policy=policy,
        inspector=inspector,
    )
    # Keep the loader reachable for components that resolve models on demand
    # (e.g. future learned-model injection). It stays an OSS local default here.
    engine.model_loader = loader  # type: ignore[attr-defined]
    return engine


def _build_local_model_loader(
    flags: FeatureFlags,
    *,
    embedder: LocalEmbedder,
    reranker: HeuristicReranker,
    trust_model: DeterministicTrustModel,
    contradiction_model: RuleContradictionModel,
) -> ModelLoader:
    """Build a :class:`ModelLoader` over the local registry + inference runner.

    Registers the OSS default embedder in a :class:`LocalFileModelRegistry` and
    (when available) serves it in-process via a :class:`LocalInferenceRunner`,
    then seeds the loader with the OSS local-default factory for every model
    task and the active ``flags``. With every commercial flag off the loader
    selects the OSS local default for each task; nothing commercial or hosted is
    imported or selected.

    The :class:`LocalInferenceRunner` is imported lazily from the OSS
    ``model-serving`` package so the core composition root has no hard
    import-time dependency on it; if that package is unavailable the loader is
    built with a minimal no-op runner that still performs no network I/O.
    """

    registry = LocalFileModelRegistry()

    try:
        from memoryguard_serving.local_runner import LocalInferenceRunner

        runner = LocalInferenceRunner()
    except Exception:  # pragma: no cover - model-serving optional at runtime
        runner = _NullInferenceRunner()

    # Register the OSS default embedder in the registry and serve it in-process
    # via the runner (no artifact files, no network access).
    register_local_embedder(registry, runner, embedder=embedder)

    # OSS local-default factory per model task. While each task's commercial
    # flag is off (the default) the loader resolves to these on-device defaults
    # (Requirements 28.1, 28.3). The shared instances are reused for the
    # embedder / reranker / trust / contradiction tasks so the loader and the
    # engine agree on a single OSS default; the detectors are constructed fresh.
    oss_defaults = {
        "embed": lambda: embedder,
        "rerank": lambda: reranker,
        "contradiction": lambda: contradiction_model,
        "poison": BasicPoisonDetector,
        "sensitive": BasicSensitiveDataDetector,
        "trust": lambda: trust_model,
    }

    return ModelLoader(registry, runner, flags, oss_defaults=oss_defaults)


class _NullInferenceRunner:
    """Fallback on-device inference runner (no network, no commercial code).

    Used only when the OSS ``model-serving`` package is not importable. It honors
    the ``InferenceRunner`` shape (``load`` + ``run``) plus the
    ``register_callable`` hook used by :func:`register_local_embedder`, without
    doing any I/O so the composition root stays local-first and import-safe.
    """

    def register_callable(  # pragma: no cover - trivial fallback
        self, model_id: object, fn: object, *, version: object = None
    ) -> None:
        return None

    def load(self, model_version: object) -> object:  # pragma: no cover - trivial
        return model_version

    def run(self, loaded: object, inputs: list[dict]) -> list[dict]:  # pragma: no cover
        return [dict(item) for item in inputs]

