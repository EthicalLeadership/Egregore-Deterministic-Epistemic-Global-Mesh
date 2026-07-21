CREATE TABLE IF NOT EXISTS execution_blocks (
    block_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    block_seq BIGINT NOT NULL,
    previous_block_hash TEXT NOT NULL,
    merkle_root TEXT,
    record_count INTEGER NOT NULL,
    block_hash TEXT NOT NULL,
    block_signature TEXT,
    causal_vector JSONB NOT NULL DEFAULT '{}',
    records JSONB NOT NULL DEFAULT '[]',
    created_at_ns BIGINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blocks_tenant_seq
    ON execution_blocks(tenant_id, block_seq);

CREATE INDEX IF NOT EXISTS idx_blocks_latest
    ON execution_blocks(tenant_id, block_seq DESC);
