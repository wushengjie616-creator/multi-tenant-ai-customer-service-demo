"""Prepare an editable workbench and an isolated, fixed customer demo tenant."""

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import delete

from app.clients.knowledge_client import vector_store
from app.core.config import settings
from app.core.database import async_session, engine
from app.core.security import ROLE_ADMIN, ROLE_USER, hash_password
from app.models import Conversation, Tenant, User
from app.demo_catalog import demo_tenant_catalog
from app.services.knowledge_ingestion import build_document_chunks
from app.services.graph_store import graph_store

ALL_DEMO_FEATURES = [
    "knowledge", "assistant", "learning", "finance",
    "services", "reminders", "handoff", "operations",
]


async def bootstrap_database() -> None:
    catalog = demo_tenant_catalog()
    catalog_by_id = {item["id"]: item for item in catalog}
    workbench_tenant_id = uuid.UUID(settings.demo_tenant_id)
    customer_tenant_id = uuid.UUID(settings.demo_customer_tenant_id)
    admin_id = uuid.UUID(settings.demo_admin_user_id)
    customer_id = uuid.UUID(settings.demo_customer_user_id)
    admin_conversation_id = uuid.UUID(settings.demo_admin_conversation_id)
    customer_conversation_id = uuid.UUID(settings.demo_customer_conversation_id)

    async with async_session() as session:
        # 历史样例曾让“四季”与当前星河租户复用同一 UUID；仅清理该已知虚构数据冲突。
        await session.execute(delete(User).where(
            User.tenant_id == customer_tenant_id,
            User.email.like("%@yuehua-math.example.com"),
        ))
        await session.execute(delete(User).where(
            User.tenant_id == workbench_tenant_id,
            User.email.like("%@xinghui-english.example.com"),
        ))
        workbench_tenant = await session.get(Tenant, workbench_tenant_id)
        if workbench_tenant is None:
            workbench_tenant = Tenant(
                id=workbench_tenant_id, name=catalog_by_id[workbench_tenant_id]["name"]
            )
            session.add(workbench_tenant)
            await session.flush()
        else:
            workbench_tenant.name = catalog_by_id[workbench_tenant_id]["name"]
        workbench_tenant.features = catalog_by_id[workbench_tenant_id]["features"]

        customer_tenant = await session.get(Tenant, customer_tenant_id)
        if customer_tenant is None:
            customer_tenant = Tenant(
                id=customer_tenant_id,
                name=catalog_by_id[customer_tenant_id]["name"],
            )
            session.add(customer_tenant)
            await session.flush()
        else:
            customer_tenant.name = catalog_by_id[customer_tenant_id]["name"]
        customer_tenant.features = catalog_by_id[customer_tenant_id]["features"]

        # The platform-admin recording surface is intentionally curated to five
        # deterministic tenants. Existing user-created tenants remain untouched.
        for item in catalog:
            tenant = await session.get(Tenant, item["id"])
            if tenant is None:
                tenant = Tenant(id=item["id"], name=item["name"])
                session.add(tenant)
                await session.flush()
            tenant.name = item["name"]
            tenant.features = item["features"]

        admin = await session.get(User, admin_id)
        if admin is None:
            admin = User(
                id=admin_id,
                tenant_id=workbench_tenant_id,
                email=settings.demo_tenant_admin_email,
                password_hash="",
            )
            session.add(admin)
        admin.tenant_id = workbench_tenant_id
        admin.email = settings.demo_tenant_admin_email
        admin.full_name = "租户管理员"
        admin.role = ROLE_ADMIN
        admin.status = "active"
        admin.password_hash = hash_password(settings.demo_tenant_admin_password)

        customer = await session.get(User, customer_id)
        if customer is None:
            customer = User(
                id=customer_id,
                tenant_id=customer_tenant_id,
                email=settings.demo_customer_email,
                password_hash="!locked",
            )
            session.add(customer)
        customer.tenant_id = customer_tenant_id
        customer.email = settings.demo_customer_email
        customer.full_name = "林小满家长"
        customer.role = ROLE_USER
        customer.status = "active"

        for item in catalog:
            if item["id"] == customer_tenant_id:
                continue
            demo_user_id = uuid.uuid5(item["id"], "curated-customer")
            demo_conversation_id = uuid.uuid5(item["id"], "curated-conversation")
            demo_user = await session.get(User, demo_user_id)
            if demo_user is None:
                demo_user = User(
                    id=demo_user_id, tenant_id=item["id"],
                    email=f"customer-{item['code'].lower()}@demo.local",
                    password_hash="!locked",
                )
                session.add(demo_user)
            demo_user.tenant_id = item["id"]
            demo_user.email = f"customer-{item['code'].lower()}@demo.local"
            demo_user.full_name = item["customer_name"]
            demo_user.role = ROLE_USER
            demo_user.status = "active"
            await session.flush()
            demo_conversation = await session.get(Conversation, demo_conversation_id)
            if demo_conversation is None:
                session.add(Conversation(
                    id=demo_conversation_id,
                    tenant_id=item["id"],
                    user_id=demo_user_id,
                ))

        await session.flush()
        admin_conversation = await session.get(Conversation, admin_conversation_id)
        if admin_conversation is None:
            session.add(
                Conversation(
                    id=admin_conversation_id,
                    tenant_id=workbench_tenant_id,
                    user_id=admin_id,
                )
            )
        else:
            admin_conversation.tenant_id = workbench_tenant_id
            admin_conversation.user_id = admin_id

        customer_conversation = await session.get(
            Conversation, customer_conversation_id
        )
        if customer_conversation is None:
            session.add(
                Conversation(
                    id=customer_conversation_id,
                    tenant_id=customer_tenant_id,
                    user_id=customer_id,
                )
            )
        else:
            customer_conversation.tenant_id = customer_tenant_id
            customer_conversation.user_id = customer_id
        await session.commit()


