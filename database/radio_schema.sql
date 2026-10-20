-- Mindshare Radio Intelligence: transcripts and audit (docs/MINDSHARE_RADIO_INGEST.md,
-- v2 additions in docs/MINDSHARE_RADIO_CORRECTIONS.md, v3 listener health and
-- hint words in docs/MINDSHARE_RADIO_HEALTH.md, v4 radio IDs and unit names in
-- docs/MINDSHARE_RADIO_UNITS.md).
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
            'radio.alert.dismiss', 'radio.hint.add', 'radio.hint.remove',
            'radio.unit.set', 'radio.unit.remove'
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

-- v3: the listener's last heartbeat. One row (id = 1), upserted on every
-- heartbeat. No transcript text and no source addresses are ever stored here.
CREATE TABLE IF NOT EXISTS lcdash_radio.listener_status (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL
);

-- v3: admin-managed hint words for the transcriber's glossary.
CREATE TABLE IF NOT EXISTS lcdash_radio.hints (
    hint_id BIGSERIAL PRIMARY KEY,
    phrase TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS radio_hints_phrase_idx
    ON lcdash_radio.hints (lower(phrase));

-- v4: who is talking. The listener reads the radio ID, talkgroup and console
-- label from the RTP payload-type-103 signalling and sends them in the optional
-- metadata `signal` object. Rows stored before v4 keep NULL (not captured).
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS radio_id TEXT;
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS talkgroup TEXT;
ALTER TABLE lcdash_radio.segments ADD COLUMN IF NOT EXISTS console_label TEXT;

CREATE INDEX IF NOT EXISTS radio_segments_radio_id_idx
    ON lcdash_radio.segments (radio_id, started_at DESC)
    WHERE radio_id IS NOT NULL;

-- v4: admin-managed unit names ("98426 = Medic 4"), shown on every transcript
-- card. The name only labels a radio ID; segments keep the ID if it is removed.
CREATE TABLE IF NOT EXISTS lcdash_radio.units (
    radio_id TEXT PRIMARY KEY CHECK (radio_id ~ '^[0-9]{1,10}$'),
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- v5: marker rows for one-time data fixes (see the end of this file). Each
-- fix runs once; the row records when and what it changed.
CREATE TABLE IF NOT EXISTS lcdash_radio.schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detail JSONB NOT NULL DEFAULT '{}'::JSONB
);

-- Databases created before v2 carry the two-event audit constraint, and v2
-- databases lack the two v3 hint events, and v3 databases lack the two v4 unit
-- events; widen it once to the full list. This
-- is the only constraint change and it runs only when needed (the guard looks
-- for the newest event, so a current constraint is left alone).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'radio_audit_event_type_check'
          AND conrelid = 'lcdash_radio.audit_events'::regclass
          AND position('radio.unit.remove' IN pg_get_constraintdef(oid)) = 0
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
                'radio.alert.dismiss', 'radio.hint.add', 'radio.hint.remove',
                'radio.unit.set', 'radio.unit.remove'
            ));
    END IF;
END
$$;

-- v5: one-time relabel of DMR audio stored under the wrong channel name.
-- Mindshare sheet v117 (revised 2026-10-20) shows cards 23 and 27 are DMR EMS
-- and DMR Fire. Until the listener config was corrected (2026-10-20 20:30:13
-- UTC) they were captured as ems_north_vhf and fire_north_vhf. Only rows that
-- started before that moment move; anything captured later under those names
-- is genuine Fire North on card 35 and stays. Audio objects keep their stored
-- key. The marker row makes this run exactly once, and its detail records how
-- many rows moved (table schema_migrations, created above). Runs under the
-- advisory lock taken at the top of this file.
DO $$
DECLARE
    moved_ems BIGINT;
    moved_fire BIGINT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM lcdash_radio.schema_migrations
        WHERE name = 'relabel-dmr-2026-10-20'
    ) THEN
        UPDATE lcdash_radio.segments
           SET channel = 'ems_dmr'
         WHERE channel = 'ems_north_vhf'
           AND started_at < TIMESTAMPTZ '2026-10-20 20:30:13+00';
        GET DIAGNOSTICS moved_ems = ROW_COUNT;
        UPDATE lcdash_radio.segments
           SET channel = 'fire_dmr'
         WHERE channel = 'fire_north_vhf'
           AND started_at < TIMESTAMPTZ '2026-10-20 20:30:13+00';
        GET DIAGNOSTICS moved_fire = ROW_COUNT;
        INSERT INTO lcdash_radio.schema_migrations (name, detail)
        VALUES (
            'relabel-dmr-2026-10-20',
            jsonb_build_object(
                'ems_north_vhf_to_ems_dmr', moved_ems,
                'fire_north_vhf_to_fire_dmr', moved_fire
            )
        );
    END IF;
END
$$;
