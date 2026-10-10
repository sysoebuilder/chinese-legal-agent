"""应用配置查询与局部修改；凭据只返回配置状态。"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/v1/config", tags=["config"])
Section = Literal["models", "mouldes", "secrets", "permissions"]
FIELDS = {
    "models": "modelconfig",
    "mouldes": "mouldesconfig",
    "secrets": "secretsconfig",
    "permissions": "permissionsconfig",
}


async def read_config(request: Request):
    try:
        config = await asyncio.to_thread(request.app.state.config_store.load)
    except (ValueError, OSError):
        raise HTTPException(503, "配置文件无效或暂时不可读") from None
    request.app.state.app_config = config
    return config


@router.get("")
async def get_config(request: Request) -> dict[str, Any]:
    return (await read_config(request)).public_view()


@router.get("/{section}")
async def get_section(section: Section, request: Request) -> dict[str, Any]:
    return (await read_config(request)).public_view()[FIELDS[section]]


@router.patch("/{section}")
async def patch_section(
    section: Section, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    """递归合并指定字段；secrets.keys 中的 null 删除对应 Key。"""
    try:
        config = await asyncio.to_thread(
            request.app.state.config_store.update, section, body
        )
    except ValueError:
        # 不返回 Pydantic errors 中可能携带的密钥输入。
        raise HTTPException(
            422, "配置字段无效，请检查字段名称、类型和连接参数"
        ) from None
    except OSError:
        raise HTTPException(503, "配置暂时不可保存") from None
    request.app.state.app_config = config
    return config.public_view()[FIELDS[section]]
