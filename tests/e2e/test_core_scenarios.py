"""对已启动 Compose 栈执行的核心 E2E；测试自身不替换下游依赖。"""

import os
import asyncio
import json
import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import websockets

from app.core.security import create_access_token
from app.core.database import async_session, engine
from app.core.config import settings
from app.models import Conversation, Handoff
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from scripts.seed_data import DEMO_CONVERSATION_ID, DEMO_OTHER_USER_ID, DEMO_TENANT_ID, DEMO_USER_ID, seed

API_URL = os.getenv("API_URL", "http://api:8000")
WS_URL = os.getenv("WS_URL", "ws://api:8000")


def auth_headers():
    token = create_access_token(tenant_id=str(DEMO_TENANT_ID), user_id=str(DEMO_USER_ID), role="user")
    return {"Authorization": f"Bearer {token}"}


async def _ensure_ai_owns_demo_conversation() -> None:
    """Keep persistent local demo state from leaking into AI-path E2E cases."""
    local_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    local_session = async_sessionmaker(local_engine, expire_on_commit=False)
    async with local_session() as session:
        await session.execute(
            update(Handoff)
            .where(
                Handoff.tenant_id == DEMO_TENANT_ID,
                Handoff.conversation_id == DEMO_CONVERSATION_ID,
                Handoff.active_key == "active",
            )
            .values(status="closed", active_key=None)
        )
        await session.commit()
    await local_engine.dispose()


async def test_e2e_01_and_10_knowledge_grounding():
    async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=10) as client:
        found = await client.post("/knowledge/query", json={"question": "如何申请退费？"})
        assert found.status_code == 200
        assert found.json()["evidence_level"] == "SUPPORTED"
        assert found.json()["citations"]
        missing = await client.post("/knowledge/query", json={"question": "今天天气怎么样？"})
        assert missing.status_code == 200
        assert missing.json()["evidence_level"] == "NONE"
        assert missing.json()["citations"] == []


async def test_e2e_02_invoice_is_masked():
    async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=10) as client:
        response = await client.get("/finance/invoice")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["data"]["order_id"] == "XH-2026-0901"
        assert body["data"]["email"] == "l***@xinghe-future.example.com"


async def test_e2e_09_duplicate_message_has_one_reply():
    payload = {"message_id": "e2e-duplicate-001", "tenant_id": str(DEMO_TENANT_ID), "user_id": str(DEMO_USER_ID), "conversation_id": str(DEMO_CONVERSATION_ID), "content": "你好"}
    async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=10) as client:
        first = await client.post("/webhooks/im/messages", json=payload)
        second = await client.post("/webhooks/im/messages", json=payload)
        assert first.status_code == second.status_code == 202
        assert {first.json()["status"], second.json()["status"]} <= {"accepted", "duplicate"}


async def _wait_for_push(predicate, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    async with httpx.AsyncClient(base_url="http://mock-im:8100", timeout=5) as client:
        while asyncio.get_running_loop().time() < deadline:
            pushes = (await client.get("/pushes")).json()["pushes"]
            match = next((item for item in pushes if predicate(item)), None)
            if match:
                return match
            await asyncio.sleep(0.25)
    raise AssertionError("expected mock IM push was not observed")


async def test_e2e_05_short_reminder_reaches_mock_im():
    marker = "e2e-short-reminder"
    run_at = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=10) as client:
        created = await client.post("/reminders", json={"conversation_id": str(DEMO_CONVERSATION_ID), "content": marker, "run_at_local": run_at, "timezone": "Asia/Shanghai", "repeat": "once"})
        assert created.status_code == 201
        reminder_id = created.json()["id"]
    pushed = await _wait_for_push(lambda item: item.get("reminder_id") == reminder_id)
    assert pushed["content"] == marker


async def test_e2e_06_handoff_contains_sanitized_context():
    conversation_id = os.urandom(16).hex()
    # A handoff is unique while active; use a fresh owned conversation so this
    # scenario remains repeatable even when the Compose database is persistent.
    import uuid
    conversation_uuid = uuid.UUID(conversation_id)
    async with async_session() as session:
        session.add(Conversation(id=conversation_uuid, tenant_id=DEMO_TENANT_ID, user_id=DEMO_USER_ID))
        await session.commit()
    await engine.dispose()
    async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=10) as client:
        response = await client.post("/handoffs", json={"conversation_id": str(conversation_uuid), "summary": "parent@example.com 要求人工", "attempted_actions": ["knowledge"]})
        assert response.status_code == 202
        handoff_id = response.json()["id"]
        assert "parent@example.com" not in response.json()["summary"]
    pushed = await _wait_for_push(lambda item: item.get("handoff_id") == handoff_id)
    assert pushed["context"]["attempted_actions"] == ["knowledge"]


