"""python -m chinese_legal_agent.api，默认仅监听本机。"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="中国法律智能体 Run API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run("chinese_legal_agent.api.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
