"""最简 LangChain + MCP 对话循环：python mcp_agent.py。"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from langchain.agents import create_agent
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from ai_agent_learning.langchain_agent.runtime import create_langchain_model
from ai_agent_learning.model_config import load_model_choices
from ai_agent_learning.mcp_client import list_all_tools

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="连接已启动的 HTTP MCP Server，例如 http://127.0.0.1:8000/mcp")
    parser.add_argument("--check-tools", action="store_true", help="只验证连接和工具发现，不调用模型")
    args = parser.parse_args()
    os.chdir(ROOT)
    server = args.url or StdioServerParameters(
        command=sys.executable,
        args=["-m", "ai_agent_learning.mcp_server"],
        cwd=str(ROOT),
        env=dict(os.environ),
    )
    async with Client(server) as client:
        tools = await list_all_tools(client)
        print("MCP 已连接，工具：" + ", ".join(tool.name for tool in tools))
        if args.check_tools:
            return
        deepseek_config = next(
            config for config in load_model_choices() if config.provider == "deepseek"
        )
        deepseek_config.validate()
        agent = create_agent(
            model=create_langchain_model(deepseek_config),
            tools=tools,
            system_prompt="你是法律检索测试助手。法律问题应调用 MCP 工具检索，依据结果回答；检索失败时如实说明，不得编造依据。",
        )
        messages = []
        print("输入问题开始测试，输入 exit 或 quit 退出。")
        while True:
            try:
                question = (await asyncio.to_thread(input, "\n你：")).strip()
            except EOFError:
                break
            if question.lower() in {"exit", "quit"}:
                break
            if not question:
                continue
            try:
                result = await agent.ainvoke(
                    {"messages": [*messages, {"role": "user", "content": question}]},
                    config={"recursion_limit": 30},
                )
                messages = result["messages"]
                print("\n助手：", messages[-1].content)
            except Exception as error:
                print(f"\n本轮失败：{error}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出。")
