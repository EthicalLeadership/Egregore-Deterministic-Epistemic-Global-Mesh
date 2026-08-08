CREATE TABLE IF NOT EXISTS dossiers (
    dossier_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    intent_hash TEXT NOT NULL,
    state JSONB NOT NULL,
    canonical_state TEXT NOT NULL,
    timestamp_ns BIGINT NOT NULL,
    signature TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(case_id, version)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    dossier_id TEXT NOT NULL REFERENCES dossiers(dossier_id) ON DELETE CASCADE,
    event_schema_version INTEGER NOT NULL,
    event_seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    timestamp_ns BIGINT NOT NULL,
    provenance_hash TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dossier_id, event_seq)
);

CREATE TABLE IF NOT EXISTS governance_log (
    log_id BIGSERIAL PRIMARY KEY,
    timestamp_ns BIGINT NOT NULL,
    checkpoint TEXT NOT NULL CHECK (checkpoint IN ('M1', 'M2', 'M3', 'M4')),
    operation TEXT NOT NULL,
    scope TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('EQUIVALENT', 'DIVERGED', 'FAIL')),
    note TEXT,
    dossier_id TEXT REFERENCES dossiers(dossier_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS replay_traces (
    trace_id BIGSERIAL PRIMARY KEY,
    dossier_id TEXT NOT NULL REFERENCES dossiers(dossier_id) ON DELETE CASCADE,
    trace_hash TEXT NOT NULL,
    timestamp_ns BIGINT NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dossiers_case_id ON dossiers(case_id);
CREATE INDEX IF NOT EXISTS idx_dossiers_timestamp ON dossiers(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_events_dossier ON events(dossier_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_governance_checkpoint ON governance_log(checkpoint, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_governance_dossier ON governance_log(dossier_id);
CREATE INDEX IF NOT EXISTS idx_governance_result ON governance_log(result, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_replay_dossier ON replay_traces(dossier_id);
CREATE INDEX IF NOT EXISTS idx_replay_hash ON replay_traces(trace_hash);
