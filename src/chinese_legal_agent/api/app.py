"""创建运行、查询状态、恢复中断和可重连 SSE。"""

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Protocol
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from chinese_legal_agent.api.config import router as config_router
from chinese_legal_agent.api.events import RedisEventBus, as_sse
from chinese_legal_agent.api.schemas import (
    TERMINAL_STATUSES,
    CreateRun,
    HistoryPage,
    RecoveryView,
    ResumeRun,
    RunView,
    SessionPage,
)
from chinese_legal_agent.api.settings import ApiSettings
from chinese_legal_agent.api.store import (
    PostgresRunStore,
    RunConflict,
    RunNotFound,
    RunStore,
)
from chinese_legal_agent.config import ConfigStore
from chinese_legal_agent.database import get_database_url, migrate_database
from chinese_legal_agent.model_config import ModelConfig

logger = logging.getLogger(__name__)


class RunPublisher(Protocol):
    def publish(self, run_id: str, generation: int) -> None: ...


class DisconnectMonitor(Protocol):
    async def is_disconnected(self) -> bool: ...


async def stream_events(
    store: RunStore,
    request: DisconnectMonitor,
    run_id: str,
    event_bus,
    heartbeat_interval: float,
) -> AsyncIterator[str]:
    if await request.is_disconnected():
        return
    # 已完成任务不依赖 Redis；刷新仍能读取持久化的最终结果。
    try:
        row = await asyncio.to_thread(store.get, run_id)
    except RunNotFound:
        return
    if row["status"] in TERMINAL_STATUSES:
        yield as_sse("run.state", PostgresRunStore.view(row).model_dump(mode="json"))
        return
    async with event_bus.subscribe(run_id) as subscription:
        try:
            row = await asyncio.to_thread(store.get, run_id)
        except RunNotFound:
            return
        state = PostgresRunStore.view(row).model_dump(mode="json")
        yield as_sse("run.state", state)
        deadline = asyncio.get_running_loop().time() + heartbeat_interval
        while state["status"] not in TERMINAL_STATUSES:
            if await request.is_disconnected():
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                # 低频校准覆盖提交后进程崩溃、通知丢失；不是逐片段查表。
                try:
                    row = await asyncio.to_thread(store.get, run_id)
                except RunNotFound:
                    return
                latest = PostgresRunStore.view(row).model_dump(mode="json")
                if latest["revision"] > state["revision"]:
                    state = latest
                    yield as_sse("run.state", state)
                yield ": ping\n\n"
                deadline = asyncio.get_running_loop().time() + heartbeat_interval
                continue
            message = await subscription.receive(min(remaining, 1.0))
            if message is None:
                continue
            event, data = message["event"], message["data"]
            if event == "run.state":
                if data["revision"] <= state["revision"]:
                    continue
                state = data
            elif (
                data.get("generation") != state["generation"]
                or state["status"] != "running"
                or state["cancel_requested"]
            ):
                continue
            yield as_sse(event, data)


async def dispatch_loop(
    store: RunStore,
    publisher: RunPublisher,
    stopped: asyncio.Event,
    interval: float,
) -> None:
    from chinese_legal_agent.api.celery_app import dispatch_pending

    while not stopped.is_set():
        try:
            await asyncio.to_thread(dispatch_pending, store, publisher)
        except Exception:  # noqa: BLE001 -- 交付失败时保留持久入队意图。
            # 不输出 Redis URL、数据库 URL 或异常正文中的认证信息。
            logger.warning("任务尚未投递，保留入队意图，稍后重试")
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from chinese_legal_agent.api.celery_app import CeleryPublisher

    app.state.app_config = await asyncio.to_thread(app.state.config_store.load)
    event_bus = app.state.event_bus
    if app.state.configured_store is None:
        database_url = get_database_url()
        await asyncio.to_thread(migrate_database, database_url)
        app.state.store = PostgresRunStore(database_url, event_bus)
    else:
        app.state.store = app.state.configured_store

    sender = app.state.publisher or CeleryPublisher()
    stopped = asyncio.Event()
    dispatcher = asyncio.create_task(
        dispatch_loop(
            app.state.store, sender, stopped, app.state.settings.dispatch_interval
        )
    )
    try:
        yield
    finally:
        stopped.set()
        await dispatcher
        if app.state.owns_event_bus:
            await asyncio.to_thread(event_bus.close)


