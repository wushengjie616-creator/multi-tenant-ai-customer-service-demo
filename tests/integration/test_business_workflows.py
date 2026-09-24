"""P3/P6/P7 持久状态、安全边界与幂等。"""

import uuid

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.database import async_session
from app.models import AuditLog, Confirmation, DeadLetter, Handoff, Message, Reminder, ToolExecution
from sqlalchemy import func, select


def headers(user):
    return {"Authorization": f"Bearer {user['token']}"}


async def test_business_creation_rejects_another_users_conversation(client, make_user, make_conversation):
    owner = await make_user()
    attacker = await make_user(tenant_id=owner["tenant_id"])
    conversation = await make_conversation(owner["tenant_id"], owner["user_id"])
    payloads = [
        ("/commands/close-auto-renew", {"conversation_id": conversation, "resource_id": attacker["user_id"]}),
        ("/reminders", {"conversation_id": conversation, "content": "上课", "run_at_local": (datetime.now() + timedelta(days=1)).isoformat(), "timezone": "Asia/Shanghai", "repeat": "once"}),
        ("/handoffs", {"conversation_id": conversation, "summary": "转人工"}),
    ]
    for path, payload in payloads:
        response = await client.post(path, json=payload, headers=headers(attacker))
        assert response.status_code == 403
    async with async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Confirmation)) == 0
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 0
        assert await session.scalar(select(func.count()).select_from(Handoff)) == 0


async def test_high_risk_command_executes_once_after_confirmation(client, make_user, make_conversation, monkeypatch):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    calls = []

    async def execute(action, payload, idempotency_key):
        calls.append((action, payload, idempotency_key))
        return {"status": "succeeded"}

    monkeypatch.setattr("app.services.command_service.platform_client.execute", execute)
    proposed = await client.post("/commands/close-auto-renew", json={"conversation_id": conversation, "resource_id": user["user_id"]}, headers=headers(user))
    assert proposed.status_code == 202
    assert calls == []
    confirmation_id = proposed.json()["confirmation_id"]
    first = await client.post(f"/commands/confirm/{confirmation_id}", headers=headers(user))
    second = await client.post(f"/commands/confirm/{confirmation_id}", headers=headers(user))
    assert first.status_code == second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert len(calls) == 1
    async with async_session() as session:
        assert await session.scalar(select(func.count()).select_from(ToolExecution)) == 1


async def test_confirmation_hash_is_bound_to_resource(client, make_user, make_conversation):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    first = await client.post("/commands/close-auto-renew", json={"conversation_id": conversation, "resource_id": "subscription-a"}, headers=headers(user))
    second = await client.post("/commands/close-auto-renew", json={"conversation_id": conversation, "resource_id": "subscription-b"}, headers=headers(user))
    async with async_session() as session:
        rows = list((await session.scalars(select(Confirmation).order_by(Confirmation.created_at))).all())
        assert first.status_code == second.status_code == 202
        assert rows[0].args_hash != rows[1].args_hash


async def test_duplicate_pending_confirmation_is_reused(client, make_user, make_conversation):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    payload = {"conversation_id": conversation, "resource_id": "membership-2026"}

    first = await client.post("/commands/close-auto-renew", json=payload, headers=headers(user))
    second = await client.post("/commands/close-auto-renew", json=payload, headers=headers(user))

    assert first.status_code == second.status_code == 202
    assert first.json()["confirmation_id"] == second.json()["confirmation_id"]
    async with async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Confirmation)) == 1


async def test_legacy_duplicate_confirmation_returns_existing_execution(client, make_user, make_conversation):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    proposed = await client.post(
        "/commands/close-auto-renew",
        json={"conversation_id": conversation, "resource_id": "membership-2026"},
        headers=headers(user),
    )
    first_id = uuid.UUID(proposed.json()["confirmation_id"])
    async with async_session() as session:
        first = await session.get(Confirmation, first_id)
        duplicate = Confirmation(
            tenant_id=first.tenant_id, user_id=first.user_id,
            conversation_id=first.conversation_id, action=first.action,
            resource_id=first.resource_id, args_hash=first.args_hash,
            arguments=first.arguments, expires_at=first.expires_at,
        )
        session.add(duplicate)
        await session.commit()
        await session.refresh(duplicate)
        duplicate_id = duplicate.id

    first_execution = await client.post(
        f"/commands/confirm/{first_id}", headers=headers(user)
    )
    duplicate_execution = await client.post(
        f"/commands/confirm/{duplicate_id}", headers=headers(user)
    )

    assert first_execution.status_code == duplicate_execution.status_code == 200


