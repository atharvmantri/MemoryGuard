# SPDX-License-Identifier: Apache-2.0
"""Tests for deterministic structured decision extraction."""

from __future__ import annotations

import pytest

from memoryguard_core.decisions import extract_decision


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("This project uses npm.", "npm"),
        ("The project uses npm.", "npm"),
        ("Use npm.", "npm"),
        ("This project uses pnpm.", "pnpm"),
        ("The project now uses pnpm instead of npm.", "pnpm"),
        ("Use pnpm, not npm.", "pnpm"),
        ("Replace npm with pnpm.", "pnpm"),
        ("Use yarn.", "yarn"),
        ("The project uses bun.", "bun"),
    ],
)
def test_extracts_package_manager_variants(text: str, value: str):
    decision = extract_decision(text)

    assert decision is not None
    assert decision.key == "package_manager"
    assert decision.value == value


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("The test command is pnpm test.", "pnpm test"),
        ("Run tests with pnpm test.", "pnpm test"),
        ("Use pnpm test for tests.", "pnpm test"),
        ("The test command is npm test.", "npm test"),
        ("Tests run with vitest run.", "vitest run"),
    ],
)
def test_extracts_test_command_variants(text: str, value: str):
    decision = extract_decision(text)

    assert decision is not None
    assert decision.key == "test_command"
    assert decision.value == value
