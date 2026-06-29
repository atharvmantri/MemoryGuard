# SPDX-License-Identifier: Apache-2.0
"""Deterministic, on-device default sensitive-data detector (OSS).

``BasicSensitiveDataDetector`` is the OSS default behind the
:class:`~memoryguard_models.base.SensitiveDataDetector` interface. It uses a
fixed catalogue of regular expressions and lightweight rules to detect secrets,
API keys/tokens, passwords, private keys, PII (emails, SSNs, credit-card
numbers), and internal company information (confidentiality/classification
markers and internal corporate hostnames) in a memory's ``content``. It runs
**entirely locally** with no external LLM API (Requirement 25.5) and is
deterministic for identical input.

It also implements the core ``IngestionInspector`` duck-type
(``inspect(record) -> MemoryRecord``): when sensitive content is detected it
elevates the record's ``sensitivity`` to the suggested tier (never *downgrading*
below the record's current tier) and annotates ``metadata['sensitive']`` with
the detection result serialized as a dict, so ingestion can flag/route the
record.

Detected categories and their suggested sensitivity tier:

* ``Sensitivity.SECRET`` -- AWS access keys (``AKIA...``), AWS secret access
  keys, generic API keys/tokens (GitHub ``ghp_...`` tokens, Slack incoming
  webhooks, ``Bearer`` tokens), passwords (``password=``, ``DB_PASSWORD=``),
  PEM private keys (``-----BEGIN ... PRIVATE KEY-----``), and internal company
  information (confidentiality/classification markers such as
  ``CONFIDENTIAL`` / ``INTERNAL USE ONLY`` / ``DO NOT DISTRIBUTE`` and internal
  corporate hostnames such as ``wiki.acme.internal`` / ``host.corp``).
* ``Sensitivity.PII`` -- email addresses, US SSNs (``###-##-####``), and
  credit-card numbers (4-group 16-digit, Luhn-validated when possible).

When both secret-class and PII-class content are present, the suggested tier is
``SECRET`` (the more operationally critical exposure). Internal company
information is treated as secret-class, so a flagged record always receives an
elevated tier of ``SECRET`` or ``PII`` (Requirement 25.2).

Requirements: 25.1 (detect secrets/keys/passwords/PII via rules), 25.2 (suggest
elevated sensitivity tier), 25.4 (implement ``IngestionInspector`` and elevate
the record's tier without downgrading), 25.5 (run locally, no external API).
"""

from __future__ import annotations

import re
from typing import Pattern

from memoryguard_core.models import MemoryRecord, Sensitivity
from memoryguard_models.base import ModelInfo, SensitiveDataDetector, SensitiveResult

# ---------------------------------------------------------------------------
# Optional core IngestionInspector contract (task 10.1). Imported defensively so
# this module never hard-fails if the interface location changes, and registered
# as a virtual subclass when present so ``isinstance(detector, IngestionInspector)``
# holds without creating a hard dependency from the model layer on a specific
# core import path.
# ---------------------------------------------------------------------------
_IngestionInspector = None
for _candidate in (
    "memoryguard_core.retrieval.policy_filter",
    "memoryguard_core.ingestion.inspectors",
    "memoryguard_core.audit.hooks",
):
    try:  # pragma: no cover - import resolution depends on task timing
        _mod = __import__(_candidate, fromlist=["IngestionInspector"])
        _IngestionInspector = getattr(_mod, "IngestionInspector", None)
        if _IngestionInspector is not None:
            break
    except Exception:  # noqa: BLE001 - any import failure -> stay duck-typed
        _IngestionInspector = None

# ---------------------------------------------------------------------------
# Module-level identity
# ---------------------------------------------------------------------------

#: Stable model id for the OSS rules-based sensitive-data detector.
DEFAULT_MODEL_ID = "sensitive/rules"

#: Semver for the OSS rules-based sensitive-data detector.
DEFAULT_VERSION = "1.0.0"

#: Detection task name (matches ``ModelInfo.task`` vocabulary in ``base.py``).
DEFAULT_TASK = "sensitive"


# ---------------------------------------------------------------------------
# Sensitivity tier ranking (for "never downgrade" elevation)
# ---------------------------------------------------------------------------

#: Restrictiveness ranking used to elevate-without-downgrading. ``PUBLIC`` is the
#: least restrictive; ``SECRET``/``PII`` are the most restrictive elevated tiers.
_TIER_RANK: dict[Sensitivity, int] = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.SECRET: 2,
    Sensitivity.PII: 3,
}


