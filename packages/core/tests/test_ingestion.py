# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the MemoryGuard Ingestion Layer (tasks 13.1 + 13.2).

Covers:

* ``chunk_text`` — empty/short handling, size cap, overlap, boundary splitting,
  and determinism.
* ``add_memory`` — manual add round-trips via ``store.get`` with provenance,
  scope, embedding, and the optional inspector hook (Requirement 3.1).
* ``ingest_file`` — one record per chunk with a ``file://`` ``source_ref`` and an
  embedding (Requirements 3.2, 3.5, 3.6); unreadable/binary files are skipped and
  recorded as failures rather than crashing (Requirement 3.7).
* ``ingest_folder`` — ingests multiple supported files, skipping binaries
  (Requirements 3.3, 3.7).
* ``ingest_repo`` — attaches a ``repo://`` ``source_ref`` with a commit reference,
  falling back to ``worktree`` when no git commit is available (Requirements 3.4,
  3.5).

Standard library + pytest only. Uses ``SqliteStore(":memory:")`` +
``LocalEmbedder`` (deterministic, offline) so the suite runs without network or
ML dependencies.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from memoryguard_core.ingestion import (
    DEFAULT_TEXT_EXTENSIONS,
    IngestionFailure,
    Ingestor,
    add_memory,
    chunk_text,
    ingest_file,
    ingest_folder,
    ingest_repo,
    read_commit,
)
from memoryguard_core.ingestion.repo_ingest import WORKTREE_REF
from memoryguard_core.models import (
    MemoryRecord,
    Scope,
    Sensitivity,
    SourceType,
)
from memoryguard_core.retrieval.policy_filter import IngestionInspector
from memoryguard_core.store import SqliteStore
from memoryguard_models.embedder.local_embedder import LocalEmbedder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> SqliteStore:
    s = SqliteStore(":memory:")
    yield s
    s.close()


@pytest.fixture()
def embedder() -> LocalEmbedder:
    return LocalEmbedder()


def _assert_round_trip(fetched: MemoryRecord, rec: MemoryRecord) -> None:
    """Assert ``fetched`` equals ``rec`` modulo float32 embedding rounding.

    The store persists embeddings as packed ``float32`` blobs, so the round-tripped
    vector matches only to float32 precision (not bit-identical to the in-memory
    float64 list). Compare the embedding with tolerance and every other field
    exactly.
    """

    assert fetched is not None
    assert fetched.embedding == pytest.approx(rec.embedding, abs=1e-6)
    fetched.embedding = rec.embedding  # normalize before exact comparison
    assert fetched == rec


class _FlagInspector(IngestionInspector):
    """Test inspector that elevates sensitivity and records calls."""

    def __init__(self) -> None:
        self.calls = 0

    def inspect(self, record: MemoryRecord) -> MemoryRecord:
        self.calls += 1
        record.sensitivity = Sensitivity.SECRET
        return record


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


def test_chunk_text_empty_returns_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_chunk_text_short_returns_single_trimmed_chunk() -> None:
    assert chunk_text("  hello world  ", max_chars=1000) == ["hello world"]


def test_chunk_text_respects_size_cap_and_overlaps() -> None:
    text = "\n\n".join(f"paragraph number {i} with some filler words" for i in range(60))
    chunks = chunk_text(text, max_chars=200, overlap=40)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 200

    # Every chunk after the first must begin with the trailing tail of the prior
    # chunk (the overlap), demonstrating context carry-over.
    for prev, nxt in zip(chunks, chunks[1:]):
        tail = prev[-40:]
        assert nxt.startswith(tail)


def test_chunk_text_is_deterministic() -> None:
    text = "\n\n".join(f"line {i} lorem ipsum dolor sit amet" for i in range(40))
    assert chunk_text(text, max_chars=150, overlap=30) == chunk_text(
        text, max_chars=150, overlap=30
    )


def test_chunk_text_hard_cuts_oversized_token() -> None:
    chunks = chunk_text("x" * 5000, max_chars=100, overlap=0)
    assert len(chunks) >= 50
    assert all(len(c) <= 100 for c in chunks)


def test_chunk_text_validates_parameters() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=0)
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=100, overlap=-1)


# ---------------------------------------------------------------------------
# add_memory (Requirement 3.1)
# ---------------------------------------------------------------------------


def test_add_memory_round_trips_with_provenance(store, embedder) -> None:
    rec = add_memory(
        store,
        embedder,
        content="the deploy key rotates monthly",
        source_type=SourceType.USER,
        source_ref="user://alice",
        scope=Scope.PROJECT,
        scope_ref="proj-1",
        tags=["ops", "rotation"],
    )

    fetched = store.get(rec.memory_id)
    assert fetched is not None
    _assert_round_trip(fetched, rec)
    assert fetched.source_type == SourceType.USER
    assert fetched.source_ref == "user://alice"
    assert fetched.scope == Scope.PROJECT
    assert fetched.scope_ref == "proj-1"
    assert fetched.tags == ["ops", "rotation"]
    # Embedding computed via the configured embedder (Requirement 3.6).
    assert fetched.embedding is not None
    assert len(fetched.embedding) == embedder.dim


