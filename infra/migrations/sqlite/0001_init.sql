-- infra/migrations/sqlite/0001_init.sql
-- MemoryGuard local-mode (Phase 1) schema.
-- Defines the memories, memory_contradictions, memory_embeddings, and
-- memory_fts tables plus supporting indexes. Embeddings are stored as a
-- separate table and cosine similarity is computed in Python for SQLite.

CREATE TABLE memories (
    memory_id     TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    scope_ref     TEXT,
    created_at    TEXT NOT NULL,          -- ISO-8601 UTC
    updated_at    TEXT NOT NULL,
    expires_at    TEXT,
    trust_score   REAL NOT NULL DEFAULT 0.0,
    sensitivity   TEXT NOT NULL DEFAULT 'internal',
    status        TEXT NOT NULL DEFAULT 'active',
    confirmations INTEGER NOT NULL DEFAULT 0,
    tags          TEXT NOT NULL DEFAULT '[]',   -- JSON array
    metadata      TEXT NOT NULL DEFAULT '{}',   -- JSON object
    CHECK (trust_score >= 0.0 AND trust_score <= 1.0)
);

CREATE TABLE memory_contradictions (
    memory_id     TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
    contradicts_id TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
    detected_at   TEXT NOT NULL,
    reason        TEXT,
    PRIMARY KEY (memory_id, contradicts_id)
);

-- Embeddings stored as a separate table; cosine similarity computed in Python for SQLite.
CREATE TABLE memory_embeddings (
    memory_id     TEXT PRIMARY KEY REFERENCES memories(memory_id) ON DELETE CASCADE,
    dim           INTEGER NOT NULL,
    vector        BLOB NOT NULL          -- packed float32
);

-- FTS5 virtual table for keyword retrieval
CREATE VIRTUAL TABLE memory_fts USING fts5(
    content, tags, content='memories', content_rowid='rowid'
);

CREATE INDEX idx_memories_scope ON memories(scope, scope_ref);
CREATE INDEX idx_memories_status ON memories(status);
CREATE INDEX idx_memories_expires ON memories(expires_at);