async def test_websocket_authenticated_message_and_reply():
    await _ensure_ai_owns_demo_conversation()
    uri = f"{WS_URL}/ws?conversation_id={DEMO_CONVERSATION_ID}"
    async with websockets.connect(uri, additional_headers=auth_headers(), open_timeout=5) as ws:
        message_id = f"ws-{os.urandom(6).hex()}"
        await ws.send(json.dumps({"type": "message.send", "payload": {"message_id": message_id, "content": "你好"}}, ensure_ascii=False))
        accepted = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        assert accepted["type"] == "message.accepted"
        event_types = []
        deltas = []
        final_content = None
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
            if event.get("payload", {}).get("in_reply_to") != message_id:
                continue
            event_types.append(event.get("type"))
            if event.get("type") == "reply.chunk":
                deltas.append(event["payload"]["delta"])
            if event.get("type") == "reply.end":
                final_content = event["payload"]["content"]
                break
        assert event_types[0] == "reply.start"
        assert "reply.chunk" in event_types
        assert event_types[-1] == "reply.end"
        assert "".join(deltas) == final_content

    resume_uri = f"{uri}&after_message_id={message_id}"
    async with websockets.connect(resume_uri, additional_headers=auth_headers(), open_timeout=5) as ws:
        resumed = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        assert resumed["type"] == "connection.resumed"
        assert any(item["role"] == "assistant" for item in resumed["payload"]["messages"])


async def test_e2e_03_cross_user_finance_is_denied_without_downstream_call():
    await seed()
    attacker = create_access_token(tenant_id=str(DEMO_TENANT_ID), user_id=str(DEMO_OTHER_USER_ID), role="user")
    async with httpx.AsyncClient(timeout=5) as raw:
        before = (await raw.get("http://mock-finance:8104/calls")).json()["count"]
    async with httpx.AsyncClient(base_url=API_URL, headers={"Authorization": f"Bearer {attacker}"}, timeout=5) as client:
        response = await client.get(f"/finance/invoice?target_user_id={DEMO_USER_ID}")
    async with httpx.AsyncClient(timeout=5) as raw:
        after = (await raw.get("http://mock-finance:8104/calls")).json()["count"]
    assert response.status_code == 403
    assert after == before


async def test_e2e_04_auto_renew_requires_confirmation_and_is_idempotent():
    async with httpx.AsyncClient(timeout=5) as raw:
        before = (await raw.get("http://mock-platform:8103/calls")).json()["count"]
    async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=10) as client:
        proposed = await client.post("/commands/close-auto-renew", json={"conversation_id": str(DEMO_CONVERSATION_ID), "resource_id": f"subscription-{os.urandom(4).hex()}"})
        assert proposed.status_code == 202
        async with httpx.AsyncClient(timeout=5) as raw:
            assert (await raw.get("http://mock-platform:8103/calls")).json()["count"] == before
        confirmation_id = proposed.json()["confirmation_id"]
        first = await client.post(f"/commands/confirm/{confirmation_id}")
        second = await client.post(f"/commands/confirm/{confirmation_id}")
        assert first.json()["execution_id"] == second.json()["execution_id"]
    async with httpx.AsyncClient(timeout=5) as raw:
        assert (await raw.get("http://mock-platform:8103/calls")).json()["count"] == before + 1


async def test_e2e_07_finance_timeout_never_invents_data():
    async with httpx.AsyncClient(timeout=5) as raw:
        await raw.put("http://mock-finance:8104/control", json={"delay_ms": 3500})
        try:
            async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=20) as client:
                response = await client.get("/finance/invoice")
            assert response.status_code == 200
            assert response.json()["status"] == "unavailable"
            assert "amount" not in response.json()
        finally:
            await raw.put("http://mock-finance:8104/control", json={})


async def test_e2e_08_invalid_llm_json_is_safe_and_calls_no_tool():
    await _ensure_ai_owns_demo_conversation()
    message_id = f"invalid-llm-{os.urandom(5).hex()}"
    reply_id = "reply-" + hashlib.sha256(f"{DEMO_TENANT_ID}:{message_id}".encode()).hexdigest()[:48]
    async with httpx.AsyncClient(timeout=5) as raw:
        before = (await raw.get("http://mock-platform:8103/calls")).json()["count"]
        await raw.put("http://mock-llm:8101/control", json={"body": "not-json"})
        try:
            # "你好" now uses the deterministic high-volume fast path; use a
            # non-fast chitchat phrase to exercise malformed provider output.
            payload = {"message_id": message_id, "tenant_id": str(DEMO_TENANT_ID), "user_id": str(DEMO_USER_ID), "conversation_id": str(DEMO_CONVERSATION_ID), "content": "你是谁"}
            async with httpx.AsyncClient(base_url=API_URL, headers=auth_headers(), timeout=5) as client:
                assert (await client.post("/webhooks/im/messages", json=payload)).status_code == 202
                deadline = asyncio.get_running_loop().time() + 8
                while asyncio.get_running_loop().time() < deadline:
                    messages = (await client.get(f"/conversations/{DEMO_CONVERSATION_ID}/messages")).json()["messages"]
                    if any(item["message_id"] == reply_id and item["content"] == "智能回复暂时不可用，请稍后重试或回复“转人工”。" for item in messages):
                        break
                    await asyncio.sleep(0.25)
                else:
                    raise AssertionError("safe LLM fallback reply was not persisted")
        finally:
            await raw.put("http://mock-llm:8101/control", json={})
        after = (await raw.get("http://mock-platform:8103/calls")).json()["count"]
    assert after == before
