import uuid
from unittest.mock import AsyncMock

import pytest

from app.workers import message_worker


class SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def envelope():
    return {
        "trace_id": "trace-test",
        "payload": {
            "tenant_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "message_id": "message-test",
            "content": "你好",
        },
    }


async def test_context_is_written_only_after_postgres_reply_commit(monkeypatch):
    monkeypatch.setattr(message_worker, "async_session", lambda: SessionContext())
    monkeypatch.setattr(
        message_worker.conversation_service,
        "mark_processing",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(message_worker, "generate_reply", AsyncMock(return_value="回复"))
    monkeypatch.setattr(message_worker.handoff_service, "get_active", AsyncMock(return_value=None))
    save_reply = AsyncMock(side_effect=RuntimeError("postgres commit failed"))
    monkeypatch.setattr(message_worker.conversation_service, "save_reply", save_reply)
    append_many = AsyncMock()
    monkeypatch.setattr(message_worker.conversation_context, "append_many", append_many)

    with pytest.raises(RuntimeError, match="postgres commit failed"):
        await message_worker._process_envelope(envelope())

    append_many.assert_not_awaited()


async def test_context_receives_persisted_user_and_assistant_messages(monkeypatch):
    monkeypatch.setattr(message_worker, "async_session", lambda: SessionContext())
    monkeypatch.setattr(
        message_worker.conversation_service,
        "mark_processing",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(message_worker, "generate_reply", AsyncMock(return_value="回复"))
    monkeypatch.setattr(message_worker.handoff_service, "get_active", AsyncMock(return_value=None))
    monkeypatch.setattr(
        message_worker.conversation_service, "save_reply", AsyncMock(return_value=None)
    )
    append_many = AsyncMock(return_value=True)
    monkeypatch.setattr(message_worker.conversation_context, "append_many", append_many)
    event = envelope()

    assert await message_worker._process_envelope(event) == "processed"

    args = append_many.await_args.args
    assert args[0] == event["payload"]["tenant_id"]
    assert args[1] == event["payload"]["conversation_id"]
    assert args[2][0] == {
        "message_id": "message-test",
        "role": "user",
        "content": "你好",
    }
    assert args[2][1]["role"] == "assistant"
    assert args[2][1]["content"] == "回复"


async def test_active_handoff_routes_to_agent_without_calling_llm(monkeypatch):
    monkeypatch.setattr(message_worker, "async_session", lambda: SessionContext())
    monkeypatch.setattr(message_worker.conversation_service, "mark_processing", AsyncMock(return_value=True))
    monkeypatch.setattr(message_worker.handoff_service, "get_active", AsyncMock(return_value=object()))
    complete = AsyncMock(return_value=None)
    generate = AsyncMock(return_value="不应生成")
    append_many = AsyncMock(return_value=True)
    monkeypatch.setattr(message_worker.conversation_service, "complete_without_reply", complete)
    monkeypatch.setattr(message_worker, "generate_reply", generate)
    monkeypatch.setattr(message_worker.conversation_context, "append_many", append_many)

    assert await message_worker._process_envelope(envelope()) == "routed_to_agent"
    generate.assert_not_awaited()
    complete.assert_awaited_once()
    assert len(append_many.await_args.args[2]) == 1
