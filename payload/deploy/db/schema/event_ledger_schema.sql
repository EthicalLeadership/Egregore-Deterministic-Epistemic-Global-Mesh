-- Canonical Event Ledger Table (AAA+++)
CREATE TABLE event_ledger (
    event_id UUID PRIMARY KEY,
    event_version VARCHAR(8) NOT NULL DEFAULT '1.0',
    event_type VARCHAR(64) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,

    trace_id UUID NOT NULL,
    correlation_id UUID NOT NULL,
    parent_event_id UUID,

    actor_user_id VARCHAR(64),
    actor_role VARCHAR(32),
    actor_tenant_id VARCHAR(64),
    actor_session_id VARCHAR(64),

    resource_type VARCHAR(32),
    resource_id VARCHAR(64),
    resource_version VARCHAR(32),

    action VARCHAR(64) NOT NULL,
    decision VARCHAR(16) NOT NULL,

    policy_id VARCHAR(64),
    policy_version VARCHAR(16),

    component VARCHAR(32) NOT NULL,
    node_id VARCHAR(64),
    cluster_id VARCHAR(64),

    budget_id VARCHAR(64),
    budget_cost NUMERIC,
    budget_remaining NUMERIC,

    payload JSONB NOT NULL,
    event_signature TEXT,
    event_hash TEXT,
    prev_event_hash TEXT,

    partition_month INT GENERATED ALWAYS AS (EXTRACT(MONTH FROM timestamp)) STORED
);

-- Partition by month for retention
CREATE INDEX idx_event_ledger_timestamp ON event_ledger (timestamp);
CREATE INDEX idx_event_ledger_trace_id ON event_ledger (trace_id);
CREATE INDEX idx_event_ledger_actor_user_id ON event_ledger (actor_user_id);
CREATE INDEX idx_event_ledger_actor_tenant_id ON event_ledger (actor_tenant_id);
CREATE INDEX idx_event_ledger_resource_type ON event_ledger (resource_type);
CREATE INDEX idx_event_ledger_resource_id ON event_ledger (resource_id);
CREATE INDEX idx_event_ledger_component ON event_ledger (component);
CREATE INDEX idx_event_ledger_decision ON event_ledger (decision);
CREATE INDEX idx_event_ledger_partition_month ON event_ledger (partition_month);

-- Retention policy: 7 years (configurable)
-- No UPDATE, No DELETE (append-only)
-- Hash-chaining: event_hash, prev_event_hash
-- Event signing: event_signature
