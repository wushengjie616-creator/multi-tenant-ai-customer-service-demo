"""P1 认证 / 防伪造集成测试（NFR3-01 / NFR3-06）。"""

import uuid

from sqlalchemy import select

from app.core.security import create_access_token
from app.models import Message


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_login_and_me(client, make_user):
    u = await make_user(email="zhangsan@example.com", phone="13812345678")

    r = await client.post(
        "/auth/login",
        json={
            "tenant_id": u["tenant_id"],
            "email": "zhangsan@example.com",
            "password": "pass123",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    # 本人看自己完整邮箱（不脱敏）
    assert body["user"]["email"] == "zhangsan@example.com"

    me = await client.get("/users/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "zhangsan@example.com"
    assert me.json()["tenant_id"] == u["tenant_id"]


async def test_login_wrong_password_401(client, make_user):
    u = await make_user(email="a@example.com")
    r = await client.post(
        "/auth/login",
        json={"tenant_id": u["tenant_id"], "email": "a@example.com", "password": "wrong"},
    )
    assert r.status_code == 401


async def test_login_unknown_tenant_returns_401(client):
    """不存在租户的登录失败不能因 audit_logs 外键变成 500。"""
    r = await client.post(
        "/auth/login",
        json={
            "tenant_id": str(uuid.uuid4()),
            "email": "nobody@example.com",
            "password": "wrong",
        },
    )
    assert r.status_code == 401


async def test_api_rejects_signed_non_uuid_identity_claims(client):
    """合法签名中的畸形身份也应是 401，而不是下游 uuid.UUID 的 500。"""
    token = create_access_token(
        tenant_id="not-a-uuid", user_id="also-not-a-uuid", role="user"
    )
    r = await client.get("/users/me", headers=_auth(token))
    assert r.status_code == 401


async def test_endpoints_require_auth(client):
    # 有效 body 但无 token → 401（而非 422，先过认证）
    valid_body = {
        "message_id": "m1",
        "tenant_id": "t",
        "user_id": "u",
        "conversation_id": "c",
        "content": "hi",
    }
    assert (await client.post("/webhooks/im/messages", json=valid_body)).status_code == 401
    assert (await client.get("/conversations/x/messages")).status_code == 401
    assert (await client.get("/users/me")).status_code == 401
    assert (await client.get("/admin/users")).status_code == 401


async def test_webhook_rejects_forged_identity(client, make_user, make_conversation):
    """请求体伪造 tenant_id/user_id 不能覆盖 token 身份 → 403（NFR3-06）。"""
    a = await make_user(role="user")
    b = await make_user(role="user")
    conv_a = await make_conversation(a["tenant_id"], a["user_id"])

    r = await client.post(
        "/webhooks/im/messages",
        json={
            "message_id": "msg-forged",
            "tenant_id": b["tenant_id"],  # 伪造：与 token 身份不一致
            "user_id": b["user_id"],
            "conversation_id": conv_a,
            "content": "hello",
        },
        headers=_auth(a["token"]),
    )
    assert r.status_code == 403


async def test_webhook_uses_token_identity(client, make_user, make_conversation):
    """一致请求以 token 身份为准落库，消息归认证租户。"""
    a = await make_user(role="user")
    conv_a = await make_conversation(a["tenant_id"], a["user_id"])

    r = await client.post(
        "/webhooks/im/messages",
        json={
            "message_id": "msg-ok-1",
            "tenant_id": a["tenant_id"],
            "user_id": a["user_id"],
            "conversation_id": conv_a,
            "content": "hello",
        },
        headers=_auth(a["token"]),
    )
    assert r.status_code == 202
    assert r.json()["status"] == "accepted"

    from app.core.database import async_session

    async with async_session() as s:
        m = await s.scalar(
            select(Message).where(
                Message.tenant_id == uuid.UUID(a["tenant_id"]),
                Message.message_id == "msg-ok-1",
            )
        )
    assert m is not None
    assert str(m.conversation_id) == conv_a


async def test_user_cannot_post_to_another_users_conversation(
    client, make_user, make_conversation
):
    """同租户也必须校验会话 owner，不能只校验 tenant。"""
    owner = await make_user(role="user")
    attacker = await make_user(role="user", tenant_id=owner["tenant_id"])
    conversation_id = await make_conversation(owner["tenant_id"], owner["user_id"])

    r = await client.post(
        "/webhooks/im/messages",
        json={
            "message_id": "same-tenant-forged-conversation",
            "tenant_id": attacker["tenant_id"],
            "user_id": attacker["user_id"],
            "conversation_id": conversation_id,
            "content": "write into another user's conversation",
        },
        headers=_auth(attacker["token"]),
    )
    assert r.status_code == 403
