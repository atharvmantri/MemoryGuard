# SPDX-License-Identifier: Apache-2.0
"""Tests for the core injection interfaces and OSS defaults.

Covers (Task 10.1, Requirements 16.4, 17.2, 17.3, 6.3, 6.5, 9.5):

* ``LocalJsonlAuditSink`` redaction — secret values and the raw content of
  secret/PII memories are never written to disk.
* ``AllowAllPolicy`` returns ``(True, [])`` (allow) for any record.
* ``NoOpInspector`` returns the record unchanged.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from memoryguard_core.audit import (
    AuditSink,
    LocalJsonlAuditSink,
    NullAuditSink,
    redact_event,
)
from memoryguard_core.audit.hooks import REDACTED
from memoryguard_core.models import (
    Scope,
    Sensitivity,
    SourceType,
    new_memory_record,
)
from memoryguard_core.retrieval import (
    AllowAllPolicy,
    IngestionInspector,
    NoOpInspector,
    PolicyProvider,
)


# ---------------------------------------------------------------------------
# AuditSink redaction
# ---------------------------------------------------------------------------

SECRET_VALUE = "super-secret-value-shh-12345"

SECRET_KEY_EVENTS = [
    {"password": SECRET_VALUE},
    {"api_key": SECRET_VALUE},
    {"apiKey": SECRET_VALUE},
    {"API-KEY": SECRET_VALUE},
    {"token": SECRET_VALUE},
    {"access_token": SECRET_VALUE},
    {"secret": SECRET_VALUE},
    {"client_secret": SECRET_VALUE},
    {"private_key": SECRET_VALUE},
    {"authorization": SECRET_VALUE},
    {"nested": {"db_password": SECRET_VALUE}},
    {"list": [{"auth_token": SECRET_VALUE}]},
]


def _written_text(sink: LocalJsonlAuditSink) -> str:
    return sink.path.read_text(encoding="utf-8")


def test_subclass_contract():
    assert issubclass(LocalJsonlAuditSink, AuditSink)
    assert issubclass(NullAuditSink, AuditSink)


@pytest.mark.parametrize("event", SECRET_KEY_EVENTS)
def test_secret_keys_are_redacted_on_disk(tmp_path, event):
    sink = LocalJsonlAuditSink(path=tmp_path / "audit.jsonl")
    sink.record(event)

    text = _written_text(sink)
    assert SECRET_VALUE not in text, f"secret value leaked for event {event!r}"
    assert REDACTED in text


def test_non_secret_values_are_preserved(tmp_path):
    sink = LocalJsonlAuditSink(path=tmp_path / "audit.jsonl")
    sink.record({"query_id": "q-123", "result_count": 4, "mode": "local"})

    events = list(sink.read_events())
    assert events == [{"query_id": "q-123", "result_count": 4, "mode": "local"}]


def test_secret_memory_content_not_logged(tmp_path):
    sink = LocalJsonlAuditSink(path=tmp_path / "audit.jsonl")
    record = new_memory_record(
        content="AWS_SECRET_ACCESS_KEY=" + SECRET_VALUE,
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
        sensitivity=Sensitivity.SECRET,
    )
    event = {
        "event": "ingest",
        "memory_id": record.memory_id,
        "content": record.content,
        "sensitivity": record.sensitivity,
        "reasons": ["detected secret"],
    }
    sink.record(event)

    text = _written_text(sink)
    assert SECRET_VALUE not in text
    assert record.content not in text
    # memory_id and reasons are retained for auditability.
    assert record.memory_id in text
    assert "detected secret" in text


def test_pii_memory_content_not_logged(tmp_path):
    sink = LocalJsonlAuditSink(path=tmp_path / "audit.jsonl")
    pii_content = "SSN 123-45-6789 for John Doe"
    event = {
        "event": "ingest",
        "memory": {
            "memory_id": "11111111-1111-4111-8111-111111111111",
            "content": pii_content,
            "sensitivity": Sensitivity.PII,
            "reasons": ["pii detected"],
        },
    }
    sink.record(event)

    text = _written_text(sink)
    assert pii_content not in text
    assert "123-45-6789" not in text
    assert "11111111-1111-4111-8111-111111111111" in text
    assert "pii detected" in text


def test_public_memory_content_is_logged(tmp_path):
    sink = LocalJsonlAuditSink(path=tmp_path / "audit.jsonl")
    event = {
        "event": "ingest",
        "memory_id": "22222222-2222-4222-8222-222222222222",
        "content": "The build uses pnpm workspaces.",
        "sensitivity": Sensitivity.PUBLIC,
    }
    sink.record(event)
    text = _written_text(sink)
    assert "pnpm workspaces" in text


def test_record_appends_one_line_per_event(tmp_path):
    sink = LocalJsonlAuditSink(path=tmp_path / "nested" / "dir" / "audit.jsonl")
    sink.record({"n": 1})
    sink.record({"n": 2})
    lines = [ln for ln in _written_text(sink).splitlines() if ln.strip()]
    assert len(lines) == 2
    assert [json.loads(ln)["n"] for ln in lines] == [1, 2]


def test_redact_event_does_not_mutate_original():
    original = {"password": SECRET_VALUE, "keep": "ok"}
    redacted = redact_event(original)
    assert original["password"] == SECRET_VALUE  # untouched
    assert redacted["password"] == REDACTED
    assert redacted["keep"] == "ok"


def test_null_sink_writes_nothing(tmp_path):
    sink = NullAuditSink()
    # Should not raise and should not create any file.
    sink.record({"password": SECRET_VALUE})


@given(
    secret=st.text(min_size=1, max_size=50),
    key=st.sampled_from(
        ["password", "api_key", "token", "secret", "access_key", "private_key"]
    ),
)
def test_property_secret_keys_never_written(tmp_path_factory, secret, key):
    """Any secret-named key's value is never persisted, for any value.

    Validates: Requirements 9.5, 16.4
    """
    # Redaction is key-based: the value of a secret-named key is replaced wholesale
    # with the ``REDACTED`` marker, so the original secret value is never written.
    # The only way ``secret`` can appear in the serialized line is as a coincidental
    # substring of the *non-secret* output: JSON punctuation (``{ } " : ,``), the
    # key name, the visible ``marker`` field, or the ``REDACTED`` placeholder. Those
    # collisions are not real leaks. Build the expected safe line (assuming correct
    # key-based redaction) and skip secrets that are coincidentally contained in it.
    # This stays a meaningful property: if redaction ever wrote the real value, the
    # actual output would diverge from this safe line and the assertion would fail.
    expected_safe_line = json.dumps(
        {key: REDACTED, "marker": "visible"}, ensure_ascii=False, sort_keys=True
    )
    # The sink also appends a trailing newline line-terminator, so account for it
    # here: a generated secret consisting of structural newline(s) is a
    # coincidental substring of the safe output, not a real value leak.
    assume(secret not in expected_safe_line + "\n")
    path = tmp_path_factory.mktemp("audit") / "audit.jsonl"
    sink = LocalJsonlAuditSink(path=path)
    sink.record({key: secret, "marker": "visible"})
    text = path.read_text(encoding="utf-8")
    assert secret not in text
    assert "visible" in text
    # The secret key's stored value is exactly the redaction marker.
    assert json.loads(text)[key] == REDACTED


# ---------------------------------------------------------------------------
# AllowAllPolicy
# ---------------------------------------------------------------------------


def _sample_record():
    return new_memory_record(
        content="example memory",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
    )


def test_allow_all_is_policy_provider():
    assert issubclass(AllowAllPolicy, PolicyProvider)


def test_allow_all_returns_allow():
    policy = AllowAllPolicy()
    allowed, reasons = policy.evaluate(_sample_record(), {})
    assert allowed is True
    assert reasons == []


@given(
    ctx=st.dictionaries(
        keys=st.text(max_size=10),
        values=st.one_of(st.text(max_size=10), st.integers(), st.booleans()),
        max_size=5,
    )
)
def test_property_allow_all_always_allows(ctx):
    """AllowAllPolicy allows regardless of context.

    Validates: Requirements 6.3, 6.5
    """
    allowed, reasons = AllowAllPolicy().evaluate(_sample_record(), ctx)
    assert allowed is True
    assert reasons == []


# ---------------------------------------------------------------------------
# NoOpInspector
# ---------------------------------------------------------------------------


def test_noop_is_ingestion_inspector():
    assert issubclass(NoOpInspector, IngestionInspector)


def test_noop_returns_unchanged_record():
    record = _sample_record()
    result = NoOpInspector().inspect(record)
    assert result is record
    assert result.sensitivity == record.sensitivity
    assert result.status == record.status
    assert result.content == record.content


@given(
    sensitivity=st.sampled_from(list(Sensitivity)),
    content=st.text(min_size=1, max_size=40).filter(lambda s: s.strip()),
)
def test_property_noop_preserves_all_fields(sensitivity, content):
    """NoOpInspector never alters the record it is given.

    Validates: Requirements 17.2, 17.3
    """
    record = new_memory_record(
        content=content,
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.GLOBAL,
        sensitivity=sensitivity,
    )
    before = (record.content, record.sensitivity, record.status, record.trust_score)
    result = NoOpInspector().inspect(record)
    after = (result.content, result.sensitivity, result.status, result.trust_score)
    assert result is record
    assert before == after
