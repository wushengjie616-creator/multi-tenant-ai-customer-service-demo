import uuid
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session
from app.core.security import create_access_token, decode_token, hash_password
from app.models import Conversation, Tenant, User
from app.demo_catalog import demo_tenant_catalog


def test_interview_demo_catalog_has_five_short_unique_tenants():
    catalog = demo_tenant_catalog()
    assert [item["code"] for item in catalog] == ["T01", "T02", "T03", "T04", "T05"]
    assert len({item["id"] for item in catalog}) == 5
    assert len({item["name"] for item in catalog}) == 5
    assert all(item["features"] and item["customer_name"] for item in catalog)


async def test_frontend_pages_are_served_by_fastapi(client):
    landing = await client.get("/ui/")
    tenant_auth = await client.get("/ui/tenant-auth.html")
    agent = await client.get("/ui/agent.html")
    tenant = await client.get("/ui/tenant.html")
    customer = await client.get("/ui/customer.html")
    platform = await client.get("/ui/platform.html")

    assert landing.status_code == tenant_auth.status_code == agent.status_code == tenant.status_code == customer.status_code == platform.status_code == 200
    assert "虚拟租户演示" in landing.text
    assert "多租户运营中心" in platform.text
    assert "多租户隔离" in landing.text
    assert "新建租户" in landing.text
    assert "租户" in tenant.text
    assert "全链路 MOCK" in customer.text
    assert "财务中心" in customer.text
    assert "开启续费" in customer.text
    assert 'id="reminder-form"' in customer.text
    assert 'id="handoff-button"' in customer.text
    assert 'id="file" type="file" multiple' in tenant.text
    assert 'id="knowledge-list"' in tenant.text
    assert 'id="operations"' in tenant.text
    assert 'id="create-tenant-form"' in tenant_auth.text
    assert 'value="finance"' in tenant_auth.text
    assert "审计事件" in tenant.text
    assert "您可以这样提问" in customer.text
    assert 'id="suggestion-list"' in customer.text
    assert 'id="create-tenant-form"' in tenant_auth.text
    assert 'id="tenant-login-form"' in tenant_auth.text
    assert "注册新租户 / 登录已有租户" in tenant_auth.text
    assert 'id="reply-templates"' in agent.text
    assert 'id="request-close"' in agent.text
    assert 'id="handoff-state"' in customer.text
    assert 'id="confirm-handoff-close"' in customer.text
    assert 'id="handoff-close-prompt"' in customer.text
    assert 'id="prompt-confirm-handoff-close"' in customer.text
    assert 'role="alertdialog"' in customer.text
    assert 'id="tenant-handoff-monitor"' in tenant.text
    assert 'id="tenant-confirm-handoff-close"' in tenant.text
    assert 'id="tenant-continue-handoff"' in tenant.text
    assert 'id="customer-list"' in tenant.text
    assert 'id="create-tenant-form"' not in tenant.text
    assert "登录已有租户" not in tenant.text
    assert "进入客户视角" not in tenant.text
    assert "再建一个租户" not in tenant.text
    assert "/ui/tenant-auth.html" not in platform.text
    assert "/ui/tenant-auth.html" not in customer.text


async def test_demo_customer_session_is_disabled_unless_demo_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False, raising=False)
    assert (await client.post("/demo/customer-session")).status_code == 404


