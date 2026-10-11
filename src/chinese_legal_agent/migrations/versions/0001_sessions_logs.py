"""创建 PostgreSQL 会话和结构化日志表。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_sessions_logs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("sessions_updated_at", "sessions", [sa.text("updated_at DESC")])
    op.create_table(
        "logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("logger", sa.Text(), nullable=False),
        sa.Column("event", sa.Text()),
        sa.Column("session_id", sa.Text()),
        sa.Column("trace_id", sa.Text()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("model", sa.Text()),
        sa.Column("tool_name", sa.Text()),
        sa.Column("tool_call_id", sa.Text()),
        sa.Column("duration_ms", sa.Float()),
        sa.Column("error_type", sa.Text()),
        sa.Column("details_json", JSONB(), nullable=False),
        sa.Column("exception", sa.Text()),
    )
    op.create_index("logs_timestamp", "logs", [sa.text("timestamp DESC")])
    for column in ("event", "session_id", "trace_id"):
        op.create_index(f"logs_{column}", "logs", [column])


def downgrade() -> None:
    op.drop_table("logs")
    op.drop_table("sessions")
