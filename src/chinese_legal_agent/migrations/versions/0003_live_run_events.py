"""流式片段改为临时推送，运行状态版本替代事件游标。"""

from alembic import op

revision = "0003_live_run_events"
down_revision = "0002_api_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("agent_run_events")
    op.alter_column("agent_runs", "last_event_id", new_column_name="revision")


def downgrade() -> None:
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB

    op.alter_column("agent_runs", "revision", new_column_name="last_event_id")
    # 旧的流式片段不恢复；只还原旧版本所需的结构。
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
