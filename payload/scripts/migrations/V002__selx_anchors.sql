CREATE TABLE IF NOT EXISTS anchor_records (
    anchor_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    block_hash TEXT NOT NULL,
    notarization TEXT NOT NULL,
    public_verify BOOLEAN NOT NULL,
    timestamp_ns BIGINT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_anchor_block_hash ON anchor_records(block_hash);
