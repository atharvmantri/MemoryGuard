# SPDX-License-Identifier: Apache-2.0
"""Feature flag definitions for MemoryGuard.

This module is the single source of truth for which capabilities are active in a
given MemoryGuard deployment. It is part of the open-source ``core`` package and
therefore obeys the open-core boundary rule:

* It imports **no** commercial code.
* It only *reads* flags; it never wires up or injects implementations.

In OSS local mode every commercial flag defaults to ``False`` and the platform
runs entirely on local OSS defaults with no external LLM API. Commercial flags
are flipped on (typically in cloud) via ``MEMORYGUARD_*`` environment variables
resolved by :meth:`FeatureFlags.from_env`.

Standard library only.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields

__all__ = ["FeatureFlags"]

# Strings that, when found in a MEMORYGUARD_* env var, resolve to ``True``.
_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
# Strings that resolve to ``False``.
_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", ""})

# Prefix used for every MemoryGuard feature-flag environment variable.
_ENV_PREFIX = "MEMORYGUARD_"


def _parse_bool(raw: str, *, default: bool) -> bool:
    """Parse an environment-variable string into a boolean.

    Recognized truthy/falsey tokens are matched case-insensitively after
    trimming whitespace. Unrecognized values fall back to ``default`` so that a
    malformed override never silently disables an OSS default or enables a
    commercial feature.
    """
    token = raw.strip().lower()
    if token in _TRUE_VALUES:
        return True
    if token in _FALSE_VALUES:
        return False
    return default


@dataclass(frozen=True)
class FeatureFlags:
    """Immutable set of MemoryGuard feature flags.

    OSS flags default ``True`` (the local-first engine is always available).
    Commercial flags default ``False`` and are enabled per-deployment.
    """

    # --- OSS (always available) ---
    local_store: bool = True
    hybrid_retrieval: bool = True
    basic_trust: bool = True
    basic_contradiction: bool = True
    mcp_server: bool = True
    local_dashboard: bool = True

    # --- OSS model layer (effectively ON by default; local, no external LLM) ---
    local_embedder: bool = True
    reranker: bool = True  # enable Stage-2 reranking (OSS heuristic reranker by default)
    heuristic_reranker: bool = True
    rule_contradiction: bool = True
    basic_poison_detector: bool = True
    basic_sensitive_detector: bool = True
    deterministic_trust: bool = True

    # --- COMMERCIAL (default OFF; flipped by license/env in cloud) ---
    cloud_store: bool = False  # Postgres + pgvector
    cloud_auth: bool = False  # SSO/login
    workspaces: bool = False
    teams: bool = False  # RBAC/SCIM
    audit_log: bool = False  # durable audit + confidence reports
    policy_engine: bool = False
    review_queue: bool = False
    advanced_trust: bool = False  # trust-aware analytics
    poisoning_detection: bool = False
    pii_detection: bool = False
    billing: bool = False
    admin_console: bool = False

    # --- COMMERCIAL model layer (default OFF; learned/hosted models) ---
    advanced_embeddings: bool = False  # fine-tuned MemoryGuard embedder
    learned_reranker: bool = False
    learned_contradiction: bool = False
    advanced_poison_detection: bool = False
    advanced_pii_model: bool = False
    learned_trust_model: bool = False
    model_serving: bool = False  # hosted model-serving API
    hosted_inference: bool = False
    model_analytics: bool = False

    @classmethod
    def from_env(cls, environ: "dict[str, str] | None" = None) -> "FeatureFlags":
        """Build a :class:`FeatureFlags` from ``MEMORYGUARD_*`` environment vars.

        Each flag ``foo`` is overridden by the environment variable
        ``MEMORYGUARD_FOO`` (the field name upper-cased). For example,
        ``MEMORYGUARD_CLOUD_STORE=true`` enables the ``cloud_store`` flag while
        ``MEMORYGUARD_LOCAL_STORE=false`` disables ``local_store``.

        Values are parsed case-insensitively. Variables that are absent leave
        the flag at its declared default; values that cannot be parsed also fall
        back to the default. No commercial code is imported or executed here.

        Args:
            environ: Optional mapping to read from. Defaults to ``os.environ``.

        Returns:
            A new, immutable ``FeatureFlags`` instance.
        """
        env = os.environ if environ is None else environ

        overrides: "dict[str, bool]" = {}
        for f in fields(cls):
            env_key = _ENV_PREFIX + f.name.upper()
            if env_key in env:
                default = f.default
                # All fields are booleans with concrete defaults.
                overrides[f.name] = _parse_bool(env[env_key], default=bool(default))

        return cls(**overrides)

    def as_dict(self) -> "dict[str, bool]":
        """Return the flags as a plain ``{name: bool}`` mapping.

        Useful for serializing the active feature set (for example, in the
        ``/v1/health`` response so the frontend can show/hide commercial UI).
        The result is a fresh dict; mutating it does not affect this immutable
        instance.
        """
        return asdict(self)
