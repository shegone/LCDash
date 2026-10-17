-- Mindshare Radio Intelligence: transcripts and audit (docs/MINDSHARE_RADIO_INGEST.md,
-- v2 additions in docs/MINDSHARE_RADIO_CORRECTIONS.md).
-- Idempotent. Applied lazily by app/services/radio_service.py, once per process.
-- Transcripts are machine generated and unverified. Rows in segments are
-- purged 30 days after received_at unless a human correction extended
-- retain_until; audit_events and corrections are never purged on their own.

-- Serialize concurrent appliers (a rolling deploy runs old and new tasks at
-- once): without this, simultaneous CREATE SCHEMA / ALTER TABLE statements
-- race and deadlock. Transaction-scoped, released at commit.
SELECT pg_advisory_xact_lock(727452301);

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
        CHECK (event_type IN (
            'radio.segments.list', 'radio.segment.listen',
            'radio.segment.correct', 'radio.segment.correct_revert',
            'radio.fix.add', 'radio.fix.remove',
            'radio.watch.add', 'radio.watch.change', 'radio.watch.remove',
            'radio.alert.dismiss'
        ))
);

CREATE INDEX IF NOT EXISTS radio_audit_created_idx
    ON lcdash_radio.audit_events (created_at DESC);

-- v2: human corrections keep the audio and the corrected transcript longer.
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS corrected_text TEXT;
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS corrected_by TEXT;
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS corrected_at TIMESTAMPTZ;
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS retain_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS radio_segments_retain_idx
    ON lcdash_radio.segments (retain_until)
    WHERE retain_until IS NOT NULL;

-- Append-only correction history (training data). new_text NULL is a revert.
CREATE TABLE IF NOT EXISTS lcdash_radio.corrections (
    correction_id BIGSERIAL PRIMARY KEY,
    segment_id TEXT NOT NULL
        REFERENCES lcdash_radio.segments (segment_id) ON DELETE CASCADE,
    previous_text TEXT,
    new_text TEXT,
    actor TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS radio_corrections_segment_idx
    ON lcdash_radio.corrections (segment_id, created_at DESC);

-- Admin-managed find-and-replace fixes, applied to machine transcripts at read time.
CREATE TABLE IF NOT EXISTS lcdash_radio.replacements (
    replacement_id BIGSERIAL PRIMARY KEY,
    find_text TEXT NOT NULL,
    replace_text TEXT NOT NULL,
    whole_word BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS radio_replacements_find_idx
    ON lcdash_radio.replacements (lower(find_text));

-- Admin-managed key words and phrases that raise a dashboard alert.
CREATE TABLE IF NOT EXISTS lcdash_radio.watch_terms (
    term_id BIGSERIAL PRIMARY KEY,
    phrase TEXT NOT NULL,
    channels TEXT[] NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS radio_watch_terms_phrase_idx
    ON lcdash_radio.watch_terms (lower(phrase));

-- One dashboard alert per (segment, term). Dismissal is shared and visual only.
CREATE TABLE IF NOT EXISTS lcdash_radio.alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    segment_id TEXT NOT NULL
        REFERENCES lcdash_radio.segments (segment_id) ON DELETE CASCADE,
    term_id BIGINT NOT NULL
        REFERENCES lcdash_radio.watch_terms (term_id) ON DELETE CASCADE,
    phrase TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dismissed_by TEXT,
    dismissed_at TIMESTAMPTZ,
    CONSTRAINT radio_alerts_segment_term_unique UNIQUE (segment_id, term_id)
);

CREATE INDEX IF NOT EXISTS radio_alerts_open_idx
    ON lcdash_radio.alerts (created_at DESC)
    WHERE dismissed_at IS NULL;

-- Databases created before v2 carry the two-event audit constraint; widen it
-- once. This is the only constraint change and it runs only when needed.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'radio_audit_event_type_check'
          AND conrelid = 'lcdash_radio.audit_events'::regclass
          AND position('radio.alert.dismiss' IN pg_get_constraintdef(oid)) = 0
    ) THEN
        ALTER TABLE lcdash_radio.audit_events
            DROP CONSTRAINT radio_audit_event_type_check;
        ALTER TABLE lcdash_radio.audit_events
            ADD CONSTRAINT radio_audit_event_type_check
            CHECK (event_type IN (
                'radio.segments.list', 'radio.segment.listen',
                'radio.segment.correct', 'radio.segment.correct_revert',
                'radio.fix.add', 'radio.fix.remove',
                'radio.watch.add', 'radio.watch.change', 'radio.watch.remove',
                'radio.alert.dismiss'
            ));
    END IF;
END
$$;