def test_add_memory_runs_inspector_hook(store, embedder) -> None:
    inspector = _FlagInspector()
    rec = add_memory(
        store,
        embedder,
        content="api_key = SECRET",
        source_type=SourceType.USER,
        source_ref="user://bob",
        scope=Scope.GLOBAL,
        inspector=inspector,
    )
    assert inspector.calls == 1
    assert store.get(rec.memory_id).sensitivity == Sensitivity.SECRET


def test_add_memory_rejects_missing_scope_ref(store, embedder) -> None:
    # PROJECT scope requires a scope_ref (Requirement 2.5 enforced via factory).
    with pytest.raises(Exception):
        add_memory(
            store,
            embedder,
            content="x",
            source_type=SourceType.USER,
            source_ref="user://x",
            scope=Scope.PROJECT,
        )


# ---------------------------------------------------------------------------
# ingest_file (Requirements 3.2, 3.5, 3.6, 3.7)
# ---------------------------------------------------------------------------


def test_ingest_file_creates_per_chunk_records(tmp_path, store, embedder) -> None:
    content = "\n\n".join(f"section {i} content goes here with words" for i in range(40))
    file_path = tmp_path / "notes.md"
    file_path.write_text(content, encoding="utf-8")

    records = ingest_file(
        store,
        embedder,
        file_path,
        scope=Scope.PROJECT,
        scope_ref="proj-1",
        max_chars=200,
        overlap=40,
    )

    assert len(records) > 1
    # Match expectations against the same chunking the ingestor performed.
    expected_chunks = chunk_text(content, max_chars=200, overlap=40)
    assert len(records) == len(expected_chunks)

    for index, rec in enumerate(records):
        assert rec.source_type == SourceType.FILE
        assert rec.source_ref.startswith("file://")
        assert rec.source_ref.endswith(f"#chunk={index}")
        assert "notes.md" in rec.source_ref
        assert rec.scope == Scope.PROJECT
        assert rec.scope_ref == "proj-1"
        assert rec.embedding is not None and len(rec.embedding) == embedder.dim
        # Persisted and retrievable.
        _assert_round_trip(store.get(rec.memory_id), rec)


def test_ingest_file_unreadable_is_skipped(tmp_path, store, embedder) -> None:
    # Invalid UTF-8 bytes -> UnicodeDecodeError on read; must be skipped, not crash.
    binary_path = tmp_path / "blob.txt"
    binary_path.write_bytes(b"\xff\xfe\x00\x01\x02\x80\x81 not valid utf-8 \xc3\x28")

    failures: list[IngestionFailure] = []
    records = ingest_file(
        store,
        embedder,
        binary_path,
        scope=Scope.GLOBAL,
        failures=failures,
    )

    assert records == []
    assert len(failures) == 1
    assert failures[0].path.endswith("blob.txt")


def test_ingest_missing_file_is_skipped(tmp_path, store, embedder) -> None:
    failures: list[IngestionFailure] = []
    records = ingest_file(
        store,
        embedder,
        tmp_path / "does_not_exist.txt",
        scope=Scope.GLOBAL,
        failures=failures,
    )
    assert records == []
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# ingest_folder (Requirements 3.3, 3.7)
# ---------------------------------------------------------------------------


