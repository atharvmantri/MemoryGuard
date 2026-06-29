# SPDX-License-Identifier: Apache-2.0
"""Deterministic content chunking for the Ingestion Layer (OSS core).

``chunk_text`` splits arbitrary text into a list of size-capped chunks, preferring
natural boundaries (paragraphs, then lines, then words) and adding a small
character overlap between consecutive chunks so that context straddling a chunk
boundary is not lost at retrieval time.

Design properties:

* **Deterministic** — identical input + parameters always yields identical chunks.
* **Boundary-preferring** — splits on blank-line paragraph breaks first, then
  single-line breaks, then word boundaries, and only hard-cuts tokens that are
  individually larger than the budget.
* **Size cap** — every returned chunk is at most ``max_chars`` characters.
* **Overlap** — each chunk after the first is prefixed with the trailing
  ``overlap`` characters of the previous chunk.
* **Empty/short safe** — empty/whitespace-only text yields ``[]``; text that fits
  in a single chunk yields a one-element list.

This module is part of the Apache-2.0 OSS core and is standard-library only. It
MUST NOT import from any commercial package.

Requirements: 3.2 (chunking for file ingestion).
"""

from __future__ import annotations

import re

__all__ = ["chunk_text", "DEFAULT_MAX_CHARS", "DEFAULT_OVERLAP"]

#: Default maximum characters per chunk.
DEFAULT_MAX_CHARS = 1000

#: Default overlap (characters) carried from the previous chunk into the next.
DEFAULT_OVERLAP = 100

#: Paragraph boundary: a blank line (optionally containing whitespace).
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")


def chunk_text(text: str, *, max_chars: int = DEFAULT_MAX_CHARS, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """Split ``text`` into deterministic, size-capped, overlapping chunks.

    Args:
        text: the content to split. ``None``-ish/empty/whitespace-only input
            returns ``[]``.
        max_chars: maximum characters per returned chunk (must be positive).
        overlap: characters of the previous chunk to prepend to each subsequent
            chunk (must be non-negative and less than ``max_chars``).

    Returns:
        A list of chunk strings, each at most ``max_chars`` characters. Returns
        ``[]`` for empty input and a single-element list when the (trimmed) text
        already fits within ``max_chars``.

    Raises:
        ValueError: if ``max_chars`` is not positive, ``overlap`` is negative, or
            ``overlap >= max_chars``.
    """

    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")

    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")

    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= max_chars:
        return [stripped]

    # Reserve room so that prepending the overlap tail (plus a separator) to a
    # packed chunk never exceeds ``max_chars``.
    sep_len = 1 if overlap > 0 else 0
    budget = max(1, max_chars - overlap - sep_len)

    pieces = _atomic_pieces(stripped, budget)
    packed = _pack(pieces, budget)
    return _apply_overlap(packed, overlap)


def _atomic_pieces(text: str, limit: int) -> list[str]:
    """Break ``text`` into atomic pieces each no longer than ``limit`` chars.

    Prefers paragraph boundaries, then line boundaries, then word boundaries, and
    finally hard-cuts any single token longer than ``limit``.
    """

    pieces: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        para = paragraph.strip()
        if not para:
            continue
        if len(para) <= limit:
            pieces.append(para)
            continue
        for raw_line in para.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if len(line) <= limit:
                pieces.append(line)
            else:
                pieces.extend(_split_long(line, limit))
    return pieces


def _split_long(segment: str, limit: int) -> list[str]:
    """Split a single over-long ``segment`` on word boundaries, hard-cutting words.

    Words longer than ``limit`` are sliced into ``limit``-sized fragments.
    """

    out: list[str] = []
    current = ""
    for word in segment.split(" "):
        if not word:
            continue
        if len(word) > limit:
            if current:
                out.append(current)
                current = ""
            for start in range(0, len(word), limit):
                out.append(word[start : start + limit])
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= limit:
            current = f"{current} {word}"
        else:
            out.append(current)
            current = word
    if current:
        out.append(current)
    return out


def _pack(pieces: list[str], budget: int) -> list[str]:
    """Greedily merge ``pieces`` into chunks no larger than ``budget`` chars."""

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= budget:
            current = f"{current}\n{piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """Prefix each chunk after the first with the previous chunk's trailing tail."""

    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    result = [chunks[0]]
    for index in range(1, len(chunks)):
        tail = chunks[index - 1][-overlap:]
        result.append(f"{tail}\n{chunks[index]}")
    return result
