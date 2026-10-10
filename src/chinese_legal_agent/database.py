"""PostgreSQL 配置和业务表的 Alembic 迁移。"""

import os

import psycopg
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from chinese_legal_agent.model_config import source_config_path


def validate_database_url(database_url: str) -> str:
    if not database_url.strip():
        raise ValueError("请在 data/.env 中配置 CHECKPOINT_DATABASE_URL")
    try:
        conninfo_to_dict(database_url)
    except Exception:
        raise ValueError("CHECKPOINT_DATABASE_URL 不是有效的 PostgreSQL 连接信息") from None
    return database_url


def get_database_url() -> str:
    load_dotenv(source_config_path(), override=False)
    return validate_database_url(os.getenv("CHECKPOINT_DATABASE_URL", ""))


def migrate_database(database_url: str) -> None:
    """只升级业务表；LangGraph checkpoint 表由官方 saver.setup() 管理。"""
    validate_database_url(database_url)
    config = Config()
    config.set_main_option("script_location", "chinese_legal_agent:migrations")
    # 连接信息直接交给驱动，兼容 URI 和 libpq 格式，也不写入配置/日志。
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(database_url),
        poolclass=NullPool,
    )
    try:
        with engine.begin() as connection:
            # 串行化多个聊天进程同时启动时的业务表迁移。
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(1000069)")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate_database(get_database_url())
    print("PostgreSQL 会话表和日志表已升级到最新版本")
