CREATE SCHEMA IF NOT EXISTS lcdash_realtime;

CREATE TABLE IF NOT EXISTS lcdash_realtime.webhook_events (
    event_id CHAR(64) PRIMARY KEY,
    source VARCHAR(20) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    payload_size INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    CHECK (source IN ('cfs', 'units')),
    CHECK (payload_size >= 0),
    CHECK (duplicate_count >= 0)
);

CREATE INDEX IF NOT EXISTS webhook_events_received_at_idx
    ON lcdash_realtime.webhook_events (received_at DESC);

