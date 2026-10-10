"""独立的配置、法律数据和重排模型初始化入口。"""

import argparse
import os
import sys
import tarfile
from pathlib import Path

import httpx

from ai_agent_learning.config import ConfigStore
from ai_agent_learning.paths import get_data_dir


def initialize_config(directory: Path | None = None) -> Path:
    """创建缺失的 JSON、迁移旧配置并校验已有文件，不要求模型 Key。"""
    store = ConfigStore(directory)
    store.load()
    return store.directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="配置目录；默认遵循 LLM_CLI_CONFIG_DIR 和 LLM_CLI_DATA_DIR",
    )
    parser.add_argument("--data-dir", type=Path, help="法律索引和下载缓存目录")
    parser.add_argument("--model-dir", type=Path, help="重排模型目录")
    parser.add_argument(
        "--laws",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("LAWS_INIT_ENABLED", "false").lower() in {"true", "1", "yes"},
        help="初始化法律库；Compose 默认启用，可用 --no-laws 跳过",
    )
    parser.add_argument(
        "--reranker",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("RERANKER_INIT_ENABLED", "false").lower()
        in {"true", "1", "yes"},
        help="检查并下载重排模型；Compose 默认启用，可用 --no-reranker 跳过",
    )
    args = parser.parse_args()
    try:
        directory = initialize_config(args.config_dir)
    except (OSError, UnicodeError, ValueError):
        # 不输出异常正文，避免无效配置中的密钥进入容器日志。
        print("配置初始化失败，请检查 JSON 格式与配置目录权限。", file=sys.stderr)
        return 1
    print(f"配置初始化完成：{directory}")
    print(
        "models.json、mouldes.json、secrets.json、permissions.json 已就绪；已有配置保留。"
    )
    print("模型 API Key 可随后通过网页设置或 agent100 config 填写。")
    if args.laws or args.reranker:
        from ai_agent_learning.initialize_resources import (
            ResourceInitializationError,
            initialize_laws,
            initialize_reranker,
            resource_lock,
        )

        stage = "共享资源"
        try:
            data_dir = args.data_dir or get_data_dir()
            with resource_lock(data_dir):
                if args.laws:
                    stage = "法律数据"
                    initialize_laws(data_dir)
                if args.reranker:
                    stage = "重排模型"
                    initialize_reranker(args.model_dir)
        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            httpx.HTTPError,
            tarfile.TarError,
        ) as error:
            # 外部 HTTP 异常可能包含认证 URL；只输出阶段和异常类型。
            print(
                f"{stage}初始化失败（{type(error).__name__}），请检查网络、Qdrant 和共享目录；"
                "再次运行可复用下载缓存。",
                file=sys.stderr,
            )
            if isinstance(error, ResourceInitializationError):
                print(str(error), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
