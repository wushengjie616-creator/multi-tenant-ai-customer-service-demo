"""P1 租户隔离 / RBAC / 脱敏集成测试（NFR3-02 / NFR3-03 / NFR3-04 / NFR3-07 / NFR4-05）。"""

import uuid

from sqlalchemy import select

from app.core.database import async_session
from app.models import AuditLog
from app.models import LLMUsage
from app.schemas.message import InboundMessage
from app.services import conversation_service


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_user_cannot_access_admin(client, make_user):
    u = await make_user(role="user")
    assert (await client.get("/admin/users", headers=_auth(u["token"]))).status_code == 403
    assert (
        await client.get("/admin/audit-logs", headers=_auth(u["token"]))
    ).status_code == 403


async def test_admin_tenant_isolation(client, make_user):
    """admin 只能看到本租户用户（且脱敏），跨租户用户不可见。"""
    admin = await make_user(role="admin", email="admin@a.example.com")
    await make_user(role="user", email="member@a.example.com", tenant_id=admin["tenant_id"])
    await make_user(role="user", email="intruder@b.example.com")  # 不同租户

    r = await client.get("/admin/users", headers=_auth(admin["token"]))
    assert r.status_code == 200
    emails = {x["email"] for x in r.json()["users"]}
    assert "m***@a.example.com" in emails  # 同租户可见（脱敏）
    assert "i***@b.example.com" not in emails  # 跨租户不可见


async def test_cross_tenant_list_messages_403(client, make_user, make_conversation):
    """租户 A 读租户 B 的会话 → 403。"""
    a = await make_user(role="user")
    b = await make_user(role="user")
    conv_b = await make_conversation(b["tenant_id"], b["user_id"])

    r = await client.get(f"/conversations/{conv_b}/messages", headers=_auth(a["token"]))
    assert r.status_code == 403


async def test_user_cannot_read_another_users_conversation_in_same_tenant(
    client, make_user, make_conversation
):
    owner = await make_user(role="user")
    attacker = await make_user(role="user", tenant_id=owner["tenant_id"])
    conversation_id = await make_conversation(owner["tenant_id"], owner["user_id"])

    r = await client.get(
        f"/conversations/{conversation_id}/messages",
        headers=_auth(attacker["token"]),
    )
    assert r.status_code == 403


async def test_cross_tenant_denied_is_audited(client, make_user, make_conversation):
    """越权访问被审计（outcome=denied，tenant 记在尝试方）。"""
    a = await make_user(role="user")
    b = await make_user(role="user")
    conv_b = await make_conversation(b["tenant_id"], b["user_id"])

    r = await client.get(f"/conversations/{conv_b}/messages", headers=_auth(a["token"]))
    assert r.status_code == 403

    async with async_session() as s:
        rows = (
            await s.scalars(
                select(AuditLog).where(AuditLog.tenant_id == uuid.UUID(a["tenant_id"]))
            )
        ).all()
    assert any(x.action == "messages.list" and x.outcome == "denied" for x in rows)


async def test_tenant_filter_isolation_at_query_level(make_user, make_conversation):
    """突变守卫：直接调 list_messages 服务，验证 tenant 过滤是硬约束。

    若有人删掉 `.where(Message.tenant_id == tenant_id)`，A 会读到 B 的消息，本用例即失败。
    """
    a = await make_user()
    b = await make_user()
    conv_b = await make_conversation(b["tenant_id"], b["user_id"])

    inbound = InboundMessage(
        message_id="b-msg-1",
        tenant_id=b["tenant_id"],
        user_id=b["user_id"],
        conversation_id=conv_b,
        content="secret of tenant B",
    )
    async with async_session() as s:
        await conversation_service.ingest_message(s, inbound)

    # A 查 B 的会话 → 空
    async with async_session() as s:
        rows = await conversation_service.list_messages(
            s, uuid.UUID(a["tenant_id"]), uuid.UUID(conv_b)
        )
    assert rows == []

    # 对照：B 查自己的会话能读到消息
    async with async_session() as s:
        own = await conversation_service.list_messages(
            s, uuid.UUID(b["tenant_id"]), uuid.UUID(conv_b)
        )
    assert len(own) == 1
    assert own[0]["content"] == "secret of tenant B"