async def test_demo_customer_session_isolated_from_workbench_tenant(client, monkeypatch):
    workbench_tenant_id = uuid.uuid4()
    customer_tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)
    monkeypatch.setattr(
        settings, "demo_tenant_id", str(workbench_tenant_id), raising=False
    )
    monkeypatch.setattr(
        settings, "demo_customer_tenant_id", str(customer_tenant_id), raising=False
    )
    monkeypatch.setattr(settings, "demo_customer_user_id", str(user_id), raising=False)
    monkeypatch.setattr(
        settings, "demo_customer_conversation_id", str(conversation_id), raising=False
    )
    monkeypatch.setattr(
        settings, "demo_customer_tenant_name", "固定示例样例", raising=False
    )
    async with async_session() as session:
        session.add(Tenant(id=workbench_tenant_id, name="工作台租户"))
        session.add(Tenant(id=customer_tenant_id, name="固定示例样例"))
        await session.flush()
        session.add(
            User(
                id=user_id,
                tenant_id=customer_tenant_id,
                role="user",
                email="customer@example.com",
                password_hash=hash_password("unused"),
            )
        )
        await session.flush()
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=customer_tenant_id,
                user_id=user_id,
            )
        )
        await session.commit()

    response = await client.post("/demo/customer-session")

    assert response.status_code == 200
    body = response.json()
    identity = decode_token(body["access_token"])
    assert identity.tenant_id == str(customer_tenant_id)
    assert identity.tenant_id != str(workbench_tenant_id)
    assert identity.user_id == str(user_id)
    assert identity.role == "user"
    assert body["conversation_id"] == str(conversation_id)
    assert body["tenant_id"] == str(customer_tenant_id)
    assert body["tenant_name"] == "固定示例样例"
    assert body["dependency_mode"] == "mock"
    assert body["features"] == [
        "knowledge", "assistant", "learning", "finance",
        "services", "reminders", "handoff", "operations",
    ]


async def test_demo_can_create_tenant_with_selected_features(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)
    response = await client.post("/demo/tenants", json={
        "tenant_name": "蓝天成长实验室",
        "admin_name": "陈老师",
        "admin_email": "admin@blue-sky.example.com",
        "admin_password": "StrongDemo123!",
        "features": ["knowledge", "assistant", "finance"],
    })

    assert response.status_code == 201
    body = response.json()
    assert body["tenant_name"] == "蓝天成长实验室"
    assert body["features"] == ["knowledge", "assistant", "finance"]
    assert body["dependency_mode"] == "configured"
    identity = decode_token(body["access_token"])
    assert identity.role == "admin"
    customer_identity = decode_token(body["customer_access_token"])
    assert customer_identity.role == "user"
    assert customer_identity.tenant_id == body["tenant_id"]
    assert body["customer_user_id"] != body["user_id"]
    assert body["customer_conversation_id"] != body["conversation_id"]
    async with async_session() as session:
        tenant = await session.get(Tenant, uuid.UUID(body["tenant_id"]))
        user = await session.get(User, uuid.UUID(body["user_id"]))
        conversation = await session.get(Conversation, uuid.UUID(body["conversation_id"]))
        assert tenant is not None and tenant.features == ["knowledge", "assistant", "finance"]
        assert user is not None and user.tenant_id == tenant.id
        assert conversation is not None and conversation.user_id == user.id
        customer_count = await session.scalar(
            select(func.count()).select_from(User).where(
                User.tenant_id == tenant.id, User.role == "user"
            )
        )
        assert customer_count == 4


async def test_logged_in_admin_can_restore_tenant_workbench_session(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)
    created = (await client.post("/demo/tenants", json={
        "tenant_name": "可重新登录机构", "admin_name": "赵老师",
        "admin_email": "login-again@example.com", "admin_password": "StrongDemo123!",
        "features": ["assistant", "handoff"],
    })).json()
    login = await client.post("/auth/login", json={
        "tenant_id": created["tenant_id"], "email": "login-again@example.com", "password": "StrongDemo123!",
    })
    restored = await client.post(
        "/demo/workbench-session",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert restored.status_code == 200
    body = restored.json()
    assert body["tenant_id"] == created["tenant_id"]
    assert body["features"] == ["assistant", "handoff"]
    assert body["customer_access_token"]
    assert body["customer_conversation_id"]


async def test_platform_admin_lists_and_enters_multiple_tenants(client, monkeypatch, make_user):
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)
    first = await make_user(role="admin")
    second = await make_user(role="admin")
    listing = await client.get("/demo/platform/tenants")
    assert listing.status_code == 200
    ids = {item["tenant_id"] for item in listing.json()["tenants"]}
    assert {first["tenant_id"], second["tenant_id"]}.issubset(ids)
    entered = await client.post(f"/demo/platform/tenants/{first['tenant_id']}/session")
    assert entered.status_code == 200
    assert entered.json()["tenant_id"] == first["tenant_id"]
    assert entered.json()["access_token"]
    assert entered.json()["customer_access_token"]