def _max_tier(current: Sensitivity, suggested: Sensitivity) -> Sensitivity:
    """Return the more restrictive of ``current`` and ``suggested``.

    Guarantees the elevation never *downgrades* a record below its existing
    sensitivity tier.
    """

    return suggested if _TIER_RANK[suggested] > _TIER_RANK[current] else current


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

# Each rule is (name, compiled_pattern, tier). ``name`` is the label surfaced in
# ``SensitiveResult.detected``; ``tier`` is the sensitivity this match implies.
_SECRET = Sensitivity.SECRET
_PII = Sensitivity.PII


def _luhn_ok(digits: str) -> bool:
    """Return ``True`` when ``digits`` passes the Luhn checksum."""

    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13:
        return False
    total = 0
    parity = len(nums) % 2
    for idx, num in enumerate(nums):
        if idx % 2 == parity:
            num *= 2
            if num > 9:
                num -= 9
        total += num
    return total % 10 == 0


# Ordered so that the most specific / highest-signal patterns appear first; all
# matching rules contribute to ``detected``.
_RULES: list[tuple[str, Pattern[str], Sensitivity]] = [
    # --- Secrets / keys / tokens -------------------------------------------
    # AWS access key id, e.g. AKIAIOSFODNN7EXAMPLE
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _SECRET),
    # AWS secret access key: explicit key name (strong signal).
    (
        "aws_secret_access_key",
        re.compile(r"(?i)aws_secret_access_key"),
        _SECRET,
    ),
    # PEM private key blocks of any type (RSA / EC / OPENSSH / generic).
    (
        "private_key",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
        _SECRET,
    ),
    # GitHub personal-access / app tokens (ghp_, gho_, ghu_, ghs_, ghr_).
    (
        "github_token",
        re.compile(r"\bgh[poursa]_[A-Za-z0-9]{20,}\b"),
        _SECRET,
    ),
    # Slack incoming webhook URLs.
    (
        "slack_webhook",
        re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"),
        _SECRET,
    ),
    # HTTP Bearer tokens (Authorization: Bearer <token>).
    (
        "bearer_token",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
        _SECRET,
    ),
    # Passwords assigned inline: password=..., DB_PASSWORD=..., pwd: ...
    (
        "password",
        re.compile(r"(?i)\b[a-z0-9_]*(?:password|passwd|pwd)\b\s*[=:]\s*\S+"),
        _SECRET,
    ),
    # --- Internal company information (treated as secret-class) ------------
    # Explicit confidentiality / classification markers (high signal).
    (
        "internal_info",
        re.compile(r"(?i)\b(?:company[\s-]+|strictly[\s-]+)?confidential\b"),
        _SECRET,
    ),
    # "internal use only", "for internal use only", "internal only".
    (
        "internal_info",
        re.compile(r"(?i)\b(?:for\s+)?internal[\s-]+(?:use[\s-]+)?only\b"),
        _SECRET,
    ),
    # "proprietary and confidential" / "proprietary information".
    (
        "internal_info",
        re.compile(r"(?i)\bproprietary\s+(?:and\s+confidential|information)\b"),
        _SECRET,
    ),
    # "trade secret(s)".
    (
        "internal_info",
        re.compile(r"(?i)\btrade\s+secrets?\b"),
        _SECRET,
    ),
    # Distribution-restriction markers: "do not distribute/share/forward".
    (
        "internal_info",
        re.compile(r"(?i)\bdo\s+not\s+(?:distribute|share|forward)\b"),
        _SECRET,
    ),
    # Internal corporate hostnames, e.g. wiki.acme.internal, host.corp,
    # portal.intranet (the trailing label is the strong, specific signal).
    (
        "internal_info",
        re.compile(
            r"\b[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?"
            r"\.(?:internal|corp|intranet)\b"
        ),
        _SECRET,
    ),
    # Generic high-entropy AWS-style 40-char secret (guarded: must contain a
    # digit or +/ so ordinary 40-letter prose does not trip it).
    (
        "generic_secret",
        re.compile(
            r"(?<![A-Za-z0-9/+])(?=[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+]))"
            r"[A-Za-z0-9/+]*[0-9/+][A-Za-z0-9/+]*"
        ),
        _SECRET,
    ),
    # --- PII ----------------------------------------------------------------
    # Email addresses.
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        _PII,
    ),
    # US Social Security Numbers: ###-##-####.
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), _PII),
]

