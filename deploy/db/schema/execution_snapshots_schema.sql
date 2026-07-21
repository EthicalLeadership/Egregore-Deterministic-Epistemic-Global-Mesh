-- Hybrid Snapshot + Event-Sourced Replay: execution_snapshots table
CREATE TABLE execution_snapshots (
    snapshot_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    block_height BIGINT NOT NULL,
    state_root_hash TEXT NOT NULL,
    state_version TEXT NOT NULL,
    state_blob BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX idx_snapshot_per_block
ON execution_snapshots (tenant_id, block_height);

-- Partitioning recommended for scale
-- PARTITION BY RANGE (created_at)