async def test_close_can_execute_again_after_a_new_confirmation(client, make_user, make_conversation, monkeypatch):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    calls = []

    async def execute(action, payload, idempotency_key):
        calls.append((action, idempotency_key))
        return {"status": "succeeded"}

    monkeypatch.setattr("app.services.command_service.platform_client.execute", execute)
    payload = {"conversation_id": conversation, "resource_id": "membership-2026"}
    first = await client.post("/commands/close-auto-renew", json=payload, headers=headers(user))
    await client.post(f"/commands/confirm/{first.json()['confirmation_id']}", headers=headers(user))
    second = await client.post("/commands/close-auto-renew", json=payload, headers=headers(user))
    await client.post(f"/commands/confirm/{second.json()['confirmation_id']}", headers=headers(user))

    assert first.json()["confirmation_id"] != second.json()["confirmation_id"]
    assert len(calls) == 2
    assert calls[0][1] != calls[1][1]


async def test_finance_cross_user_is_denied_audited_and_never_calls_downstream(client, make_user, monkeypatch):
    owner = await make_user()
    attacker = await make_user(tenant_id=owner["tenant_id"])
    calls = []

    async def query(*args):
        calls.append(args)
        return {}

    monkeypatch.setattr("app.api.business.finance_client.query", query)
    response = await client.get(f"/finance/invoice?target_user_id={owner['user_id']}", headers=headers(attacker))
    assert response.status_code == 403
    assert calls == []
    async with async_session() as session:
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "finance.invoice"))
        assert audit is not None and audit.outcome == "denied"


async def test_reminder_and_handoff_are_owned_and_idempotent(client, make_user, make_conversation):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    run_at = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    reminder = await client.post("/reminders", json={"conversation_id": conversation, "content": "上课", "run_at_local": run_at, "timezone": "Asia/Shanghai", "repeat": "once"}, headers=headers(user))
    assert reminder.status_code == 201
    cancelled = await client.delete(f"/reminders/{reminder.json()['id']}", headers=headers(user))
    assert cancelled.json()["status"] == "cancelled"

    payload = {"conversation_id": conversation, "summary": "邮箱 parent@example.com，用户要求转人工", "attempted_actions": ["knowledge"]}
    first = await client.post("/handoffs", json=payload, headers=headers(user))
    second = await client.post("/handoffs", json=payload, headers=headers(user))
    assert first.json()["id"] == second.json()["id"]
    assert "parent@example.com" not in first.json()["summary"]


async def test_duplicate_active_reminder_same_time_and_title_is_rejected(client, make_user, make_conversation):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    payload = {
        "conversation_id": conversation, "content": "准备数学课本",
        "run_at_local": (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(hours=2)).replace(tzinfo=None).isoformat(),
        "timezone": "Asia/Shanghai", "repeat": "once",
    }
    assert (await client.post("/reminders", json=payload, headers=headers(user))).status_code == 201
    duplicate = await client.post("/reminders", json=payload, headers=headers(user))
    assert duplicate.status_code == 409
    assert "已存在内容和时间相同的提醒" in duplicate.json()["detail"]
    async with async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 1


async def test_offline_handoff_message_is_saved_without_ai_reply(client, make_user, make_conversation):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    response = await client.post(
        "/handoffs", headers=headers(user),
        json={"conversation_id": conversation, "summary": "人工客服不在线", "reason": "offline_message", "message": "请明天上午联系我"},
    )
    assert response.status_code == 202
    history = await client.get(f"/conversations/{conversation}/messages", headers=headers(user))
    assert [(item["role"], item["content"]) for item in history.json()["messages"]] == [
        ("user", "请明天上午联系我")
    ]