async def test_admin_masks_pii(client, make_user):
    """批量展示脱敏 email/phone；审计不含完整 PII。"""
    admin = await make_user(role="admin", email="admin@example.com")
    await make_user(
        role="user",
        email="private@example.com",
        phone="13812345678",
        tenant_id=admin["tenant_id"],
    )

    r = await client.get("/admin/users", headers=_auth(admin["token"]))
    assert r.status_code == 200
    users = r.json()["users"]
    emails = {x["email"] for x in users}
    phones = {x["phone"] for x in users if x["phone"]}
    assert "p***@example.com" in emails
    assert "private@example.com" not in emails  # 完整邮箱不出现
    assert "138****5678" in phones

    # 登录失败：审计落库的 target 只含脱敏邮箱
    await client.post(
        "/auth/login",
        json={
            "tenant_id": admin["tenant_id"],
            "email": "private@example.com",
            "password": "wrong",
        },
    )
    async with async_session() as s:
        rows = (
            await s.scalars(
                select(AuditLog).where(AuditLog.tenant_id == uuid.UUID(admin["tenant_id"]))
            )
        ).all()
    targets = " ".join(x.target for x in rows)
    assert "private@example.com" not in targets
    assert "p***@example.com" in targets


async def test_audit_logs_tenant_isolation(client, make_user):
    """admin 查审计日志只看到本租户，跨租户审计不可见。"""
    a = await make_user(role="admin", email="admin@a.example.com")
    b = await make_user(role="user", email="b@b.example.com")

    # 各产生一条登录审计（a 成功 / b 失败），落库在不同租户下
    await client.post(
        "/auth/login",
        json={"tenant_id": a["tenant_id"], "email": "admin@a.example.com", "password": "pass123"},
    )
    await client.post(
        "/auth/login",
        json={"tenant_id": b["tenant_id"], "email": "b@b.example.com", "password": "wrong"},
    )

    r = await client.get("/admin/audit-logs", headers=_auth(a["token"]))
    assert r.status_code == 200
    tenant_ids = {x["tenant_id"] for x in r.json()["audit_logs"]}
    assert a["tenant_id"] in tenant_ids
    assert b["tenant_id"] not in tenant_ids  # 跨租户审计不可见


async def test_admin_creates_same_tenant_admin_with_working_initial_login(client, make_user):
    owner = await make_user(role="admin")
    response = await client.post(
        "/admin/users", headers=_auth(owner["token"]),
        json={"full_name": "王老师", "email": "wang-admin@example.com", "initial_password": "InitialPass123!"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["role"] == "admin"
    assert body["login"] == {
        "tenant_id": owner["tenant_id"],
        "email": "wang-admin@example.com",
        "password": "InitialPass123!",
    }
    login = await client.post("/auth/login", json=body["login"])
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "admin"


async def test_non_admin_cannot_create_admin(client, make_user):
    user = await make_user()
    response = await client.post(
        "/admin/users", headers=_auth(user["token"]),
        json={"full_name": "越权", "email": "denied@example.com", "initial_password": "InitialPass123!"},
    )
    assert response.status_code == 403


async def test_llm_usage_dashboard_is_tenant_scoped(client, make_user, make_conversation):
    admin = await make_user(role="admin")
    other = await make_user(role="admin")
    conversation = await make_conversation(admin["tenant_id"], admin["user_id"])
    other_conversation = await make_conversation(other["tenant_id"], other["user_id"])
    async with async_session() as session:
        session.add_all([
            LLMUsage(tenant_id=uuid.UUID(admin["tenant_id"]), conversation_id=uuid.UUID(conversation), message_id="m1", model="deepseek", prompt_tokens=100, completion_tokens=20, cost_usd=0.01),
            LLMUsage(tenant_id=uuid.UUID(other["tenant_id"]), conversation_id=uuid.UUID(other_conversation), message_id="m2", model="deepseek", prompt_tokens=999, completion_tokens=999, cost_usd=9.99),
        ])
        await session.commit()
    response = await client.get("/admin/llm-usage", headers=_auth(admin["token"]))
    assert response.status_code == 200
    assert response.json()["conversations"] == [{
        "conversation_id": conversation, "prompt_tokens": 100, "completion_tokens": 20,
        "total_tokens": 120, "cost_usd": 0.01, "llm_calls": 1,
    }]
