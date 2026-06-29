# SPDX-License-Identifier: Apache-2.0
"""Tests for memoryguard_core.flags.FeatureFlags."""

from __future__ import annotations

import dataclasses

from hypothesis import given
from hypothesis import strategies as st

from memoryguard_core import FeatureFlags as ExportedFeatureFlags
from memoryguard_core.flags import FeatureFlags

OSS_TRUE_FLAGS = [
    "local_store",
    "hybrid_retrieval",
    "basic_trust",
    "basic_contradiction",
    "mcp_server",
    "local_dashboard",
    "local_embedder",
    "heuristic_reranker",
    "rule_contradiction",
    "basic_poison_detector",
    "basic_sensitive_detector",
    "deterministic_trust",
]

COMMERCIAL_FALSE_FLAGS = [
    "cloud_store",
    "cloud_auth",
    "workspaces",
    "teams",
    "audit_log",
    "policy_engine",
    "review_queue",
    "advanced_trust",
    "poisoning_detection",
    "pii_detection",
    "billing",
    "admin_console",
    "learned_reranker",
    "learned_contradiction",
    "advanced_poison_detection",
    "advanced_pii_model",
    "learned_trust_model",
    "hosted_inference",
    "model_serving",
    "model_analytics",
]


def test_export_is_same_class():
    assert ExportedFeatureFlags is FeatureFlags


def test_dataclass_is_frozen():
    flags = FeatureFlags()
    assert dataclasses.is_dataclass(flags)
    params = flags.__dataclass_params__
    assert params.frozen is True


def test_frozen_instance_rejects_mutation():
    flags = FeatureFlags()
    try:
        flags.cloud_store = True  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("FeatureFlags should be immutable")


def test_oss_flags_default_true():
    flags = FeatureFlags()
    for name in OSS_TRUE_FLAGS:
        assert getattr(flags, name) is True, f"{name} should default True"


def test_commercial_flags_default_false():
    flags = FeatureFlags()
    for name in COMMERCIAL_FALSE_FLAGS:
        assert getattr(flags, name) is False, f"{name} should default False"


def test_all_fields_are_bool():
    flags = FeatureFlags()
    for f in dataclasses.fields(FeatureFlags):
        assert isinstance(getattr(flags, f.name), bool)


def test_from_env_empty_matches_defaults():
    assert FeatureFlags.from_env({}) == FeatureFlags()


def test_from_env_enables_commercial_flag():
    flags = FeatureFlags.from_env({"MEMORYGUARD_CLOUD_STORE": "true"})
    assert flags.cloud_store is True
    # Other defaults untouched.
    assert flags.local_store is True
    assert flags.billing is False


def test_from_env_disables_oss_flag():
    flags = FeatureFlags.from_env({"MEMORYGUARD_LOCAL_STORE": "false"})
    assert flags.local_store is False


def test_from_env_various_truthy_tokens():
    for token in ["1", "true", "TRUE", "Yes", "on", " t "]:
        flags = FeatureFlags.from_env({"MEMORYGUARD_BILLING": token})
        assert flags.billing is True, f"token {token!r} should enable flag"


def test_from_env_various_falsey_tokens():
    for token in ["0", "false", "No", "off", ""]:
        flags = FeatureFlags.from_env({"MEMORYGUARD_LOCAL_STORE": token})
        assert flags.local_store is False, f"token {token!r} should disable flag"


def test_from_env_unrecognized_value_falls_back_to_default():
    flags = FeatureFlags.from_env({"MEMORYGUARD_CLOUD_STORE": "banana"})
    assert flags.cloud_store is False  # default preserved
    flags2 = FeatureFlags.from_env({"MEMORYGUARD_LOCAL_STORE": "banana"})
    assert flags2.local_store is True  # default preserved


def test_from_env_ignores_unknown_env_vars():
    flags = FeatureFlags.from_env({"MEMORYGUARD_NOT_A_FLAG": "true", "PATH": "/usr/bin"})
    assert flags == FeatureFlags()


def test_as_dict_returns_all_flags():
    flags = FeatureFlags()
    d = flags.as_dict()
    assert isinstance(d, dict)
    field_names = {f.name for f in dataclasses.fields(FeatureFlags)}
    assert set(d) == field_names
    for name in field_names:
        assert d[name] is getattr(flags, name)
        assert isinstance(d[name], bool)


def test_as_dict_reflects_overrides():
    flags = FeatureFlags.from_env({"MEMORYGUARD_CLOUD_STORE": "true"})
    d = flags.as_dict()
    assert d["cloud_store"] is True
    assert d["local_store"] is True
    assert d["billing"] is False


def test_as_dict_is_fresh_copy():
    flags = FeatureFlags()
    d = flags.as_dict()
    d["cloud_store"] = True  # mutating the dict must not affect the instance
    assert flags.cloud_store is False


_FIELD_NAMES = [f.name for f in dataclasses.fields(FeatureFlags)]


@given(
    field=st.sampled_from(_FIELD_NAMES),
    value=st.booleans(),
)
def test_property_from_env_roundtrips_any_single_flag(field: str, value: bool):
    """from_env applies the MEMORYGUARD_<FIELD> override for every flag.

    Validates: Requirements 17.1, 17.2, 17.4
    """
    env = {"MEMORYGUARD_" + field.upper(): "true" if value else "false"}
    flags = FeatureFlags.from_env(env)
    assert getattr(flags, field) is value
    # Every other field stays at its declared default.
    defaults = FeatureFlags()
    for other in _FIELD_NAMES:
        if other != field:
            assert getattr(flags, other) is getattr(defaults, other)