async def test_admin_can_list_and_reply_only_to_own_tenant_handoffs(
    client, make_user, make_conversation
):
    customer = await make_user()
    admin = await make_user(tenant_id=customer["tenant_id"], role="admin")
    outsider = await make_user(role="admin")
    conversation = await make_conversation(customer["tenant_id"], customer["user_id"])
    created = await client.post(
        "/handoffs",
        json={"conversation_id": conversation, "summary": "需要老师协助"},
        headers=headers(customer),
    )
    handoff_id = created.json()["id"]

    own = await client.get("/admin/handoffs", headers=headers(admin))
    assert own.status_code == 200
    assert [item["id"] for item in own.json()["handoffs"]] == [handoff_id]
    assert (await client.get("/admin/handoffs", headers=headers(outsider))).json()["handoffs"] == []
    denied = await client.post(
        f"/admin/handoffs/{handoff_id}/reply",
        json={"content": "跨租户回复"}, headers=headers(outsider),
    )
    assert denied.status_code == 403

    reply = await client.post(
        f"/admin/handoffs/{handoff_id}/reply",
        json={"content": "您好，我是人工客服陈老师。"}, headers=headers(admin),
    )
    assert reply.status_code == 200
    history = await client.get(
        f"/conversations/{conversation}/messages", headers=headers(customer)
    )
    assert history.json()["messages"][-1]["content"] == "您好，我是人工客服陈老师。"
    assert history.json()["messages"][-1]["role"] == "assistant"


async def test_handoff_lifecycle_requires_agent_close_and_customer_confirmation(
    client, make_user, make_conversation
):
    customer = await make_user()
    agent = await make_user(tenant_id=customer["tenant_id"], role="agent")
    conversation = await make_conversation(customer["tenant_id"], customer["user_id"])
    created = await client.post(
        "/handoffs", headers=headers(customer),
        json={"conversation_id": conversation, "summary": "需要人工协助"},
    )
    handoff_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    active = await client.get(
        f"/handoffs/active?conversation_id={conversation}", headers=headers(customer)
    )
    assert active.json()["handoff"]["status"] == "pending"

    accepted = await client.post(
        f"/admin/handoffs/{handoff_id}/accept", headers=headers(agent)
    )
    assert accepted.json()["status"] == "in_progress"

    close_requested = await client.post(
        f"/admin/handoffs/{handoff_id}/request-close", headers=headers(agent)
    )
    assert close_requested.json()["status"] == "awaiting_confirmation"
    assert close_requested.json()["close_deadline"] is not None

    declined = await client.post(
        f"/handoffs/{handoff_id}/close-response", headers=headers(customer),
        json={"confirm": False},
    )
    assert declined.json()["status"] == "in_progress"

    await client.post(f"/admin/handoffs/{handoff_id}/request-close", headers=headers(agent))
    confirmed = await client.post(
        f"/handoffs/{handoff_id}/close-response", headers=headers(customer),
        json={"confirm": True},
    )
    assert confirmed.json()["status"] == "ended"
    async with async_session() as session:
        row = await session.get(Handoff, uuid.UUID(handoff_id))
        assert row.active_key is None
        assert row.ended_at is not None


async def test_expired_handoff_close_request_is_auto_ended(client, make_user, make_conversation):
    customer = await make_user()
    conversation = await make_conversation(customer["tenant_id"], customer["user_id"])
    created = await client.post(
        "/handoffs", headers=headers(customer),
        json={"conversation_id": conversation, "summary": "超时结束测试"},
    )
    async with async_session() as session:
        row = await session.get(Handoff, uuid.UUID(created.json()["id"]))
        row.status = "awaiting_confirmation"
        row.close_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    active = await client.get(
        f"/handoffs/active?conversation_id={conversation}", headers=headers(customer)
    )
    assert active.json()["handoff"] is None
    async with async_session() as session:
        row = await session.get(Handoff, uuid.UUID(created.json()["id"]))
        assert row.status == "ended"


