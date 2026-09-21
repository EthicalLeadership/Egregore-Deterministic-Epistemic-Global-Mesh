"""
Add workflows table
"""
# Alembic identifiers
revision = '20260219_add_workflows_table'
down_revision = None
branch_labels = None
depends_on = None
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lane", sa.String(128), index=True, nullable=False),
        sa.Column("shadow", sa.String(128), index=True, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("lane", "shadow", "idempotency_key", name="uq_workflow_idem"),
    )

def downgrade() -> None:
    op.drop_table("workflows")
