import os
from pathlib import Path


def find_project_root() -> Path:
    """从当前目录向上查找项目根目录。"""
    current = Path.cwd().resolve()

    for directory in (current, *current.parents):
        if (directory / ".git").exists() or (directory / "pyproject.toml").is_file():
            return directory

    return current


def get_data_dir() -> Path:
    """默认使用当前项目的 data 目录，并允许环境变量覆盖。"""
    configured = os.getenv("AGENT_DATA_DIR")

    if configured:
        data_dir = Path(configured).expanduser().resolve()
    else:
        data_dir = find_project_root() / "data"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
