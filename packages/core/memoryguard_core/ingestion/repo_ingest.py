# SPDX-License-Identifier: Apache-2.0
"""Git repository ingestion for the Ingestion Layer (OSS core).

:func:`ingest_repo` ingests the text files of a local directory, attaching a
``source_ref`` that identifies the repository and — where available — the current
commit reference. It detects a git repository by the presence of a ``.git``
directory/file and reads the checked-out commit via ``git rev-parse HEAD``. When
git or the ``.git`` metadata is unavailable, ingestion still proceeds and the
commit portion of the ``source_ref`` falls back to ``worktree`` (Requirement 3.4
is satisfied "where available").

Each created record uses a ``repo://<relpath>@<commit>#chunk=<i>`` ``source_ref``
so provenance points back to the file within the repository at a specific commit.
``scope_ref`` defaults to the repository folder name.

This module reuses :func:`~memoryguard_core.ingestion.ingest.ingest_folder` for
the actual file walking, chunking, embedding, inspection, and skip-on-error
behavior. It is part of the Apache-2.0 OSS core, uses only the Python standard
library (plus ``subprocess`` for the optional ``git`` call), and MUST NOT import
from any commercial package.

Requirements: 3.4, 3.5.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Protocol

from memoryguard_core.ingestion.chunking import DEFAULT_MAX_CHARS, DEFAULT_OVERLAP
from memoryguard_core.ingestion.ingest import (
    IngestionFailure,
    ingest_folder,
)
from memoryguard_core.models import MemoryRecord, Scope, SourceType

__all__ = ["ingest_repo", "read_commit", "WORKTREE_REF"]

logger = logging.getLogger("memoryguard.ingestion")

#: Commit-reference placeholder used when no git commit can be resolved.
WORKTREE_REF = "worktree"


class _Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class _Store(Protocol):
    def add(self, record: MemoryRecord) -> MemoryRecord: ...


class _Inspector(Protocol):
    def inspect(self, record: MemoryRecord) -> MemoryRecord: ...


def _has_git_metadata(repo_root: Path) -> bool:
    """Return ``True`` when ``repo_root`` looks like a git repo (has ``.git``)."""

    # ``.git`` is a directory in a normal clone and a file in a worktree/submodule.
    return (repo_root / ".git").exists()


def read_commit(repo_root: Path) -> Optional[str]:
    """Return the current commit hash for ``repo_root``, or ``None`` if unavailable.

    Gracefully handles a missing ``git`` executable, a non-repository path, and
    any ``git`` invocation error — returning ``None`` rather than raising so that
    ingestion can fall back to the worktree reference.
    """

    if not _has_git_metadata(repo_root):
        return None
    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
        logger.warning("ingest_repo: git rev-parse failed for %s (%s)", repo_root, exc)
        return None
    if completed.returncode != 0:
        return None
    commit = completed.stdout.strip()
    return commit or None


def ingest_repo(
    store: _Store,
    embedder: _Embedder,
    path: str | Path,
    *,
    scope: Scope = Scope.REPO,
    scope_ref: Optional[str] = None,
    inspector: Optional[_Inspector] = None,
    extensions: Optional[set[str] | frozenset[str] | list[str]] = None,
    source_type: SourceType = SourceType.COMMIT,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    failures: Optional[list[IngestionFailure]] = None,
) -> list[MemoryRecord]:
    """Ingest a git repository's text files with repository + commit provenance.

    Detects the repository at ``path``, resolves its current commit (or falls back
    to ``"worktree"``), and ingests its supported text files via
    :func:`ingest_folder`. Every created record receives a
    ``repo://<relpath>@<commit>#chunk=<i>`` ``source_ref`` and the supplied
    ``scope``/``scope_ref`` (``scope_ref`` defaults to the repo folder name).

    Args:
        store: the ``MemoryStore`` to persist into.
        embedder: the ``Embedder`` used to compute embeddings.
        path: the repository root (or any text folder).
        scope: scope for created records (default :attr:`Scope.REPO`).
        scope_ref: scope binding id; defaults to the repository folder name.
        inspector: optional ingestion inspector.
        extensions: optional override of ingested file extensions.
        source_type: provenance source type for created records (default
            :attr:`SourceType.COMMIT`).
        max_chars / overlap: chunking parameters.
        failures: optional list to which skipped-file failures are appended.

    Returns:
        The combined list of persisted records across the repository.
    """

    repo_root = Path(path).resolve()
    if scope_ref is None:
        scope_ref = repo_root.name

    commit = read_commit(repo_root)
    ref = commit if commit else WORKTREE_REF

    def _repo_source_ref(resolved: Path, chunk_index: int) -> str:
        try:
            rel = resolved.relative_to(repo_root).as_posix()
        except ValueError:  # pragma: no cover - defensive; ingest_folder stays under root
            rel = resolved.name
        return f"repo://{rel}@{ref}#chunk={chunk_index}"

    return ingest_folder(
        store,
        embedder,
        repo_root,
        scope=scope,
        scope_ref=scope_ref,
        inspector=inspector,
        extensions=extensions,
        source_type=source_type,
        source_ref_builder=_repo_source_ref,
        max_chars=max_chars,
        overlap=overlap,
        failures=failures,
    )
