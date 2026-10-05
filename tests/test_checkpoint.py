"""离线验证 PostgreSQL 配置、资源清理和 Windows 线程适配。"""

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import pytest

from ai_agent_learning.langchain_agent import checkpoint as module


def test_requires_configuration_and_hides_invalid_url():
    async def scenario():
        with pytest.raises(ValueError, match="CHECKPOINT_DATABASE_URL"):
            async with module.open_checkpointer(""):
                pytest.fail("缺少数据库配置时不应启动")
        with pytest.raises(ValueError) as caught:
            async with module.open_checkpointer("secret-password invalid"):
                pytest.fail("非法连接配置不应启动")
        assert "secret-password" not in str(caught.value)

    asyncio.run(scenario())


@pytest.mark.parametrize("setup_fails", [False, True])
def test_windows_connection_setup_and_close_run_in_worker(monkeypatch, setup_fails):
    main_thread = threading.get_ident()
    events = []

    class FakeSaver:
        def setup(self):
            assert threading.get_ident() != main_thread
            events.append("setup")
            if setup_fails:
                raise RuntimeError("setup failed")

    @contextmanager
    def fake_connection(url):
        assert url == "postgresql://localhost/test"
        assert threading.get_ident() != main_thread
        events.append("open")
        try:
            yield FakeSaver()
        finally:
            assert threading.get_ident() != main_thread
            events.append("close")

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(module.ThreadedPostgresSaver, "from_conn_string", fake_connection)

    async def scenario():
        async with module.open_checkpointer("postgresql://localhost/test"):
            events.append("use")

    if setup_fails:
        with pytest.raises(RuntimeError, match="setup failed"):
            asyncio.run(scenario())
        assert events == ["open", "setup", "close"]
    else:
        asyncio.run(scenario())
        assert events == ["open", "setup", "use", "close"]


def test_threaded_saver_async_methods_do_not_block_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    saver = object.__new__(module.ThreadedPostgresSaver)
    events = []

    def record(name, result):
        def call(*args, **kwargs):
            assert threading.get_ident() != main_thread
            events.append((name, args, kwargs))
            return result
        return call

    config = {"configurable": {"thread_id": "test"}}
    monkeypatch.setattr(saver, "get_tuple", record("get", "checkpoint"))
    monkeypatch.setattr(saver, "list", record("list", iter(["first", "second"])))
    monkeypatch.setattr(saver, "put", record("put", config))
    monkeypatch.setattr(saver, "put_writes", record("writes", None))
    monkeypatch.setattr(saver, "delete_thread", record("delete", None))
    monkeypatch.setattr(saver, "get_delta_channel_history", record("history", {}))

    async def scenario():
        assert await saver.aget_tuple(config) == "checkpoint"
        assert [item async for item in saver.alist(config, limit=2)] == ["first", "second"]
        assert await saver.aput(config, {}, {}, {}) == config
        await saver.aput_writes(config, [("channel", "value")], "task", "path")
        await saver.adelete_thread("test")
        assert await saver.aget_delta_channel_history(config=config, channels=["channel"]) == {}

    asyncio.run(scenario())
    assert events[3][1] == (config, [("channel", "value")], "task", "path")
    assert events[5][2] == {"config": config, "channels": ["channel"]}


def test_other_platform_uses_official_async_saver(monkeypatch):
    events = []

    class FakeSaver:
        async def setup(self):
            events.append("setup")

    @asynccontextmanager
    async def fake_connection(url):
        assert url == "postgresql://localhost/test"
        events.append("open")
        try:
            yield FakeSaver()
        finally:
            events.append("close")

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module.AsyncPostgresSaver, "from_conn_string", fake_connection)

    async def scenario():
        async with module.open_checkpointer("postgresql://localhost/test"):
            events.append("use")

    asyncio.run(scenario())
    assert events == ["open", "setup", "use", "close"]