async def bootstrap_knowledge(root: Path, manifest: dict) -> tuple[int, int]:
    tenant_id = manifest["tenant_id"]
    document_count = 0
    chunk_count = 0
    async with async_session() as session:
        for document in manifest["documents"]:
            content = (root / document["file"]).read_text(encoding="utf-8")
            chunks = build_document_chunks(
                tenant_id=tenant_id,
                document_id=document["document_id"],
                title=document["title"],
                source=document["source"],
                content=content,
                version=int(document.get("version", 1)),
            )
            await vector_store.delete_document(tenant_id, document["document_id"])
            await vector_store.upsert_chunks(tenant_id, chunks)
            await graph_store.replace_document(
                session,
                tenant_id,
                document_id=document["document_id"],
                title=document["title"],
                source=document["source"],
                content=content,
                version=int(document.get("version", 1)),
                effective_from=document.get("effective_from"),
                effective_until=document.get("effective_until"),
            )
            document_count += 1
            chunk_count += len(chunks)
    return document_count, chunk_count


async def main() -> None:
    if not settings.demo_mode:
        print("DEMO_MODE=false，跳过演示数据初始化")
        await engine.dispose()
        return
    await bootstrap_database()
    roots = [Path(settings.demo_customer_knowledge_root), Path("sample-data/tenants/xinghui-english")]
    stale_manifest_path = Path("sample-data/tenants/yuehua-math/manifest.json")
    if stale_manifest_path.exists():
        stale = json.loads(stale_manifest_path.read_text(encoding="utf-8"))
        if stale.get("tenant_id") == settings.demo_customer_tenant_id:
            for document in stale.get("documents", []):
                await vector_store.delete_document(settings.demo_customer_tenant_id, document["document_id"])
    for root in roots:
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            print(f"未找到 {manifest_path}，跳过该演示知识库")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["tenant_id"] not in {settings.demo_customer_tenant_id, settings.demo_tenant_id}:
            raise ValueError(f"演示 manifest tenant_id 未配置：{manifest['tenant_id']}")
        documents, chunks = await bootstrap_knowledge(root, manifest)
        print(f"演示知识初始化完成：{manifest['tenant_name']}，{documents} 份文档，{chunks} 个知识分块")
    await vector_store.client.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