def authorized(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = request.app.state.settings.token
    if not token:
        return
    expected = f"Bearer {token}"
    if authorization is None or not hmac.compare_digest(
        authorization.encode(), expected.encode()
    ):
        raise HTTPException(
            401,
            "需要有效的 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def missing(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "运行或会话不存在"})


async def conflict(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


async def unavailable(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用"})


router = APIRouter(dependencies=[Depends(authorized)])


@router.get("/v1/sessions", response_model=SessionPage)
async def sessions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionPage:
    """获取可选择的会话；后续创建 run 时传入选中的 thread_id。"""
    return await asyncio.to_thread(request.app.state.store.sessions, limit, offset)


@router.delete("/v1/sessions/{thread_id}", status_code=204, response_class=Response)
async def delete_session(thread_id: UUID, request: Request) -> Response:
    """按 thread_id 删除会话及其全部运行和 checkpoint。"""
    await asyncio.to_thread(request.app.state.store.delete_session, str(thread_id))
    return Response(status_code=204)


@router.get("/v1/sessions/{thread_id}/messages", response_model=HistoryPage)
async def history(
    thread_id: UUID,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
    before: Annotated[str | None, Query(min_length=1)] = None,
) -> HistoryPage:
    """读取终端与 API 共用的历史，最多 10 轮/20 条消息。"""
    try:
        return await asyncio.to_thread(
            request.app.state.store.history,
            str(thread_id),
            limit,
            before,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post("/v1/runs", response_model=RunView, status_code=202)
async def create_run(body: CreateRun, request: Request) -> RunView:
    key_ref = None
    permission_mode = None
    try:
        if request.app.state.model_loader is not None:
            config = request.app.state.model_loader()
        else:
            app_config = await asyncio.to_thread(request.app.state.config_store.load)
            request.app.state.app_config = app_config
            config = app_config.active_model()
            key_ref = app_config.modelconfig.models[app_config.modelconfig.active].api_key_ref
            permission_mode = app_config.permissionsconfig.mode
    except (SystemExit, ValueError, OSError):
        raise HTTPException(503, "请先在网页设置中配置模型和 API Key") from None
    public_config = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
    }
    if key_ref is not None:
        public_config["api_key_ref"] = key_ref
    if permission_mode is not None:
        # 暂停后的恢复也使用提交时的审批模式，避免设置变更绕过拒绝决定。
        public_config["permission_mode"] = permission_mode
    row = await asyncio.to_thread(
        request.app.state.store.create,
        body.message,
        str(body.thread_id) if body.thread_id is not None else None,
        public_config,
    )
    return PostgresRunStore.view(row)


@router.get("/v1/runs/{run_id}", response_model=RunView)
async def get_run(run_id: UUID, request: Request) -> RunView:
    row = await asyncio.to_thread(request.app.state.store.get, str(run_id))
    return PostgresRunStore.view(row)


@router.get("/v1/sessions/{thread_id}/recovery", response_model=RecoveryView)
async def recovery(thread_id: UUID, request: Request) -> RecoveryView:
    """查询未解决的 API 任务；执行锁已释放且存在 checkpoint 时允许重连。"""
    return await asyncio.to_thread(request.app.state.store.recovery, str(thread_id))


@router.post("/v1/runs/{run_id}/reconnect", response_model=RunView, status_code=202)
async def reconnect_run(run_id: UUID, request: Request) -> RunView:
    """从已有断点继续，不添加消息、不默认批准工具操作。"""
    row = await asyncio.to_thread(request.app.state.store.reconnect, str(run_id))
    return PostgresRunStore.view(row)


@router.post("/v1/runs/{run_id}/resume", response_model=RunView, status_code=202)
async def resume_run(run_id: UUID, body: ResumeRun, request: Request) -> RunView:
    try:
        row = await asyncio.to_thread(
            request.app.state.store.resume, str(run_id), body.responses
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return PostgresRunStore.view(row)


@router.post("/v1/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(run_id: UUID, request: Request) -> RunView:
    row = await asyncio.to_thread(request.app.state.store.cancel, str(run_id))
    return PostgresRunStore.view(row)


@router.get("/v1/runs/{run_id}/events")
async def events(
    run_id: UUID,
    request: Request,
) -> StreamingResponse:
    store = request.app.state.store
    await asyncio.to_thread(store.get, str(run_id))
    return StreamingResponse(
        stream_events(
            store,
            request,
            str(run_id),
            request.app.state.event_bus,
            request.app.state.settings.heartbeat_interval,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def create_app(
    *,
    store: RunStore | None = None,
    publisher: RunPublisher | None = None,
    settings: ApiSettings | None = None,
    event_bus=None,
    model_loader: Callable[[], ModelConfig] | None = None,
    config_store: ConfigStore | None = None,
) -> FastAPI:
    api_settings = settings or ApiSettings.from_env()
    app = FastAPI(title="中国法律智能体 Run API", version="v1", lifespan=lifespan)
    app.state.settings = api_settings
    app.state.configured_store = store
    app.state.owns_event_bus = event_bus is None
    app.state.event_bus = event_bus or RedisEventBus(api_settings.redis_url)
    if isinstance(store, PostgresRunStore):
        store.event_bus = app.state.event_bus
    app.state.publisher = publisher
    app.state.model_loader = model_loader
    app.state.config_store = config_store or ConfigStore()
    app.add_exception_handler(RunNotFound, missing)
    app.add_exception_handler(RunConflict, conflict)
    app.add_exception_handler(psycopg.Error, unavailable)
    app.include_router(router)
    app.include_router(config_router, dependencies=[Depends(authorized)])
    return app


app = create_app()
