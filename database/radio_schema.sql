-- Mindshare Radio Intelligence: transcripts and audit (docs/MINDSHARE_RADIO_INGEST.md).
-- Idempotent. Applied lazily by app/services/radio_service.py, once per process.
-- Transcripts are machine generated and unverified. Rows in segments are
-- purged 30 days after received_at; audit_events is never purged.

CREATE SCHEMA IF NOT EXISTS lcdash_radio;

CREATE TABLE IF NOT EXISTS lcdash_radio.segments (
    segment_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    codec TEXT NOT NULL,
    payload_type INTEGER NOT NULL,
    sample_rate_hz INTEGER NOT NULL,
    pcmu_packet_count INTEGER NOT NULL DEFAULT 0,
    transcript TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    transcribed_at TIMESTAMPTZ,
    asr_segments JSONB NOT NULL DEFAULT '[]'::JSONB,
    low_confidence BOOLEAN NOT NULL DEFAULT FALSE,
    audio_key TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT radio_segments_direction_check CHECK (direction IN ('tx', 'rx'))
);

CREATE INDEX IF NOT EXISTS radio_segments_started_idx
    ON lcdash_radio.segments (started_at DESC, segment_id DESC);

CREATE INDEX IF NOT EXISTS radio_segments_channel_idx
    ON lcdash_radio.segments (channel, started_at DESC);

CREATE INDEX IF NOT EXISTS radio_segments_received_idx
    ON lcdash_radio.segments (received_at);

CREATE TABLE IF NOT EXISTS lcdash_radio.audit_events (
    event_id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    segment_id TEXT,
    detail JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT radio_audit_event_type_check
        CHECK (event_type IN ('radio.segments.list', 'radio.segment.listen'))
);

CREATE INDEX IF NOT EXISTS radio_audit_created_idx
    ON lcdash_radio.audit_events (created_at DESC);
