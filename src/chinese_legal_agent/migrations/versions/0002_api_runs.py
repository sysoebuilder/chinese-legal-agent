"""Agent 运行、事件和事务内入队意图。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_api_runs"
down_revision = "0001_sessions_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column(
            "thread_id", sa.Text(), sa.ForeignKey("sessions.thread_id"), nullable=False
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("model_config", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "dispatch_pending", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("interrupts", JSONB(), nullable=False, server_default="[]"),
        sa.Column("resume_values", JSONB(), nullable=False, server_default="{}"),
        sa.Column("answer", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("last_event_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_approval','waiting_input',"
            "'succeeded','failed','cancelled')",
            name="agent_runs_status",
        ),
    )
    # 等待审批或失败的 checkpoint 未解决前，不接受同一会话的新运行。
    op.create_index(
        "agent_runs_active_thread",
        "agent_runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('succeeded','cancelled')"),
    )
    op.create_index("agent_runs_dispatch", "agent_runs", ["dispatch_pending"])
    op.create_table(
        "agent_run_events",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_run_events")
    op.drop_table("agent_runs")