def test_ingest_folder_ingests_multiple_files_and_skips_binaries(tmp_path, store, embedder) -> None:
    (tmp_path / "a.md").write_text("alpha document content", encoding="utf-8")
    (tmp_path / "b.py").write_text("def b():\n    return 'beta'", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("gamma nested content", encoding="utf-8")
    # Unsupported binary extension -> skipped by extension filter.
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    # Supported extension but invalid bytes -> skipped on decode (3.7).
    (tmp_path / "broken.txt").write_bytes(b"\xff\xfe\x00bad")

    failures: list[IngestionFailure] = []
    records = ingest_folder(
        store,
        embedder,
        tmp_path,
        scope=Scope.PROJECT,
        scope_ref="proj-1",
        failures=failures,
    )

    refs = {r.source_ref for r in records}
    # The three valid text files produced records (each fits one chunk).
    assert len(records) == 3
    assert any("a.md" in r for r in refs)
    assert any("b.py" in r for r in refs)
    assert any("c.txt" in r for r in refs)
    # The .png was never attempted (extension filtered); broken.txt failed decode.
    assert not any("image.png" in r for r in refs)
    assert any(f.path.endswith("broken.txt") for f in failures)
    # Every record is persisted and embedded.
    for rec in records:
        assert store.get(rec.memory_id) is not None
        assert rec.embedding is not None


def test_default_text_extensions_cover_common_types() -> None:
    for ext in (".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".rst"):
        assert ext in DEFAULT_TEXT_EXTENSIONS


# ---------------------------------------------------------------------------
# ingest_repo (Requirements 3.4, 3.5)
# ---------------------------------------------------------------------------


def test_ingest_repo_attaches_repo_source_ref_worktree_fallback(tmp_path, store, embedder) -> None:
    # A plain directory with NO .git -> commit unavailable -> "worktree" fallback.
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "README.md").write_text("project readme content", encoding="utf-8")
    (repo / "main.py").write_text("print('hello')", encoding="utf-8")

    records = ingest_repo(store, embedder, repo, scope=Scope.REPO)

    assert len(records) == 2
    for rec in records:
        assert rec.source_type == SourceType.COMMIT
        assert rec.source_ref.startswith("repo://")
        # commit reference present; with no git it is the worktree fallback.
        assert f"@{WORKTREE_REF}" in rec.source_ref
        # scope_ref defaults to the repo folder name.
        assert rec.scope == Scope.REPO
        assert rec.scope_ref == "myrepo"
        assert rec.embedding is not None
        _assert_round_trip(store.get(rec.memory_id), rec)

    refs = {r.source_ref for r in records}
    assert any(r.startswith("repo://README.md@") for r in refs)
    assert any(r.startswith("repo://main.py@") for r in refs)


def test_ingest_repo_respects_explicit_scope_ref(tmp_path, store, embedder) -> None:
    repo = tmp_path / "another"
    repo.mkdir()
    (repo / "doc.txt").write_text("some documentation", encoding="utf-8")

    records = ingest_repo(store, embedder, repo, scope=Scope.REPO, scope_ref="custom-ref")
    assert records
    assert all(r.scope_ref == "custom-ref" for r in records)


# ---------------------------------------------------------------------------
# Ingestor convenience wrapper
# ---------------------------------------------------------------------------


def test_ingestor_accumulates_failures(tmp_path, store, embedder) -> None:
    (tmp_path / "ok.md").write_text("ok content", encoding="utf-8")
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\x00bad")

    ingestor = Ingestor(store=store, embedder=embedder)
    records = ingestor.ingest_folder(tmp_path, scope=Scope.GLOBAL)

    assert len(records) == 1
    assert any(f.path.endswith("bad.txt") for f in ingestor.failures)


# ---------------------------------------------------------------------------
# read_commit + ingest_repo real-commit path (Requirement 3.4)
#
# The suite above exercises the "worktree" fallback (a directory with no .git).
# These tests cover the other half of Requirement 3.4: when a real git commit IS
# available, ``ingest_repo`` must attach that commit reference to the source_ref.
# They are skipped automatically when ``git`` is not installed on the host.
# ---------------------------------------------------------------------------

_GIT = shutil.which("git")

#: Matches the 40-char (or longer) lowercase hex object name git rev-parse emits.
_COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{40,}$")


def _init_git_repo(root) -> str:
    """Initialize a git repo at ``root``, commit its files, and return HEAD.

    Configures a local identity so the commit succeeds in isolated CI sandboxes
    that have no global git config.
    """

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [_GIT, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )

    _git("init")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "MemoryGuard Test")
    _git("add", "-A")
    _git("commit", "-m", "initial commit")
    return _git("rev-parse", "HEAD").stdout.strip()


def test_read_commit_returns_none_for_non_git_directory(tmp_path) -> None:
    # A plain directory has no .git metadata -> no commit resolvable.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert read_commit(plain) is None


@pytest.mark.skipif(_GIT is None, reason="git executable not available")
def test_read_commit_returns_head_hash_for_real_repo(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hello", encoding="utf-8")
    head = _init_git_repo(repo)

    resolved = read_commit(repo.resolve())
    assert resolved == head
    assert _COMMIT_HASH_RE.match(resolved)


@pytest.mark.skipif(_GIT is None, reason="git executable not available")
def test_ingest_repo_attaches_real_commit_source_ref(tmp_path, store, embedder) -> None:
    repo = tmp_path / "committed"
    repo.mkdir()
    (repo / "README.md").write_text("project readme content", encoding="utf-8")
    (repo / "main.py").write_text("print('hello')", encoding="utf-8")
    head = _init_git_repo(repo)

    records = ingest_repo(store, embedder, repo, scope=Scope.REPO)

    assert len(records) == 2
    for rec in records:
        assert rec.source_type == SourceType.COMMIT
        assert rec.source_ref.startswith("repo://")
        # Requirement 3.4: the real commit reference is attached, NOT the
        # worktree fallback.
        assert f"@{head}" in rec.source_ref
        assert f"@{WORKTREE_REF}" not in rec.source_ref
        assert rec.scope == Scope.REPO
        assert rec.scope_ref == "committed"
        _assert_round_trip(store.get(rec.memory_id), rec)

    refs = {r.source_ref for r in records}
    assert any(r.startswith(f"repo://README.md@{head}") for r in refs)
    assert any(r.startswith(f"repo://main.py@{head}") for r in refs)
