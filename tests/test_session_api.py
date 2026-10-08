"""会话选择和共享 checkpoint 历史的分页与访问边界。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ai_agent_learning.api.app import create_app
from ai_agent_learning.api.history import history_page
from ai_agent_learning.api.schemas import SessionPage, SessionView
from ai_agent_learning.api.settings import ApiSettings
from ai_agent_learning.api.store import RunNotFound


def turns(count):
    messages = []
    for index in range(count):
        messages.extend(
            [
                HumanMessage(id=f"user-{index}", content=f"question-{index}"),
                AIMessage(id=f"assistant-{index}", content=f"answer-{index}"),
            ]
        )
    return messages


def test_history_recovers_compacted_turns_without_duplicates_or_summary():
    thread_id = str(uuid4())
    original = turns(13)
    compacted = [
        HumanMessage(
            id="summary",
            content="internal summary",
            additional_kwargs={"lc_source": "summarization"},
        ),
        *original[-6:],
    ]
    snapshots = [compacted, original, original[:-2]]
    latest = history_page(thread_id, snapshots, 10, None)
    assert len(latest.messages) == 20
    assert latest.messages[0].id == "user-3"
    assert latest.messages[-1].id == "assistant-12"
    assert latest.has_more and latest.next_before == "user-3"
    older = history_page(thread_id, snapshots, 10, latest.next_before)
    assert [message.id for message in older.messages] == [m.id for m in original[:6]]
    assert not older.has_more and older.next_before is None
    with pytest.raises(ValueError, match="before"):
        history_page(thread_id, snapshots, 10, "different-session-user")


def test_history_filters_tool_roundtrips_and_keeps_unanswered_user():
    messages = [
        SystemMessage(content="secret system prompt"),
        HumanMessage(id="u1", content="question"),
        AIMessage(
            id="tool",
            content="checking",
            tool_calls=[
                {
                    "name": "lookup",
                    "args": {},
                    "id": "tool-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(id="result", content="secret result", tool_call_id="tool-1"),
        AIMessage(id="a1", content="initial explanation"),
        AIMessage(id="a2", content="final answer"),
        HumanMessage(id="u2", content="pending question"),
    ]
    page = history_page(str(uuid4()), [messages], 10, None)
    assert [message.id for message in page.messages] == ["u1", "a2", "u2"]
    assert [message.role for message in page.messages] == ["user", "assistant", "user"]


class SessionStore:
    def __init__(self):
        self.thread_id = str(uuid4())
        now = datetime.now(UTC)
        self.session = SessionView(
            thread_id=self.thread_id,
            name="shared conversation",
            created_at=now,
            updated_at=now,
        )

    def pending_dispatches(self):
        return []

    def sessions(self, limit, offset):
        return SessionPage(
            sessions=[self.session] if offset == 0 else [], has_more=False
        )

    def history(self, thread_id, limit, before):
        if thread_id != self.thread_id:
            raise RunNotFound(thread_id)
        return history_page(thread_id, [turns(12)], limit, before)


def test_session_routes_pagination_validation_and_authentication():
    store = SessionStore()
    app = create_app(
        store=store,
        publisher=SimpleNamespace(publish=lambda *_: None),
        settings=ApiSettings(token="test-token", dispatch_interval=0.01),
        model_loader=lambda: pytest.fail("Reading history must not load a model"),
    )
    path = f"/v1/sessions/{store.thread_id}/messages"
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        assert client.get("/v1/sessions").status_code == 401
        assert client.get(path).status_code == 401
        response = client.get("/v1/sessions", headers=headers)
        assert response.json()["sessions"][0]["thread_id"] == store.thread_id
        assert (
            client.get("/v1/sessions?offset=1", headers=headers).json()["sessions"]
            == []
        )
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        page = response.json()
        assert len(page["messages"]) == 20
        older = client.get(
            path, params={"before": page["next_before"]}, headers=headers
        )
        assert len(older.json()["messages"]) == 4
        for params in [{"limit": 11}, {"limit": 0}, {"before": "unknown"}]:
            assert client.get(path, params=params, headers=headers).status_code == 422
        assert (
            client.get(f"/v1/sessions/{uuid4()}/messages", headers=headers).status_code
            == 404
        )


def test_empty_checkpoint_history():
    page = history_page(str(uuid4()), [], 10, None)
    assert page.messages == [] and not page.has_more and page.next_before is None
