# SPDX-License-Identifier: Apache-2.0
"""Rule-based contradiction detection for the MemoryGuard Trust Engine.

``RuleContradictionModel`` is the OSS, Phase-1 default behind the model-layer
``ContradictionModel`` / ``ContradictionClassifier`` interface
(``packages/models``). It decides whether two memories conflict using a
deterministic heuristic pipeline that runs entirely on-device with **no external
LLM API**:

1. **Comparability gate** — the two memories must share the same ``scope`` *and*
   have overlapping ``scope_ref`` values. Disjoint ``scope_ref`` values are never
   a contradiction (scope isolation, Property 10).
2. **Topical similarity** — cosine similarity of the two embeddings must be
   ``>= SIM_THRESHOLD`` (they talk about the same subject). Embeddings are taken
   from the records when present and dimension-compatible; otherwise they are
   computed on demand via an injected :class:`Embedder` (default
   :class:`LocalEmbedder`). If embeddings are unavailable the pipeline falls back
   to a token-overlap (Jaccard) similarity.
3. **Conflict signal** — a negation flip, antonym pair, or numeric/value
   mismatch on the shared subject (e.g. ``"uses Flask"`` vs ``"uses FastAPI"``,
   ``"PostgreSQL 15"`` vs ``"MySQL 8"``, ``"0.5"`` vs ``"0.7"``,
   ``"never"`` vs its absence).
4. **Decision** — ``is_contradiction = comparable AND similar AND conflicting``;
   ``confidence = f(similarity, conflict_strength)`` in ``[0, 1]``.

Documented invariants (design *Contradiction Detection Model*, Properties 8-11),
all guaranteed by construction:

* **Symmetry** — ``detect(a, b).is_contradiction == detect(b, a).is_contradiction``
  (every sub-signal is set-based / order-independent).
* **Irreflexive** — ``detect(a, a).is_contradiction is False``.
* **Scope isolation** — disjoint ``scope_ref`` -> ``is_contradiction is False``.
* **Bounded confidence** — ``0.0 <= confidence <= 1.0``.

``scan()`` (store-wide neighbor search) is implemented separately in task 8.2.

This module is part of the Apache-2.0 OSS core. It imports only from the OSS
``packages/core`` and ``packages/models`` layers and MUST NOT import from any
commercial package.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Optional

from memoryguard_core.decisions import extract_decision
from memoryguard_core.models import MemoryRecord
from memoryguard_models.base import (
    ContradictionModel,
    ContradictionResult,
    Embedder,
    ModelInfo,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids any import cycle
    from memoryguard_core.store.base import MemoryStore

__all__ = [
    "RuleContradictionModel",
    "SIM_THRESHOLD",
    "NEIGHBOR_LIMIT",
]


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

#: Minimum cosine (or token-overlap) similarity for two memories to be
#: considered topically comparable enough to contradict. Below this they are
#: treated as talking about different things.
SIM_THRESHOLD: float = 0.6

#: Number of nearest vector neighbors :meth:`RuleContradictionModel.scan` pulls
#: from the store as contradiction candidates (design *Contradiction Detection
#: Model -> scanContradictions*). Bounds the per-candidate work.
NEIGHBOR_LIMIT: int = 25

#: Conflict-signal strengths (each in ``[0, 1]``) by detected conflict kind.
_STRENGTH_NEGATION: float = 0.75
_STRENGTH_ANTONYM: float = 0.80
_STRENGTH_NUMERIC: float = 0.70
_STRENGTH_VALUE: float = 0.65

#: Model identity for the rule-based detector.
_MODEL_ID = "contradiction/rules"
_TASK = "contradiction"
_VERSION = "1.0.0"

#: Tokenizer: lowercase alphanumeric runs (deterministic, dependency-free).
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")

#: Numeric token pattern (integers / decimals), extracted from raw content.
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

#: Negation markers that flip the polarity of a statement. ``n't`` is matched as
#: a substring of contractions ("isn't", "doesn't", ...).
_NEGATIONS: frozenset[str] = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "without",
        "cannot",
        "cant",
        "dont",
        "doesnt",
        "isnt",
        "wasnt",
        "arent",
        "werent",
        "wont",
        "shouldnt",
        "neither",
        "nor",
        "unsupported",
        "disabled",
        "deny",
        "denied",
        "disallow",
        "disallowed",
        "false",
    }
)

#: Antonym pairs whose presence on opposite sides signals a direct conflict.
_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("enabled", "disabled"),
    ("enable", "disable"),
    ("true", "false"),
    ("on", "off"),
    ("allow", "deny"),
    ("allowed", "denied"),
    ("supported", "unsupported"),
    ("deprecated", "supported"),
    ("sync", "async"),
    ("synchronous", "asynchronous"),
    ("public", "private"),
    ("active", "inactive"),
    ("up", "down"),
    ("open", "closed"),
    ("present", "absent"),
    ("required", "optional"),
    ("stable", "unstable"),
)

#: Common English stopwords excluded when looking for *salient* distinctive
#: tokens, so "the project uses X" vs "the project uses Y" keys on X/Y.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "as",
        "and",
        "or",
        "but",
        "we",
        "our",
        "us",
        "they",
        "their",
        "uses",
        "use",
        "used",
        "using",
        "project",
        "projects",
        "system",
        "app",
        "application",
        "service",
        "default",
        "currently",
        "now",
        "has",
        "have",
        "will",
        "should",
        "must",
        "its",
        "set",
        "value",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp01(value: float) -> float:
    """Clamp ``value`` into ``[0.0, 1.0]``."""

    numeric = float(value)
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def _tokens(text: str) -> list[str]:
    """Tokenize ``text`` into lowercase alphanumeric tokens."""

    return _TOKEN_RE.findall(text.lower())


def _numbers(text: str) -> set[str]:
    """Return the set of numeric literals (normalized) found in ``text``."""

    out: set[str] = set()
    for raw in _NUMBER_RE.findall(text):
        try:
            out.add(repr(float(raw)))
        except ValueError:  # pragma: no cover - regex guarantees parseable
            continue
    return out


def _cosine(a: list[float], b: list[float]) -> Optional[float]:
    """Cosine similarity of two equal-length vectors, or ``None`` on mismatch.

    Returns ``0.0`` when either vector has zero magnitude.
    """

    if len(a) != len(b):
        return None
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard overlap of two token sets, in ``[0, 1]`` (``0`` if both empty)."""

    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _scope_refs_overlap(a: MemoryRecord, b: MemoryRecord) -> bool:
    """Whether two records share an overlapping ``scope_ref``.

    Two ``None`` scope_refs (e.g. ``global`` / ``org`` memories that bind to no
    specific id) overlap; two equal non-empty refs overlap; anything else
    (differing refs, or exactly one ref present) is disjoint. Symmetric.
    """

    ra = a.scope_ref
    rb = b.scope_ref
    if ra is None and rb is None:
        return True
    if ra is None or rb is None:
        return False
    return str(ra).strip() == str(rb).strip()


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def _has_negation(tokens: set[str]) -> bool:
    """Whether the token set contains a negation marker."""

    return bool(tokens & _NEGATIONS)


