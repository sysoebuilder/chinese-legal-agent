"""PowerShell 命令工具及 LangChain 注册。"""

import asyncio
import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from chinese_legal_agent.langchain_agent.tools.risk import RiskLevel
from chinese_legal_agent.langchain_agent.tools.run_command.windows_job import WindowsJob

TOOL_DESCRIPTION = (
    "在指定工作目录中执行 PowerShell 命令。命令可能修改文件或系统状态，有依赖的命令应按顺序执行。返回退出码、标准输出、错误输出和超时"
    "状态；必须检查 exit_code 和 timed_out 判断命令是否成功。每路输出最多保留 64 KiB。不支持交互输入或后台常驻进程；"
    "调用结束会清理子进程。超时不撤销已发生的修改。"
)
TOOL_METADATA = {
    "risk_level": RiskLevel.HIGH.value,
    "has_side_effects": True,
    "supports_idempotent_retry": False,
    "manages_own_timeout": True,
}


OUTPUT_LIMIT_BYTES = 64 * 1024


MAX_TIMEOUT_SECONDS = 300


async def handler(
    command: str,
    working_directory: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("run_command 当前仅支持 Windows")
    if not command.strip():
        raise ValueError("command 不能为空")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise TypeError("timeout_seconds 必须是整数")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds 必须在 1 到 300 秒之间")
    directory = Path(working_directory)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("working_directory 必须是存在的目录绝对路径")
    shell = shutil.which("powershell.exe")
    if shell is None:
        raise RuntimeError("未找到 powershell.exe")

    # 用户命令经标准输入传入。在加入 Job Object 前，PowerShell 只等待输入，
    # 避免进程启动后、加入作业前抢先创建不受管理的子进程。
    bootstrap = (
        "$payload = [Console]::In.ReadLine(); "
        "if ($null -eq $payload) { exit 1 }; "
        "$script = [Text.Encoding]::Unicode.GetString("
        "[Convert]::FromBase64String($payload)); "
        "& ([ScriptBlock]::Create($script))"
    )
    script = (
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[Text.UTF8Encoding]::new($false)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$global:LASTEXITCODE = 0\n"
        "try {\n"
        "    & {\n" + command + "\n    }\n"
        "    if (-not $?) { exit 1 }\n"
        "    exit $LASTEXITCODE\n"
        "} catch {\n"
        "    [Console]::Error.WriteLine(($_ | Out-String))\n"
        "    exit 1\n"
        "}\n"
    )
    payload = base64.b64encode(script.encode("utf-16-le")) + b"\n"
    job = WindowsJob()
    process = None
    readers: list[asyncio.Task[None]] = []
    stdout = bytearray()
    stderr = bytearray()
    truncated = [False, False]
    timed_out = False
    # shield 确保取消发生在启动过程中时，仍能取得进程并清理。
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            bootstrap,
            cwd=str(directory),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    )
    try:
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            process = await launch
            raise
        job.assign(process.pid)
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            asyncio.create_task(_drain(process.stdout, stdout, truncated, 0)),
            asyncio.create_task(_drain(process.stderr, stderr, truncated, 1)),
        ]
        try:
            async with asyncio.timeout(timeout_seconds):
                process.stdin.write(payload)
                await process.stdin.drain()
                process.stdin.close()
                await process.wait()
        except TimeoutError:
            timed_out = True
    finally:
        # 关闭作业句柄终止整个进程树，包括继承 stdout 的后台子进程。
        job.close()
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
                if readers:
                    await asyncio.wait_for(asyncio.gather(*readers), timeout=5)
            finally:
                for reader in readers:
                    if not reader.done():
                        reader.cancel()
                if readers:
                    await asyncio.gather(*readers, return_exceptions=True)

    # 启动失败或取消会向外抛出异常，正常返回路径必定已经取得进程。
    assert process is not None
    return {
        "exit_code": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "timed_out": timed_out,
        "stdout_truncated": truncated[0],
        "stderr_truncated": truncated[1],
    }


async def _drain(
    stream: asyncio.StreamReader,
    buffer: bytearray,
    truncated: list[bool],
    index: int,
) -> None:
    while chunk := await stream.read(8192):
        remaining = OUTPUT_LIMIT_BYTES - len(buffer)
        buffer.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated[index] = True


class RunCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, description="要在系统 shell 中执行的完整命令。")
    working_directory: str = Field(min_length=1, description="执行命令时使用的现有工作目录路径。")
    timeout_seconds: int = Field(default=30, ge=1, le=MAX_TIMEOUT_SECONDS, description="命令超时时间，单位秒，默认 30 秒。")


run_command_tool = StructuredTool.from_function(
    coroutine=handler,
    name="run_command",
    description=TOOL_DESCRIPTION,
    args_schema=RunCommandInput,
    metadata=TOOL_METADATA,
)