async def test_demo_rejects_unknown_tenant_feature(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True, raising=False)
    response = await client.post("/demo/tenants", json={
        "tenant_name": "无效功能租户",
        "admin_name": "管理员",
        "admin_email": "invalid@example.com",
        "admin_password": "StrongDemo123!",
        "features": ["knowledge", "not-a-feature"],
    })
    assert response.status_code == 422


async def test_tenant_document_upload_obeys_configured_size_limit(
    client, make_user, monkeypatch
):
    admin = await make_user(role="admin")
    monkeypatch.setattr(settings, "demo_max_upload_chars", 10, raising=False)
    from app.api import knowledge

    monkeypatch.setattr(knowledge.vector_store, "delete_document", AsyncMock())
    monkeypatch.setattr(knowledge.vector_store, "upsert_chunks", AsyncMock())
    token = create_access_token(
        tenant_id=admin["tenant_id"], user_id=admin["user_id"], role="admin"
    )

    response = await client.post(
        "/knowledge/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "document_id": "too-large",
            "title": "超限",
            "source": "ui://upload",
            "content": "12345678901",
            "version": 1,
        },
    )

    assert response.status_code == 413
    knowledge.vector_store.upsert_chunks.assert_not_awaited()


async def test_tenant_document_upload_persists_llm_entities(
    client, make_user, monkeypatch
):
    admin = await make_user(role="admin")
    from app.api import knowledge

    monkeypatch.setattr(knowledge.vector_store, "delete_document", AsyncMock())
    monkeypatch.setattr(knowledge.vector_store, "upsert_chunks", AsyncMock())
    monkeypatch.setattr(
        knowledge.llm_client,
        "generate",
        AsyncMock(
            return_value=(
                '{"entities":["银河教室"],"aliases":["三号教室"],'
                '"topics":["校区地点"],"keywords":["上课地址"]}'
            )
        ),
    )
    token = create_access_token(
        tenant_id=admin["tenant_id"], user_id=admin["user_id"], role="admin"
    )

    response = await client.post(
        "/knowledge/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "document_id": "room-policy",
            "title": "教室安排",
            "source": "ui://upload",
            "content": "周三的口语课在银河教室上课。",
            "version": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["enrichment"] == {"llm": 1}
    chunks = knowledge.vector_store.upsert_chunks.await_args.args[1]
    assert "银河教室" in chunks[0]["payload"]["entities"]


async def test_tenant_admin_lists_documents_using_token_tenant(
    client, make_user, monkeypatch
):
    admin = await make_user(role="admin")
    from app.api import knowledge

    expected = [{
        "document_id": "course-guide",
        "title": "课程说明",
        "source": "ui://tenant-upload/course-guide",
        "version": 1,
        "preview_lines": ["第一行", "第二行", "第三行"],
        "content": "第一行\n第二行\n第三行\n第四行",
    }]
    list_documents = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        knowledge.vector_store, "list_documents", list_documents, raising=False
    )

    response = await client.get(
        "/knowledge/documents",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )

    assert response.status_code == 200
    assert response.json() == {"documents": expected}
    list_documents.assert_awaited_once_with(admin["tenant_id"])


async def test_customer_gets_tenant_scoped_suggested_questions(
    client, make_user, monkeypatch
):
    customer = await make_user(role="user")
    from app.api import knowledge

    suggestions = ["有哪些课程？", "如何预约试听课？"]
    list_suggestions = AsyncMock(return_value=suggestions)
    monkeypatch.setattr(
        knowledge.vector_store,
        "list_suggested_questions",
        list_suggestions,
        raising=False,
    )

    response = await client.get(
        "/knowledge/suggestions",
        headers={"Authorization": f"Bearer {customer['token']}"},
    )

    assert response.status_code == 200
    assert response.json() == {"suggestions": suggestions}
    list_suggestions.assert_awaited_once_with(customer["tenant_id"], limit=8)
