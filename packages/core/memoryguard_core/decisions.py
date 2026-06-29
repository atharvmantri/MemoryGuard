# SPDX-License-Identifier: Apache-2.0
"""Deterministic extraction of simple project decisions from memory text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "Decision",
    "canonical_decision_label",
    "extract_decision",
    "infer_query_decision_keys",
    "has_supersession_cue",
]


@dataclass(frozen=True)
class Decision:
    """A simple key/value project fact extracted from memory text."""

    key: str
    value: str


_VALUE = r"([A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z][A-Za-z0-9_.+-]*)?)"
_PACKAGE_MANAGER_VALUE = r"(npm|pnpm|yarn|bun)"
_PACKAGE_MANAGER_RE = re.compile(
    rf"\b(?:this\s+project\s+|the\s+project\s+)?(?:now\s+)?"
    rf"(?:uses?|using|use)\s+{_PACKAGE_MANAGER_VALUE}\b",
    re.I,
)
_PACKAGE_MANAGER_INSTEAD_RE = re.compile(
    rf"\b(?:this\s+project\s+|the\s+project\s+)?(?:now\s+)?"
    rf"(?:uses?|using|use)\s+{_PACKAGE_MANAGER_VALUE}\s*,?\s+"
    rf"(?:not|instead\s+of)\s+{_PACKAGE_MANAGER_VALUE}\b",
    re.I,
)
_PACKAGE_MANAGER_REPLACE_RE = re.compile(
    rf"\breplace\s+{_PACKAGE_MANAGER_VALUE}\s+with\s+{_PACKAGE_MANAGER_VALUE}\b",
    re.I,
)
_TEST_COMMAND_VALUE = (
    r"(vitest\s+run|uv\s+run\s+pytest|pytest|pnpm\s+test|npm\s+test|"
    r"yarn\s+test|bun\s+test|ruff\s+check\s+\.)"
)
_TEST_COMMAND_RE = re.compile(
    rf"\b(?:the\s+)?test\s+command\s+(?:is\s+)?{_TEST_COMMAND_VALUE}\b"
    rf"|\b(?:run\s+tests?\s+with|tests?\s+run\s+with|use)\s+{_TEST_COMMAND_VALUE}"
    rf"(?:\s+for\s+tests?)?\b",
    re.I,
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "backend_framework",
        re.compile(
            rf"\b(?:backend|back\s*end)(?:\s+\w+){{0,4}}\s+"
            rf"(?:uses?|using|is|has\s+(?:now\s+)?been\s+using)\s+{_VALUE}\b",
            re.I,
        ),
    ),
    (
        "backend_framework",
        re.compile(rf"\b(?:uses?|using)\s+{_VALUE}\s+for\s+(?:the\s+)?backend\b", re.I),
    ),
    (
        "package_manager",
        re.compile(rf"\b(?:use|uses|using)\s+{_VALUE}\s+(?:not|instead\s+of)\s+\w+\b", re.I),
    ),
    (
        "package_manager",
        re.compile(rf"\b(?:package\s+manager|pm)\s+(?:is|uses?|using)\s+{_VALUE}\b", re.I),
    ),
    (
        "frontend_framework",
        re.compile(
            rf"\b(?:frontend|front\s*end|ui)(?:\s+\w+){{0,4}}\s+"
            rf"(?:uses?|using|is|has\s+(?:now\s+)?been\s+using)\s+{_VALUE}\b",
            re.I,
        ),
    ),
    (
        "frontend_framework",
        re.compile(rf"\b(?:uses?|using)\s+{_VALUE}\s+for\s+(?:the\s+)?frontend\b", re.I),
    ),
    (
        "database_local",
        re.compile(rf"\blocal\s+database\s+(?:is|uses?|using)\s+{_VALUE}\b", re.I),
    ),
    (
        "database_cloud",
        re.compile(rf"\bcloud\s+database\s+(?:is|uses?|using)\s+{_VALUE}\b", re.I),
    ),
    (
        "database",
        re.compile(rf"\bdatabase\s+(?:is|uses?|using)\s+{_VALUE}\b", re.I),
    ),
    (
        "database",
        re.compile(rf"\b(?:uses?|using)\s+{_VALUE}\s+for\s+(?:the\s+)?database\b", re.I),
    ),
    (
        "database",
        re.compile(rf"\b(?:uses?|using)\s+{_VALUE}\s+as\s+(?:the\s+)?database\b", re.I),
    ),
    (
        "deployment_target",
        re.compile(
            rf"\b(?:deploy(?:ment)?\s+(?:target|platform)|deploys?\s+to|hosted\s+on)"
            rf"\s+(?:is\s+|uses?\s+|using\s+)?{_VALUE}\b",
            re.I,
        ),
    ),
    (
        "test_command",
        re.compile(
            rf"\b(?:the\s+)?test\s+command\s+(?:is\s+)?{_TEST_COMMAND_VALUE}\b",
            re.I,
        ),
    ),
    (
        "test_command",
        re.compile(
            rf"\b(?:run\s+tests?\s+with|tests?\s+run\s+with|use)\s+{_TEST_COMMAND_VALUE}"
            rf"(?:\s+for\s+tests?)?\b",
            re.I,
        ),
    ),
)

_LABELS = {
    "backend_framework": "Backend framework",
    "package_manager": "Package manager",
    "database_local": "Local database",
    "database_cloud": "Cloud database",
    "database": "Database",
    "frontend_framework": "Frontend framework",
    "deployment_target": "Deployment target",
    "test_command": "Test command",
    "replacement": "Replacement",
}

_REPLACE_RE = re.compile(
    rf"\breplace\s+{_VALUE}\s+with\s+{_VALUE}\b|\bno\s+longer\s+use\s+{_VALUE}\b",
    re.I,
)
_SUPERSESSION_RE = re.compile(
    r"\b(now|from\s+now\s+on|has\s+now\s+been|now\s+using|replace|replaced|"
    r"no\s+longer|instead\s+of|supersedes?)\b",
    re.I,
)
_TRAILING_STOP = re.compile(
    r"\s+(?:for|as|because|since|instead|not|now|currently|in|on|with)\b.*$",
    re.I,
)


def extract_decision(text: str) -> Optional[Decision]:
    """Extract a known project decision from ``text`` when it is obvious."""

    cleaned = " ".join(str(text).strip().split())
    if not cleaned:
        return None
    test_command = _TEST_COMMAND_RE.search(cleaned)
    if test_command:
        value = next(group for group in test_command.groups() if group)
        return Decision(key="test_command", value=_normalize_value(value))

    pm_replace = _PACKAGE_MANAGER_REPLACE_RE.search(cleaned)
    if pm_replace:
        return Decision(key="package_manager", value=_normalize_value(pm_replace.group(2)))
    pm_instead = _PACKAGE_MANAGER_INSTEAD_RE.search(cleaned)
    if pm_instead:
        return Decision(key="package_manager", value=_normalize_value(pm_instead.group(1)))
    pm = _PACKAGE_MANAGER_RE.search(cleaned)
    if pm:
        return Decision(key="package_manager", value=_normalize_value(pm.group(1)))

    replacement = _REPLACE_RE.search(cleaned)
    if replacement and replacement.lastindex and replacement.lastindex >= 2:
        value = _normalize_value(replacement.group(2))
        if value:
            return Decision(key="replacement", value=value)

    for key, pattern in _PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        value = _normalize_value(match.group(1))
        if value:
            return Decision(key=key, value=value)
    return None


def has_supersession_cue(text: str) -> bool:
    """Whether ``text`` explicitly says a decision is replacing prior guidance."""

    return bool(_SUPERSESSION_RE.search(str(text)))


def canonical_decision_label(key: str) -> str:
    """Return a human-readable label for a decision key."""

    return _LABELS.get(key, key.replace("_", " ").capitalize())


def infer_query_decision_keys(query: str) -> frozenset[str]:
    """Infer the structured decision key(s) a query is asking about."""

    text = str(query).lower()
    keys: set[str] = set()
    if "backend" in text or "back end" in text or "api framework" in text:
        keys.add("backend_framework")
    if "package manager" in text or "npm" in text or "pnpm" in text or "yarn" in text or "bun" in text:
        keys.add("package_manager")
    if "database" in text or "datastore" in text or "db" in text:
        keys.update({"database", "database_local", "database_cloud"})
    if "deploy" in text or "deployed" in text or "hosted" in text or "hosting" in text:
        keys.add("deployment_target")
    if "test" in text or "tests" in text or "testing" in text:
        keys.add("test_command")
    if "frontend" in text or "front end" in text or "ui framework" in text:
        keys.add("frontend_framework")
    return frozenset(keys)


def _normalize_value(value: str) -> str:
    value = _TRAILING_STOP.sub("", value)
    value = value.strip(" .,:;\"'")
    if re.match(r"^(uv|pnpm|npm|yarn|bun|pytest|ruff|vitest)\b", value, re.I):
        return value
    words = value.split()
    if len(words) > 2:
        words = words[:2]
    return " ".join(words)
