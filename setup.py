"""构建包含 Qdrant 与完整离线法规数据的 Windows x64 wheel。"""

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent


class BuildWithOfflineLaws(build_py):
    """构建时把根目录的法规发布数据复制进 Python 包。"""

    def run(self) -> None:
        super().run()
        source = ROOT / "laws"
        target = Path(self.build_lib) / "ai_agent_learning" / "laws"
        required = (
            source / "raw" / "npc",
            source / "npc-law-vectors.zip",
            source / "npc-law-vectors.zip.sha256",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(f"缺少离线法规安装数据：{', '.join(missing)}")
        shutil.copytree(source / "raw", target / "raw", dirs_exist_ok=True)
        shutil.copy2(source / "npc-law-vectors.zip", target)
        shutil.copy2(source / "npc-law-vectors.zip.sha256", target)


class WindowsWheel(bdist_wheel):
    def finalize_options(self) -> None:
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self) -> tuple[str, str, str]:
        return "py3", "none", "win_amd64"


setup(cmdclass={"build_py": BuildWithOfflineLaws, "bdist_wheel": WindowsWheel})
