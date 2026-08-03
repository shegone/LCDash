CREATE SCHEMA IF NOT EXISTS lcdash_analytics;

CREATE TABLE IF NOT EXISTS lcdash_analytics.calls (
    cfs_number TEXT PRIMARY KEY,
    dispatch_agency TEXT NOT NULL DEFAULT '',
    response_agency TEXT NOT NULL DEFAULT '',
    call_taker TEXT NOT NULL DEFAULT '',
    call_taker_unique_identifier TEXT NOT NULL DEFAULT '',
    incident_code TEXT NOT NULL DEFAULT '',
    incident_description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    disposition_code TEXT NOT NULL DEFAULT '',
    disposition_description TEXT NOT NULL DEFAULT '',
    beat TEXT NOT NULL DEFAULT '',
    zone TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    latitude NUMERIC(9, 4),
    longitude NUMERIC(9, 4),
    is_scheduled BOOLEAN NOT NULL DEFAULT FALSE,
    incident_at TIMESTAMPTZ,
    call_received_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    source_collected_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE lcdash_analytics.calls
    ADD COLUMN IF NOT EXISTS call_taker TEXT NOT NULL DEFAULT '';

ALTER TABLE lcdash_analytics.calls
    ADD COLUMN IF NOT EXISTS call_taker_unique_identifier
        TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS lcdash_analytics.units (
    unit_number TEXT PRIMARY KEY,
    agency TEXT NOT NULL DEFAULT '',
    unit_type TEXT NOT NULL DEFAULT '',
    station TEXT NOT NULL DEFAULT '',
    last_seen_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.call_agency_times (
    cfs_number TEXT NOT NULL
        REFERENCES lcdash_analytics.calls(cfs_number) ON DELETE CASCADE,
    agency_ori TEXT NOT NULL,
    dispatched_at TIMESTAMPTZ,
    enroute_at TIMESTAMPTZ,
    staged_at TIMESTAMPTZ,
    on_scene_at TIMESTAMPTZ,
    at_patient_at TIMESTAMPTZ,
    backup_enroute_at TIMESTAMPTZ,
    backup_arrived_at TIMESTAMPTZ,
    leaving_at TIMESTAMPTZ,
    transporting_at TIMESTAMPTZ,
    arrived_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ,
    in_quarters_at TIMESTAMPTZ,
    PRIMARY KEY (cfs_number, agency_ori)
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.unit_responses (
    cfs_number TEXT NOT NULL
        REFERENCES lcdash_analytics.calls(cfs_number) ON DELETE CASCADE,
    unit_number TEXT NOT NULL,
    unit_type TEXT NOT NULL DEFAULT '',
    station TEXT NOT NULL DEFAULT '',
    beat TEXT NOT NULL DEFAULT '',
    dispatched_at TIMESTAMPTZ,
    enroute_at TIMESTAMPTZ,
    staged_at TIMESTAMPTZ,
    on_scene_at TIMESTAMPTZ,
    at_patient_at TIMESTAMPTZ,
    backup_enroute_at TIMESTAMPTZ,
    backup_arrived_at TIMESTAMPTZ,
    leaving_at TIMESTAMPTZ,
    transporting_at TIMESTAMPTZ,
    arrived_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ,
    in_quarters_at TIMESTAMPTZ,
    PRIMARY KEY (cfs_number, unit_number)
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.sync_state (
    state_key TEXT PRIMARY KEY,
    state_timestamp TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.sync_runs (
    run_id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    calls_discovered INTEGER NOT NULL DEFAULT 0,
    calls_stored INTEGER NOT NULL DEFAULT 0,
    analytics_failures INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.mae_interactions (
    interaction_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_email TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    source_metadata JSONB NOT NULL DEFAULT '[]'::JSONB,
    evidence_metadata JSONB NOT NULL DEFAULT '[]'::JSONB,
    entities JSONB NOT NULL DEFAULT '{}'::JSONB,
    write_access BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.mae_feedback (
    feedback_id BIGSERIAL PRIMARY KEY,
    interaction_id UUID NOT NULL
        REFERENCES lcdash_analytics.mae_interactions(interaction_id)
        ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_email TEXT NOT NULL DEFAULT '',
    rating TEXT NOT NULL CHECK (
        rating IN ('helpful', 'incorrect', 'incomplete', 'wrong_source')
    ),
    comment TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.jack_interactions (
    interaction_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_email TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    source_metadata JSONB NOT NULL DEFAULT '[]'::JSONB,
    evidence_metadata JSONB NOT NULL DEFAULT '[]'::JSONB,
    assurance_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    write_access BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.jack_feedback (
    feedback_id BIGSERIAL PRIMARY KEY,
    interaction_id UUID NOT NULL
        REFERENCES lcdash_analytics.jack_interactions(interaction_id)
        ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_email TEXT NOT NULL DEFAULT '',
    rating TEXT NOT NULL CHECK (
        rating IN ('helpful', 'incorrect', 'incomplete', 'wrong_source')
    ),
    comment TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.mae_evaluation_runs (
    evaluation_run_id BIGSERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    category TEXT NOT NULL,
    question TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    source_check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    read_only_check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    answer_check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    expected_source_kinds JSONB NOT NULL DEFAULT '[]'::JSONB,
    actual_source_kinds JSONB NOT NULL DEFAULT '[]'::JSONB,
    answer TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    error_summary TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.jack_evaluation_runs (
    evaluation_run_id BIGSERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    category TEXT NOT NULL,
    question TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    document_check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    support_check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    speed_check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    expected_documents JSONB NOT NULL DEFAULT '[]'::JSONB,
    actual_documents JSONB NOT NULL DEFAULT '[]'::JSONB,
    answer TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    error_summary TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.mae_memory (
    memory_id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL DEFAULT '',
    approved_at TIMESTAMPTZ,
    approved_by TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'retired')),
    title TEXT NOT NULL,
    trigger_text TEXT NOT NULL,
    guidance TEXT NOT NULL,
    source_interaction_id UUID
        REFERENCES lcdash_analytics.mae_interactions(interaction_id)
        ON DELETE SET NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS lcdash_analytics.jack_memory (
    memory_id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL DEFAULT '',
    approved_at TIMESTAMPTZ,
    approved_by TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'retired')),
    title TEXT NOT NULL,
    trigger_text TEXT NOT NULL,
    guidance TEXT NOT NULL,
    source_interaction_id UUID
        REFERENCES lcdash_analytics.jack_interactions(interaction_id)
        ON DELETE SET NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_analytics_calls_received
    ON lcdash_analytics.calls(call_received_at);
CREATE INDEX IF NOT EXISTS idx_analytics_calls_agency_received
    ON lcdash_analytics.calls(response_agency, call_received_at);
CREATE INDEX IF NOT EXISTS idx_analytics_calls_taker_received
    ON lcdash_analytics.calls(call_taker, call_received_at);
CREATE INDEX IF NOT EXISTS idx_analytics_calls_taker_identifier_received
    ON lcdash_analytics.calls(
        call_taker_unique_identifier,
        call_received_at
    );
CREATE INDEX IF NOT EXISTS idx_analytics_calls_incident_received
    ON lcdash_analytics.calls(incident_code, call_received_at);
CREATE INDEX IF NOT EXISTS idx_analytics_calls_zone_received
    ON lcdash_analytics.calls(zone, call_received_at);
CREATE INDEX IF NOT EXISTS idx_analytics_unit_responses_unit
    ON lcdash_analytics.unit_responses(unit_number, dispatched_at);
CREATE INDEX IF NOT EXISTS idx_analytics_unit_responses_station
    ON lcdash_analytics.unit_responses(station, dispatched_at);
CREATE INDEX IF NOT EXISTS idx_mae_interactions_created
    ON lcdash_analytics.mae_interactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mae_interactions_user
    ON lcdash_analytics.mae_interactions(user_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mae_feedback_interaction
    ON lcdash_analytics.mae_feedback(interaction_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jack_interactions_created
    ON lcdash_analytics.jack_interactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jack_interactions_user
    ON lcdash_analytics.jack_interactions(user_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jack_feedback_interaction
    ON lcdash_analytics.jack_feedback(interaction_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mae_evaluation_runs_case
    ON lcdash_analytics.mae_evaluation_runs(case_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_mae_evaluation_runs_started
    ON lcdash_analytics.mae_evaluation_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jack_evaluation_runs_case
    ON lcdash_analytics.jack_evaluation_runs(case_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jack_evaluation_runs_started
    ON lcdash_analytics.jack_evaluation_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_mae_memory_status
    ON lcdash_analytics.mae_memory(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_jack_memory_status
    ON lcdash_analytics.jack_memory(status, updated_at DESC);

CREATE OR REPLACE VIEW lcdash_analytics.unit_response_metrics AS
SELECT
    response.cfs_number,
    response.unit_number,
    response.unit_type,
    response.station,
    response.beat,
    response.dispatched_at,
    response.enroute_at,
    response.on_scene_at,
    response.transporting_at,
    response.arrived_at,
    response.available_at,
    CASE
        WHEN response.enroute_at >= response.dispatched_at
        THEN EXTRACT(EPOCH FROM response.enroute_at - response.dispatched_at)
    END AS turnout_seconds,
    CASE
        WHEN response.on_scene_at >= response.enroute_at
        THEN EXTRACT(EPOCH FROM response.on_scene_at - response.enroute_at)
    END AS travel_seconds,
    CASE
        WHEN response.on_scene_at >= call_record.call_received_at
        THEN EXTRACT(EPOCH FROM response.on_scene_at - call_record.call_received_at)
    END AS total_response_seconds,
    CASE
        WHEN response.transporting_at >= response.on_scene_at
        THEN EXTRACT(EPOCH FROM response.transporting_at - response.on_scene_at)
    END AS scene_to_transport_seconds,
    CASE
        WHEN response.arrived_at >= response.transporting_at
        THEN EXTRACT(EPOCH FROM response.arrived_at - response.transporting_at)
    END AS transport_seconds,
    CASE
        WHEN response.available_at >= response.arrived_at
        THEN EXTRACT(EPOCH FROM response.available_at - response.arrived_at)
    END AS destination_turnaround_seconds,
    CASE
        WHEN response.available_at >= response.dispatched_at
        THEN EXTRACT(EPOCH FROM response.available_at - response.dispatched_at)
    END AS commitment_seconds
FROM lcdash_analytics.unit_responses AS response
JOIN lcdash_analytics.calls AS call_record
    ON call_record.cfs_number = response.cfs_number;

CREATE OR REPLACE VIEW lcdash_analytics.call_response_metrics AS
SELECT
    agency_times.cfs_number,
    agency_times.agency_ori,
    agency_times.dispatched_at,
    agency_times.enroute_at,
    agency_times.on_scene_at,
    agency_times.available_at,
    CASE
        WHEN agency_times.dispatched_at >= call_record.call_received_at
        THEN EXTRACT(EPOCH FROM agency_times.dispatched_at - call_record.call_received_at)
    END AS call_processing_seconds,
    CASE
        WHEN agency_times.enroute_at >= agency_times.dispatched_at
        THEN EXTRACT(EPOCH FROM agency_times.enroute_at - agency_times.dispatched_at)
    END AS turnout_seconds,
    CASE
        WHEN agency_times.on_scene_at >= agency_times.enroute_at
        THEN EXTRACT(EPOCH FROM agency_times.on_scene_at - agency_times.enroute_at)
    END AS travel_seconds,
    CASE
        WHEN agency_times.on_scene_at >= call_record.call_received_at
        THEN EXTRACT(EPOCH FROM agency_times.on_scene_at - call_record.call_received_at)
    END AS total_response_seconds
FROM lcdash_analytics.call_agency_times AS agency_times
JOIN lcdash_analytics.calls AS call_record
    ON call_record.cfs_number = agency_times.cfs_number;
