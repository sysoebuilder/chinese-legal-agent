import argparse
import asyncio

from ai_agent_learning.langchain_agent.run import run
from ai_agent_learning.main import run_config, run_law_install


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent100",
        description="个人 LLM 命令行助手",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="agent100 0.5.0",
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "config",
        help="选择提供商、输入模型名称并更新当前配置",
    )

    commands.add_parser(
        "chat",
        help="进入 LangChain/LangGraph 多轮会话",
    )

    laws_parser = commands.add_parser(
        "laws",
        help="管理离线法规知识库",
    )
    laws_commands = laws_parser.add_subparsers(
        dest="laws_command",
        required=True,
    )
    laws_commands.add_parser(
        "install",
        help="将随安装包提供的原始法规和向量安装到 Qdrant",
    )

    return parser


def main() -> None:
    args = create_parser().parse_args()

    try:
        if args.command == "config":
            run_config()

        elif args.command == "chat":
            asyncio.run(run())

        elif args.command == "laws" and args.laws_command == "install":
            run_law_install()

    except KeyboardInterrupt:
        print("\n已退出")