#: 4-group, 16-digit credit-card candidate (Luhn-checked separately).
_CREDIT_CARD = re.compile(r"\b(?:\d[ -]?){15}\d\b")


class BasicSensitiveDataDetector(SensitiveDataDetector):
    """OSS regex/rules sensitive-data detector + ``IngestionInspector``.

    Deterministic and offline. Detects secrets/keys/passwords/private-keys
    (``SECRET``) and emails/SSNs/credit-cards (``PII``), suggests an elevated
    sensitivity tier, and can elevate a record's tier at ingestion without ever
    downgrading it.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        version: str = DEFAULT_VERSION,
    ) -> None:
        self._info = ModelInfo(model_id=model_id, task=DEFAULT_TASK, version=version)

    # -- identity -----------------------------------------------------------

    @property
    def info(self) -> ModelInfo:
        """Stable identity + version for reproducibility."""

        return self._info

    # -- detection ----------------------------------------------------------

    def inspect_content(self, record: MemoryRecord) -> SensitiveResult:
        """Inspect ``record.content`` and return a :class:`SensitiveResult`."""

        content = record.content if isinstance(record.content, str) else ""
        detected: list[str] = []
        has_secret = False

        for name, pattern, tier in _RULES:
            if pattern.search(content):
                detected.append(name)
                # Only secret-tier detections affect the suggested tier; PII is
                # captured via ``detected`` and the default PII suggestion below.
                if tier is _SECRET:
                    has_secret = True

        # Credit-card numbers: 4-group 16-digit, validated with Luhn when the
        # checksum is computable (keeps false positives low on arbitrary digits).
        for match in _CREDIT_CARD.finditer(content):
            digits = re.sub(r"[ -]", "", match.group(0))
            if len(digits) == 16 and _luhn_ok(digits):
                detected.append("credit_card")
                break

        # De-duplicate while preserving first-seen order.
        seen: set[str] = set()
        deduped = [d for d in detected if not (d in seen or seen.add(d))]

        has_sensitive = bool(deduped)
        if not has_sensitive:
            return SensitiveResult(
                has_sensitive=False,
                detected=[],
                suggested_sensitivity=record.sensitivity,
                reason="No sensitive patterns detected.",
            )

        # Secrets dominate PII when both are present.
        suggested = Sensitivity.SECRET if has_secret else Sensitivity.PII
        reason = (
            f"Detected {len(deduped)} sensitive pattern(s): "
            f"{', '.join(deduped)} -> suggested {suggested.value}."
        )
        return SensitiveResult(
            has_sensitive=True,
            detected=deduped,
            suggested_sensitivity=suggested,
            reason=reason,
        )

    # -- IngestionInspector (duck-typed: inspect(record) -> record) ---------

    def inspect(self, record: MemoryRecord) -> MemoryRecord:
        """Elevate ``record.sensitivity`` and annotate metadata when sensitive.

        Implements the core ``IngestionInspector`` contract. When sensitive
        content is found, the record's ``sensitivity`` is raised to the more
        restrictive of its current tier and the suggested tier (never a
        downgrade), and the full result is stored under
        ``record.metadata['sensitive']`` as a serializable dict. The (possibly
        mutated) record is returned.
        """

        result = self.inspect_content(record)
        if not result.has_sensitive:
            return record

        record.sensitivity = _max_tier(record.sensitivity, result.suggested_sensitivity)
        if not isinstance(record.metadata, dict):
            record.metadata = {}
        record.metadata["sensitive"] = _result_as_dict(result)
        return record


def _result_as_dict(result: SensitiveResult) -> dict:
    """Serialize a :class:`SensitiveResult` to a JSON-friendly dict."""

    return {
        "has_sensitive": result.has_sensitive,
        "detected": list(result.detected),
        "suggested_sensitivity": result.suggested_sensitivity.value,
        "reason": result.reason,
    }


# Register as a virtual subclass of the core IngestionInspector when available,
# so isinstance checks pass without a hard import dependency.
if _IngestionInspector is not None:  # pragma: no cover - depends on core import
    try:
        _IngestionInspector.register(BasicSensitiveDataDetector)
    except Exception:  # noqa: BLE001 - registration is best-effort only
        pass


__all__ = [
    "BasicSensitiveDataDetector",
    "DEFAULT_MODEL_ID",
    "DEFAULT_VERSION",
    "DEFAULT_TASK",
]
