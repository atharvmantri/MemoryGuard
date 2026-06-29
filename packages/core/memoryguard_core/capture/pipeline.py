# SPDX-License-Identifier: Apache-2.0
"""Deterministic transcript extraction for the Agent Capture MVP."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from memoryguard_core.capture.models import CaptureCandidate
from memoryguard_core.decisions import Decision, canonical_decision_label, extract_decision
from memoryguard_core.models import Sensitivity
from memoryguard_core.secrets import contains_secret, redact_text

__all__ = ["extract_candidates"]

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_NOISE_RE = re.compile(
    r"\b(maybe|scratch|temporary|for now only|try this|experiment|"
    r"not sure|could use|let's test|failed approach|did not work)\b",
    re.I,
)
_CODING_RULE_RE = re.compile(
    r"\b(do not|don't|never|must not|avoid|prefer|always|must|should)\b",
    re.I,
)
_FROM_TO_RE = re.compile(
    r"\bmoved\s+from\s+([A-Za-z][A-Za-z0-9_.+-]*)\s+to\s+([A-Za-z][A-Za-z0-9_.+-]*)\b",
    re.I,
)
_INSTEAD_RE = re.compile(
    r"\b(?:use|uses|using)\s+([A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z][A-Za-z0-9_.+-]*)?)"
    r"\s+instead\s+of\s+([A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z][A-Za-z0-9_.+-]*)?)\b",
    re.I,
)
_REPLACED_BY_RE = re.compile(
    r"\b(?:replaced|replace)\s+([A-Za-z][A-Za-z0-9_.+-]*)\s+with\s+"
    r"([A-Za-z][A-Za-z0-9_.+-]*)\b",
    re.I,
)
_LOCAL_CLOUD_DB_RE = re.compile(
    r"\blocal\s+dev\s+uses\s+([A-Za-z][A-Za-z0-9_.+-]*)\s+but\s+"
    r"production\s+uses\s+([A-Za-z][A-Za-z0-9_.+-]*)\b",
    re.I,
)
_DEPLOY_THIS_RE = re.compile(r"\bdeploy\s+this\s+on\s+([A-Za-z][A-Za-z0-9_.+-]*)\b", re.I)
_PREVIOUS_DEAD_RE = re.compile(
    r"\b(?:previous|old)\s+([A-Za-z][A-Za-z0-9_.+-]*)\s+plan\s+is\s+(?:dead|outdated|deprecated)\b",
    re.I,
)


def extract_candidates(
    text: str,
    *,
    source_type: str,
    source_ref: str,
) -> list[CaptureCandidate]:
    """Extract pending memory candidates from plain transcript text.

    The MVP is deliberately rules-first and conservative. It stores only
    redacted excerpts/candidates, never raw full chats.
    """

    candidates: list[CaptureCandidate] = []
    for chunk, start, end in _chunks(text):
        redacted = redact_text(chunk)
        ref = _with_lines(source_ref, start, end)
        if contains_secret(chunk):
            candidates.append(
                CaptureCandidate.new(
                    content="Sensitive value omitted from captured transcript.",
                    canonical_content="Sensitive value omitted from captured transcript.",
                    source_type=source_type,
                    source_ref=ref,
                    evidence=redacted,
                    confidence=0.0,
                    sensitivity=Sensitivity.SECRET,
                    metadata={"capture_action": "omit_sensitive"},
                )
            )
            continue
        if _NOISE_RE.search(chunk):
            continue
        candidates.extend(_extract_chunk(chunk, redacted, source_type, ref))
    return _dedupe(candidates)


def _chunks(text: str) -> Iterable[tuple[str, int, int]]:
    for line_no, raw in enumerate(str(text).splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        # Drop common transcript speaker prefixes without losing line provenance.
        line = re.sub(r"^(user|assistant|codex|cursor|claude)\s*:\s*", "", line, flags=re.I)
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            cleaned = sentence.strip(" -\t")
            if len(cleaned) >= 8:
                yield cleaned, line_no, line_no


def _extract_chunk(
    chunk: str,
    evidence: str,
    source_type: str,
    source_ref: str,
) -> list[CaptureCandidate]:
    split_db = _LOCAL_CLOUD_DB_RE.search(chunk)
    if split_db:
        return [
            _decision_candidate(
                "database_local",
                split_db.group(1),
                chunk,
                evidence,
                source_type,
                source_ref,
                confidence=0.90,
            ),
            _decision_candidate(
                "database_cloud",
                split_db.group(2),
                chunk,
                evidence,
                source_type,
                source_ref,
                confidence=0.90,
            ),
        ]

    deploy_this = _DEPLOY_THIS_RE.search(chunk)
    if deploy_this:
        return [
            _decision_candidate(
                "deployment_target",
                deploy_this.group(1),
                chunk,
                evidence,
                source_type,
                source_ref,
                confidence=0.82,
            )
        ]

    dead = _PREVIOUS_DEAD_RE.search(chunk)
    if dead:
        value = _clean_value(dead.group(1))
        return [
            CaptureCandidate.new(
                content=f"{value} is deprecated/outdated.",
                canonical_content=f"{value} is deprecated/outdated.",
                decision_key="deprecated_value",
                value=value,
                source_type=source_type,
                source_ref=source_ref,
                evidence=evidence,
                confidence=0.78,
                sensitivity=Sensitivity.INTERNAL,
                metadata={"capture_action": "mark_outdated", "deprecated_value": value},
            )
        ]

    decision = extract_decision(_normalize_for_decisions(chunk))
    if decision is not None:
        supersedes = _supersedes_value(chunk)
        return [
            _candidate_from_decision(
                decision,
                chunk,
                evidence,
                source_type,
                source_ref,
                supersedes_value=supersedes,
            )
        ]

    if _CODING_RULE_RE.search(chunk) and _looks_durable_rule(chunk):
        canonical = redact_text(chunk).rstrip(".") + "."
        return [
            CaptureCandidate.new(
                content=canonical,
                canonical_content=canonical,
                decision_key="coding_rule",
                source_type=source_type,
                source_ref=source_ref,
                evidence=evidence,
                confidence=0.74,
                sensitivity=Sensitivity.INTERNAL,
                metadata={"capture_action": "coding_rule"},
            )
        ]
    return []


def _normalize_for_decisions(text: str) -> str:
    moved = _FROM_TO_RE.search(text)
    if moved:
        return f"Backend framework is {moved.group(2)}."
    deployed = _DEPLOY_THIS_RE.search(text)
    if deployed:
        return f"Deployment target is {deployed.group(1)}."
    return text


def _candidate_from_decision(
    decision: Decision,
    chunk: str,
    evidence: str,
    source_type: str,
    source_ref: str,
    *,
    supersedes_value: Optional[str],
) -> CaptureCandidate:
    return _decision_candidate(
        decision.key,
        decision.value,
        chunk,
        evidence,
        source_type,
        source_ref,
        confidence=0.86 if decision.key != "replacement" else 0.70,
        supersedes_value=supersedes_value,
    )


def _decision_candidate(
    key: str,
    value: str,
    chunk: str,
    evidence: str,
    source_type: str,
    source_ref: str,
    *,
    confidence: float,
    supersedes_value: Optional[str] = None,
) -> CaptureCandidate:
    clean_value = _clean_value(value)
    label = canonical_decision_label(key)
    canonical = f"{label}: {clean_value}"
    content = _content_for_key(key, clean_value, supersedes_value)
    return CaptureCandidate.new(
        content=content,
        canonical_content=canonical,
        decision_key=key,
        value=clean_value,
        supersedes_value=_clean_value(supersedes_value) if supersedes_value else None,
        source_type=source_type,
        source_ref=source_ref,
        evidence=evidence,
        confidence=confidence,
        sensitivity=Sensitivity.INTERNAL,
        metadata={"capture_action": "memory", "raw_pattern": redact_text(chunk)},
    )


def _content_for_key(key: str, value: str, supersedes_value: Optional[str]) -> str:
    if key == "backend_framework":
        base = (
            f"The backend framework now uses {value} instead of {supersedes_value}."
            if supersedes_value
            else f"The backend framework is {value}."
        )
    elif key == "package_manager":
        base = f"The project now uses {value} instead of {supersedes_value}." if supersedes_value else f"Package manager is {value}."
    elif key == "database_local":
        base = f"Local database is {value}."
    elif key == "database_cloud":
        base = f"Cloud database is {value}."
    elif key == "frontend_framework":
        base = f"Frontend framework is {value}."
    elif key == "deployment_target":
        base = f"Deployment target is {value}."
    elif key == "test_command":
        base = f"The test command is {value}."
    else:
        base = f"{canonical_decision_label(key)} is {value}."
    return redact_text(base)


def _supersedes_value(text: str) -> Optional[str]:
    for regex, group in ((_FROM_TO_RE, 1), (_INSTEAD_RE, 2), (_REPLACED_BY_RE, 1)):
        match = regex.search(text)
        if match:
            return match.group(group)
    return None


def _looks_durable_rule(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in ("dependency", "dependencies", "api", "llm", "secret", "store", "commit", "agent")
    )


def _with_lines(source_ref: str, start: int, end: int) -> str:
    line_ref = f"L{start}" if start == end else f"L{start}-L{end}"
    if not source_ref:
        return line_ref
    return f"{source_ref}#{line_ref}"


def _clean_value(value: Optional[str]) -> str:
    return redact_text(value or "").strip(" .,:;\"'")


def _dedupe(candidates: list[CaptureCandidate]) -> list[CaptureCandidate]:
    seen: set[tuple[str, str, str]] = set()
    result: list[CaptureCandidate] = []
    for candidate in candidates:
        key = (
            candidate.decision_key or "",
            (candidate.value or candidate.content).lower(),
            Path(candidate.source_ref.split("#", 1)[0]).name.lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result
