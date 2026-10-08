"""python -m ai_agent_learning.api，默认仅监听本机。"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="agent100 Run API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run("ai_agent_learning.api.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
