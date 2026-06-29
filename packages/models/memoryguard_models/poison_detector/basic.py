# SPDX-License-Identifier: Apache-2.0
"""Rules/heuristics OSS poison detector (``BasicPoisonDetector``).

``BasicPoisonDetector`` is the OSS default behind the :class:`PoisonDetector`
interface. It inspects a memory's *content* for prompt injection, jailbreaks,
exfiltration attempts, credential theft, remote-code-execution lures, sabotage,
data-exposure, and obfuscated payloads using a deterministic, dependency-free
set of regular-expression heuristics + known injection patterns. It runs
**locally with no external LLM API** (Requirement 24.5).

It additionally implements the core ``IngestionInspector`` contract via an
``inspect(record) -> MemoryRecord`` method so the same component can run at
ingestion time to:

* **downgrade trust** -- multiply the record's ``trust_score`` by
  ``(1 - severity)`` so more-severe content loses more trust, and
* **route to review** -- set ``status = DISPUTED`` and annotate
  ``metadata['poison']`` with the :class:`PoisonResult` as a plain dict.

Ingested content is always treated strictly as **data**; nothing matched here is
ever executed or interpreted as an instruction.

Design references: "MemoryGuard Poison Detector" and "Component Interfaces for
Commercial Injection" (the ``IngestionInspector`` ABC).

Requirements: 24.1, 24.2, 24.4, 24.5.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import NamedTuple

from memoryguard_core.models import MemoryRecord, MemoryStatus, clamp_trust_score
from memoryguard_models.base import ModelInfo, PoisonDetector, PoisonResult

# ---------------------------------------------------------------------------
# Optional core IngestionInspector contract (task 10.1 may land concurrently).
# We import it defensively so this module never hard-fails when the interface
# is not yet present, and register as a virtual subclass when it is, so that
# ``isinstance(detector, IngestionInspector)`` holds without a hard dependency.
# ---------------------------------------------------------------------------
_IngestionInspector = None
for _candidate in (
    "memoryguard_core.retrieval.policy_filter",
    "memoryguard_core.audit.hooks",
    "memoryguard_core.ingestion.inspectors",
):
    try:  # pragma: no cover - import resolution depends on task 10.1 timing
        _mod = __import__(_candidate, fromlist=["IngestionInspector"])
        _IngestionInspector = getattr(_mod, "IngestionInspector", None)
        if _IngestionInspector is not None:
            break
    except Exception:  # noqa: BLE001 - any import failure -> stay duck-typed
        _IngestionInspector = None


# ---------------------------------------------------------------------------
# Identity + tunable constants
# ---------------------------------------------------------------------------

#: Stable model id for the OSS rules poison detector.
DEFAULT_MODEL_ID = "poison/rules"

#: Semver for the OSS rules poison detector.
DEFAULT_VERSION = "1.0.0"

#: Minimum length for a contiguous base64-ish run to be treated as a suspicious
#: obfuscated payload. Kept high to avoid flagging ordinary tokens/identifiers.
_BASE64_MIN_LEN = 40


class _Rule(NamedTuple):
    """A single named detection rule."""

    category: str
    severity: float
    pattern: re.Pattern[str]


def _c(pattern: str, *, dotall: bool = False) -> re.Pattern[str]:
    """Compile ``pattern`` case-insensitively (optionally DOTALL)."""
    flags = re.IGNORECASE
    if dotall:
        flags |= re.DOTALL
    return re.compile(pattern, flags)


# ---------------------------------------------------------------------------
# Known injection / poisoning patterns, grouped by category.
#
# Categories emitted (deterministic priority order below):
#   prompt_injection, jailbreak, exfiltration, credential_theft,
#   remote_code_execution, sabotage, data_exposure, obfuscation
# ---------------------------------------------------------------------------

_RULES: tuple[_Rule, ...] = (
    # -- prompt injection / instruction override --------------------------
    _Rule(
        "prompt_injection",
        0.85,
        _c(r"\bignore\s+(?:the\s+|all\s+)?(?:above|previous|prior|preceding|earlier|foregoing)\b"),
    ),
    _Rule(
        "prompt_injection",
        0.85,
        _c(
            r"\bdisregard\s+(?:all\s+|the\s+)?"
            r"(?:above|prior|previous|preceding|earlier|instructions?|rules?)\b"
        ),
    ),
    _Rule(
        "prompt_injection",
        0.8,
        _c(r"\bforget\s+(?:everything|all|what\s+you|previous|prior)\b"),
    ),
    _Rule(
        "prompt_injection",
        0.8,
        _c(r"\bnote to (?:ai|assistant|the\s+(?:ai|assistant|model)|llm)\b"),
    ),
    _Rule("prompt_injection", 0.8, _c(r"\bthe real instruction is\b")),
    _Rule(
        "prompt_injection",
        0.85,
        _c(r"\bact as (?:the\s+)?(?:system|admin|administrator|root|developer)\b"),
    ),
    # System-prompt override at the start of a line, e.g. "SYSTEM: ...".
    _Rule("prompt_injection", 0.8, _c(r"^\s*(?:system|assistant|developer)\s*:", dotall=False)),
    # HTML / comment-embedded instructions.
    _Rule(
        "prompt_injection",
        0.8,
        _c(
            r"<!--.*?(?:assistant|system|ignore|override|instruction|prompt).*?-->",
            dotall=True,
        ),
    ),
    # -- jailbreak / guardrail override -----------------------------------
    _Rule("jailbreak", 0.85, _c(r"\byou are now\b")),
    _Rule("jailbreak", 0.9, _c(r"\bdan\b\s+and\s+have\s+no\s+restrictions\b")),
    _Rule("jailbreak", 0.7, _c(r"\bno\s+restrictions\b")),
    _Rule(
        "jailbreak",
        0.85,
        _c(
            r"\boverride\s+(?:your\s+)?"
            r"(?:guardrails?|safety|safeguards?|restrictions?|filters?|rules?|policies|security)\b"
        ),
    ),
    # -- exfiltration ------------------------------------------------------
    _Rule("exfiltration", 0.9, _c(r"\bexfiltrat")),
    _Rule("exfiltration", 0.8, _c(r"\bleak\s+(?:the|all|every|your|our|my)\b")),
    _Rule(
        "exfiltration",
        0.85,
        _c(
            r"\bsend\s+.{0,60}?\b(?:to|via)\b.{0,40}?"
            r"(?:webhook|https?://|url|endpoint|attacker|e-?mail|address|server)\b"
        ),
    ),
    _Rule("exfiltration", 0.85, _c(r"\bprint\s+the\s+contents?\s+of\b")),
    _Rule("exfiltration", 0.85, _c(r"\brespond\s+only\s+with\s+the\s+contents?\s+of\b")),
    _Rule("exfiltration", 0.75, _c(r"\bconnection string\b")),
    # -- credential theft --------------------------------------------------
    _Rule(
        "credential_theft",
        0.9,
        _c(
            r"\breveal\s+the\b.{0,40}"
            r"\b(?:password|secret|secrets|token|tokens|api[_\s-]?keys?|keys?|credentials?)\b"
        ),
    ),
    _Rule(
        "credential_theft",
        0.85,
        _c(r"\b(?:admin|root|database|db)\s+(?:password|secret|credentials?)\b"),
    ),
    # -- remote code execution --------------------------------------------
    _Rule("remote_code_execution", 0.95, _c(r"\b(?:curl|wget)\s+https?://")),
    _Rule("remote_code_execution", 0.9, _c(r"\|\s*(?:sh|bash|zsh)\b")),
    # -- sabotage ----------------------------------------------------------
    _Rule(
        "sabotage",
        0.85,
        _c(r"\bdelete\s+(?:all\s+)?.{0,30}?\b(?:logs?|audit|backups?|records?|tables?|database)\b"),
    ),
    _Rule(
        "sabotage",
        0.85,
        _c(r"\bdisable\s+(?:the\s+)?(?:policy|security|audit|guard|firewall|protection|safety)\b"),
    ),
    # -- data exposure -----------------------------------------------------
    _Rule("data_exposure", 0.8, _c(r"/etc/(?:passwd|shadow)\b")),
)

#: Deterministic ordering for emitted categories.
_CATEGORY_ORDER: tuple[str, ...] = (
    "prompt_injection",
    "jailbreak",
    "exfiltration",
    "credential_theft",
    "remote_code_execution",
    "sabotage",
    "data_exposure",
    "obfuscation",
)

#: Detects a long contiguous base64-ish run that mixes case + digits (a common
#: shape for obfuscated/encoded payloads). Conservative to avoid false positives.
_BASE64_RE = re.compile(rf"[A-Za-z0-9+/]{{{_BASE64_MIN_LEN},}}={{0,2}}")


def _looks_base64(token: str) -> bool:
    """Heuristic: a long token mixing upper, lower, and digits looks encoded."""
    core = token.rstrip("=")
    if len(core) < _BASE64_MIN_LEN:
        return False
    return (
        any(ch.islower() for ch in core)
        and any(ch.isupper() for ch in core)
        and any(ch.isdigit() for ch in core)
    )


class BasicPoisonDetector(PoisonDetector):
    """OSS default poison/injection detector (rules + heuristics, offline).

    Args:
        model_id: stable model identifier (default ``"poison/rules"``).
        version: semver string for the model (default ``"1.0.0"``).
        downgrade_to_status: status assigned to a poisoned record by
            :meth:`inspect` (default :attr:`MemoryStatus.DISPUTED` -- route to
            review). Set to ``None`` to leave the status untouched.

    The detector is deterministic for fixed input and a fixed model version, and
    its output is always well-formed: ``severity`` is clamped to ``[0, 1]``,
    ``categories`` is a list, and ``reason`` is a non-empty string.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        version: str = DEFAULT_VERSION,
        downgrade_to_status: MemoryStatus | None = MemoryStatus.DISPUTED,
    ) -> None:
        self._model_id = model_id
        self._version = version
        self._downgrade_to_status = downgrade_to_status

    # -- MemoryGuardModel / PoisonDetector interface -----------------------

    @property
    def info(self) -> ModelInfo:
        """Stable identity + version for reproducibility (``task="poison"``)."""
        return ModelInfo(model_id=self._model_id, task="poison", version=self._version)

    def inspect_content(self, record: MemoryRecord) -> PoisonResult:
        """Inspect ``record.content`` and return a bounded :class:`PoisonResult`.

        Content is treated strictly as data: each rule's pattern is *matched*
        against the text; nothing is executed. ``is_poisoned`` is ``True`` when
        any known-injection pattern matches.
        """
        text = record.content if isinstance(record.content, str) else str(record.content)

        matched: dict[str, float] = {}
        for rule in _RULES:
            if rule.pattern.search(text):
                # Keep the highest severity seen per category (deterministic).
                prev = matched.get(rule.category, 0.0)
                if rule.severity > prev:
                    matched[rule.category] = rule.severity

        # Obfuscation: a long base64-ish blob anywhere in the content.
        for token in _BASE64_RE.findall(text):
            if _looks_base64(token):
                matched["obfuscation"] = max(matched.get("obfuscation", 0.0), 0.55)
                break

        if not matched:
            return PoisonResult(
                is_poisoned=False,
                categories=[],
                severity=0.0,
                reason="no known injection or poisoning patterns detected",
            )

        categories = [c for c in _CATEGORY_ORDER if c in matched]
        severity = self._aggregate_severity(matched)
        reason = self._build_reason(categories, severity)
        return PoisonResult(
            is_poisoned=True,
            categories=categories,
            severity=severity,
            reason=reason,
        )

    # -- IngestionInspector contract --------------------------------------

    def inspect(self, record: MemoryRecord) -> MemoryRecord:
        """Run at ingestion: flag, downgrade trust, and route to review.

        Implements the core ``IngestionInspector`` contract. When the content is
        poisoned this:

        * multiplies ``trust_score`` by ``(1 - severity)`` (more-severe content
          loses more trust),
        * sets ``status`` to ``DISPUTED`` (route to review) unless configured
          otherwise, and
        * records the full :class:`PoisonResult` under ``metadata['poison']``.

        The (possibly mutated) record is returned. Content is never executed.
        """
        result = self.inspect_content(record)
        if not result.is_poisoned:
            return record

        # Downgrade trust proportionally to severity.
        record.trust_score = clamp_trust_score(record.trust_score * (1.0 - result.severity))

        # Route to review.
        if self._downgrade_to_status is not None:
            record.status = self._downgrade_to_status

        # Annotate metadata with the result as plain data (never executed).
        if not isinstance(record.metadata, dict):
            record.metadata = {}
        record.metadata["poison"] = asdict(result)
        return record

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _aggregate_severity(matched: dict[str, float]) -> float:
        """Combine per-category severities into a single bounded magnitude.

        Uses the max matched severity as the floor and nudges it up slightly for
        each additional distinct category, so content tripping several signals
        scores higher. Always clamped to ``[0, 1]``.
        """
        if not matched:
            return 0.0
        base = max(matched.values())
        extra = 0.03 * (len(matched) - 1)
        return clamp_trust_score(base + extra)

    @staticmethod
    def _build_reason(categories: list[str], severity: float) -> str:
        """Compose a short, non-empty explanation (no raw content echoed)."""
        label = ", ".join(categories)
        return f"matched poison signals [{label}] (severity {severity:.2f})"


# Register as a virtual subclass of the core IngestionInspector when available,
# so isinstance checks pass without a hard import dependency.
if _IngestionInspector is not None:  # pragma: no cover - depends on task 10.1
    try:
        _IngestionInspector.register(BasicPoisonDetector)
    except Exception:  # noqa: BLE001 - registration is best-effort only
        pass


__all__ = [
    "BasicPoisonDetector",
    "DEFAULT_MODEL_ID",
    "DEFAULT_VERSION",
]
