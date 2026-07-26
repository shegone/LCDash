CREATE SCHEMA IF NOT EXISTS lcdash_knowledge;

CREATE TABLE IF NOT EXISTS lcdash_knowledge.documents (
    document_id BIGSERIAL PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    file_size BIGINT NOT NULL DEFAULT 0,
    modified_at TIMESTAMPTZ,
    page_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE lcdash_knowledge.documents
    ADD COLUMN IF NOT EXISTS library_key TEXT NOT NULL DEFAULT 'centralsquare';

CREATE INDEX IF NOT EXISTS knowledge_documents_library_idx
    ON lcdash_knowledge.documents(library_key, title);

CREATE TABLE IF NOT EXISTS lcdash_knowledge.chunks (
    chunk_id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL
        REFERENCES lcdash_knowledge.documents(document_id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding REAL[],
    embedding_model TEXT NOT NULL DEFAULT '',
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', COALESCE(content, ''))
    ) STORED,
    UNIQUE(document_id, page_number, chunk_index)
);

ALTER TABLE lcdash_knowledge.chunks
    ADD COLUMN IF NOT EXISTS embedding REAL[];
ALTER TABLE lcdash_knowledge.chunks
    ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS knowledge_chunks_search_idx
    ON lcdash_knowledge.chunks USING GIN(search_vector);

CREATE INDEX IF NOT EXISTS knowledge_chunks_document_idx
    ON lcdash_knowledge.chunks(document_id, page_number);

CREATE TABLE IF NOT EXISTS lcdash_knowledge.index_state (
    state_id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (state_id),
    status TEXT NOT NULL DEFAULT 'never_run',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    documents_found INTEGER NOT NULL DEFAULT 0,
    documents_indexed INTEGER NOT NULL DEFAULT 0,
    documents_unchanged INTEGER NOT NULL DEFAULT 0,
    documents_failed INTEGER NOT NULL DEFAULT 0,
    chunks_stored INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT NOT NULL DEFAULT ''
);

INSERT INTO lcdash_knowledge.index_state (state_id)
VALUES (TRUE)
ON CONFLICT (state_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS lcdash_knowledge.library_index_state (
    library_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'never_run',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    documents_found INTEGER NOT NULL DEFAULT 0,
    documents_indexed INTEGER NOT NULL DEFAULT 0,
    documents_unchanged INTEGER NOT NULL DEFAULT 0,
    documents_failed INTEGER NOT NULL DEFAULT 0,
    chunks_stored INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT NOT NULL DEFAULT ''
);

INSERT INTO lcdash_knowledge.library_index_state (library_key)
VALUES ('centralsquare'), ('mindshare')
ON CONFLICT (library_key) DO NOTHING;