async def test_platform_reads_use_authenticated_identity(client, make_user, monkeypatch):
    user = await make_user()
    calls = []

    async def query(kind, tenant_id, user_id):
        calls.append((kind, tenant_id, user_id))
        return {"status": "ok", "kind": kind}

    monkeypatch.setattr("app.api.business.platform_client.query", query)
    schedule = await client.get("/platform/course-schedule", headers=headers(user))
    report = await client.get("/platform/study-report", headers=headers(user))
    subscription = await client.get("/platform/subscription-status", headers=headers(user))

    assert schedule.status_code == report.status_code == subscription.status_code == 200
    assert calls == [
        ("course_schedule", user["tenant_id"], user["user_id"]),
        ("study_report", user["tenant_id"], user["user_id"]),
        ("subscription_status", user["tenant_id"], user["user_id"]),
    ]


async def test_low_risk_platform_command_is_idempotent(client, make_user, make_conversation, monkeypatch):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    calls = []

    async def execute(action, payload, idempotency_key):
        calls.append((action, payload, idempotency_key))
        return {"status": "succeeded", "action": action}

    monkeypatch.setattr("app.services.command_service.platform_client.execute", execute)
    payload = {
        "conversation_id": conversation,
        "resource_id": "course-101",
        "arguments": {"date": "2026-09-25", "reason": "生病"},
        "idempotency_key": "leave-request-001",
    }
    first = await client.post("/commands/submit-leave", json=payload, headers=headers(user))
    conflicting_retry = {**payload, "arguments": {"date": "2026-09-26", "reason": "changed"}}
    second = await client.post("/commands/submit-leave", json=conflicting_retry, headers=headers(user))

    assert first.status_code == second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert len(calls) == 1


async def test_open_auto_renew_is_immediate_and_idempotent(client, make_user, make_conversation, monkeypatch):
    user = await make_user()
    conversation = await make_conversation(user["tenant_id"], user["user_id"])
    calls = []

    async def execute(action, payload, idempotency_key):
        calls.append((action, payload, idempotency_key))
        return {"status": "succeeded", "action": action, "enabled": True}

    monkeypatch.setattr("app.services.command_service.platform_client.execute", execute)
    payload = {
        "conversation_id": conversation,
        "resource_id": "membership-2026",
        "arguments": {"plan": "annual"},
        "idempotency_key": "open-renew-001",
    }
    first = await client.post("/commands/open-auto-renew", json=payload, headers=headers(user))
    second = await client.post("/commands/open-auto-renew", json=payload, headers=headers(user))

    assert first.status_code == second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert first.json()["result"]["enabled"] is True
    assert len(calls) == 1
    assert calls[0][0] == "open_auto_renew"
    assert calls[0][1] == {"resource_id": "membership-2026", "plan": "annual"}
    assert len(calls[0][2]) == 64


async def test_admin_dead_letters_are_tenant_scoped_and_replayable(client, make_user, make_conversation):
    admin = await make_user(role="admin")
    other_admin = await make_user(role="admin")
    conversation = await make_conversation(admin["tenant_id"], admin["user_id"])
    async with async_session() as session:
        message = Message(
            tenant_id=admin["tenant_id"], conversation_id=conversation,
            message_id="failed-message", role="user", content="请查询课程",
            status="processing", trace_id="trace-dead-letter",
        )
        own = DeadLetter(
            tenant_id=admin["tenant_id"], trace_id="trace-dead-letter",
            original_subject="im.inbound", error_type="TimeoutError", error="timeout",
            metadata_json={"message_id": "failed-message"},
        )
        foreign = DeadLetter(
            tenant_id=other_admin["tenant_id"], trace_id="foreign",
            original_subject="im.inbound", error_type="ValueError", error="bad",
            metadata_json={"message_id": "foreign-message"},
        )
        session.add_all([message, own, foreign])
        await session.commit()
        own_id = str(own.id)

    listed = await client.get("/admin/dead-letters", headers=headers(admin))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["dead_letters"]] == [own_id]
    assert "content" not in listed.json()["dead_letters"][0]["metadata"]

    replayed = await client.post(f"/admin/dead-letters/{own_id}/replay", headers=headers(admin))
    repeated = await client.post(f"/admin/dead-letters/{own_id}/replay", headers=headers(admin))
    assert replayed.status_code == repeated.status_code == 202
    assert replayed.json()["status"] == "replayed"