def _conflict_signal(
    cand: MemoryRecord, existing: MemoryRecord
) -> tuple[bool, float, Optional[str]]:
    """Detect a conflict signal between two (topically similar) memories.

    Returns ``(conflicting, strength, reason)`` where ``strength`` is in
    ``[0, 1]``. The checks are symmetric (set-based / XOR) so the result does not
    depend on argument order.
    """

    cand_decision = extract_decision(cand.content)
    existing_decision = extract_decision(existing.content)
    if (
        cand_decision is not None
        and existing_decision is not None
        and cand_decision.key == existing_decision.key
        and cand_decision.value.lower() != existing_decision.value.lower()
    ):
        return (
            True,
            0.92,
            f"decision conflict ({cand_decision.key}: "
            f"{existing_decision.value} vs {cand_decision.value})",
        )
    if (
        cand_decision is not None
        and existing_decision is not None
        and {cand_decision.key, existing_decision.key}
        & {"database", "database_local", "database_cloud"}
        and "database" in {cand_decision.key, existing_decision.key}
        and cand_decision.value.lower() != existing_decision.value.lower()
    ):
        return (
            True,
            0.90,
            f"decision conflict (database split: "
            f"{existing_decision.value} vs {cand_decision.value})",
        )

    a_tokens = _tokens(cand.content)
    b_tokens = _tokens(existing.content)
    a_set = set(a_tokens)
    b_set = set(b_tokens)

    # 1. Negation flip: a negation marker present on exactly one side.
    if _has_negation(a_set) != _has_negation(b_set):
        return True, _STRENGTH_NEGATION, "negation flip on shared subject"

    # 2. Antonym pair on opposite sides.
    for x, y in _ANTONYMS:
        if (x in a_set and y in b_set) or (y in a_set and x in b_set):
            return True, _STRENGTH_ANTONYM, f"antonym conflict ({x}/{y})"

    # 3. Numeric / value mismatch: both mention numbers but the sets differ.
    a_nums = _numbers(cand.content)
    b_nums = _numbers(existing.content)
    if a_nums and b_nums and a_nums != b_nums:
        return True, _STRENGTH_NUMERIC, "numeric/value mismatch on shared subject"

    # 4. Salient value mismatch: shared context plus a distinctive token on each
    #    side (e.g. "uses Flask" vs "uses FastAPI"). Requires shared context so
    #    unrelated statements are not flagged.
    shared = a_set & b_set
    only_a = {t for t in (a_set - b_set) if t not in _STOPWORDS}
    only_b = {t for t in (b_set - a_set) if t not in _STOPWORDS}
    if shared and only_a and only_b:
        return True, _STRENGTH_VALUE, "differing value on shared subject"

    return False, 0.0, None


# ---------------------------------------------------------------------------
# RuleContradictionModel
# ---------------------------------------------------------------------------


class RuleContradictionModel(ContradictionModel):
    """Deterministic, rule-based contradiction detector (OSS default).

    Args:
        embedder: optional :class:`Embedder` used to compute embeddings on demand
            when the records do not carry dimension-compatible embeddings. When
            omitted, a default :class:`LocalEmbedder` is constructed lazily on
            first use (kept lazy so importing this module never requires the
            embedder to be constructed).
        sim_threshold: minimum similarity for topical comparability (default
            :data:`SIM_THRESHOLD`).
    """

    def __init__(
        self,
        embedder: Optional[Embedder] = None,
        *,
        sim_threshold: float = SIM_THRESHOLD,
    ) -> None:
        self._embedder = embedder
        self._embedder_initialized = embedder is not None
        self._sim_threshold = float(sim_threshold)

    # -- model identity ----------------------------------------------------

    @property
    def info(self) -> ModelInfo:
        """Stable identity + version (``task="contradiction"``)."""

        return ModelInfo(model_id=_MODEL_ID, task=_TASK, version=_VERSION)

    # -- embedder access ---------------------------------------------------

    def _get_embedder(self) -> Optional[Embedder]:
        """Return the embedder, lazily constructing a default ``LocalEmbedder``.

        The default embedder import is deferred so that this module imports
        cleanly even if the embedder package is unavailable; a failure to build
        the default simply disables embedding-based similarity (the token
        fallback still applies).
        """

        if self._embedder is not None or self._embedder_initialized:
            return self._embedder
        self._embedder_initialized = True
        try:  # pragma: no cover - exercised indirectly
            from memoryguard_models.embedder.local_embedder import LocalEmbedder

            self._embedder = LocalEmbedder()
        except Exception:  # pragma: no cover - defensive: fall back to tokens
            self._embedder = None
        return self._embedder

    # -- similarity --------------------------------------------------------

    def _similarity(self, cand: MemoryRecord, existing: MemoryRecord) -> float:
        """Topical similarity in ``[0, 1]`` between two memories.

        Prefers cosine similarity of dimension-compatible embeddings (from the
        records, else computed via the embedder), and falls back to token-overlap
        (Jaccard) similarity. Symmetric and deterministic.
        """

        va = cand.embedding
        vb = existing.embedding

        # Use stored embeddings only when both are present and dimension-equal.
        if not (
            isinstance(va, list)
            and isinstance(vb, list)
            and len(va) > 0
            and len(va) == len(vb)
        ):
            embedder = self._get_embedder()
            if embedder is not None:
                try:
                    va = embedder.embed(cand.content)
                    vb = embedder.embed(existing.content)
                except Exception:  # pragma: no cover - defensive
                    va = vb = None
            else:  # pragma: no cover - default embedder normally available
                va = vb = None

        if isinstance(va, list) and isinstance(vb, list) and len(va) == len(vb) and va:
            cos = _cosine(va, vb)
            if cos is not None:
                # Map cosine [-1, 1] -> [0, 1] for a bounded confidence input.
                return _clamp01((cos + 1.0) / 2.0 if cos < 0.0 else cos)

        # Fallback: token-overlap similarity.
        return _jaccard(set(_tokens(cand.content)), set(_tokens(existing.content)))

    # -- detection ---------------------------------------------------------

    def detect(
        self,
        candidate: MemoryRecord,
        existing: MemoryRecord,
    ) -> ContradictionResult:
        """Decide whether ``candidate`` and ``existing`` contradict.

        Implements the comparability -> similarity -> conflict pipeline and
        guarantees the documented invariants (symmetry, irreflexivity, scope
        isolation, bounded confidence).
        """

        # Irreflexive: a record never contradicts itself (Property 9).
        if candidate.memory_id == existing.memory_id:
            return ContradictionResult(
                is_contradiction=False,
                reason="same memory (irreflexive)",
                confidence=0.0,
            )

        # Comparability gate: same scope AND overlapping scope_ref (Property 10).
        comparable = candidate.scope == existing.scope and _scope_refs_overlap(
            candidate, existing
        )
        if not comparable:
            return ContradictionResult(
                is_contradiction=False,
                reason="not comparable (different scope or disjoint scope_ref)",
                confidence=0.0,
            )

        # Topical similarity. Structured decision conflicts are comparable even
        # when the lightweight embedder undershoots a natural-language paraphrase.
        similarity = self._similarity(candidate, existing)

        # Conflict signal.
        conflicting, strength, conflict_reason = _conflict_signal(candidate, existing)
        structured_decision_conflict = bool(
            conflict_reason and conflict_reason.startswith("decision conflict")
        )
        similar = similarity >= self._sim_threshold or structured_decision_conflict

        is_contradiction = bool(similar and conflicting)

        if is_contradiction:
            confidence = _clamp01(0.5 * similarity + 0.5 * strength)
            reason = (
                f"{conflict_reason}; topical similarity {similarity:.2f}"
                f" >= {self._sim_threshold:.2f}"
            )
        else:
            # Low confidence that the pair contradicts; bounded in [0, 1].
            confidence = _clamp01(0.25 * similarity)
            if not similar:
                reason = (
                    f"not topically similar (similarity {similarity:.2f}"
                    f" < {self._sim_threshold:.2f})"
                )
            else:
                reason = "topically similar but no conflict signal detected"

        return ContradictionResult(
            is_contradiction=is_contradiction,
            reason=reason,
            confidence=confidence,
        )

    # -- store-wide scan (task 8.2) ----------------------------------------

    def scan(
        self,
        candidate: MemoryRecord,
        store: "MemoryStore",
    ) -> list[tuple[str, ContradictionResult]]:
        """Scan ``store`` for memories that contradict ``candidate``.

        Implements the design's *scanContradictions* algorithm:

        1. Gather candidate neighbors. When ``candidate`` carries a usable
           embedding, the ``NEIGHBOR_LIMIT`` nearest vectors are pulled via
           :meth:`MemoryStore.vector_search`. When no embedding is available the
           scan falls back to :meth:`MemoryStore.list` restricted to the
           candidate's own ``scope`` / ``scope_ref`` (the only memories that
           could be comparable anyway).
        2. Skip the candidate's own ``memory_id`` (a record never contradicts
           itself) and any id already collected.
        3. Run :meth:`detect` against each neighbor and collect
           ``(memory_id, ContradictionResult)`` for every detected contradiction.

        Guarantees (loop invariants from the design):

        * The returned list contains **no duplicate** ``memory_id`` values.
        * The candidate's own id is **never** included.
        * Records that are not comparable (disjoint scope) are never added,
          because :meth:`detect` already returns ``is_contradiction=False`` for
          them.

        Returns the list of conflicts (possibly empty). Deterministic and
        on-device: no external LLM API.
        """

        conflicts: list[tuple[str, ContradictionResult]] = []
        seen: set[str] = {candidate.memory_id}

        embedding = candidate.embedding
        if isinstance(embedding, list) and len(embedding) > 0:
            neighbors = store.vector_search(embedding, NEIGHBOR_LIMIT)
        else:
            # No embedding -> fall back to same-scope listing.
            neighbors = [
                (record, 0.0)
                for record in store.list(
                    scope=candidate.scope, scope_ref=candidate.scope_ref
                )
            ]

        for other, _similarity in neighbors:
            other_id = other.memory_id
            # Skip the candidate itself and any id already processed
            # (guarantees no duplicates and never includes the candidate).
            if other_id in seen:
                continue
            seen.add(other_id)

            result = self.detect(candidate, other)
            if result.is_contradiction:
                conflicts.append((other_id, result))

        return conflicts
