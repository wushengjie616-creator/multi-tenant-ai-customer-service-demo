"""初始化两个学科演示租户：演示租户1（仅英语）、演示租户2（仅数学）。幂等。

知识库导入走 scripts/import_knowledge.py（Qdrant），本脚本只负责 PostgreSQL 的
租户 / 用户 / 会话记录。
"""

import asyncio
import uuid

from app.core.database import async_session, engine
from app.models import Conversation, Tenant, User

TENANTS = [
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000001"),
        "name": "演示租户1",
        "subject": "英语",
        "users": [
            {
                "id": uuid.UUID("10000000-0000-0000-0000-000000000101"),
                "email": "student1@example.com",
                "full_name": "示例学员-张同学",
            },
            {
                "id": uuid.UUID("10000000-0000-0000-0000-000000000102"),
                "email": "student2@example.com",
                "full_name": "示例学员-李同学",
            },
        ],
        "conversation_id": uuid.UUID("10000000-0000-0000-0000-000000000201"),
    },
    {
        "id": uuid.UUID("20000000-0000-0000-0000-000000000001"),
        "name": "演示租户2",
        "subject": "数学",
        "users": [
            {
                "id": uuid.UUID("20000000-0000-0000-0000-000000000101"),
                "email": "student3@example.com",
                "full_name": "示例学员-王同学",
            },
            {
                "id": uuid.UUID("20000000-0000-0000-0000-000000000102"),
                "email": "student4@example.com",
                "full_name": "示例学员-赵同学",
            },
        ],
        "conversation_id": uuid.UUID("20000000-0000-0000-0000-000000000201"),
    },
]


async def seed() -> None:
    async with async_session() as session:
        for tenant in TENANTS:
            if await session.get(Tenant, tenant["id"]) is None:
                session.add(Tenant(id=tenant["id"], name=tenant["name"]))
            for user in tenant["users"]:
                if await session.get(User, user["id"]) is None:
                    # password_hash 用不可登录占位（与迁移 0003 backfill 一致），
                    # 演示流程走 token 签发、不走 /auth/login。
                    session.add(
                        User(
                            id=user["id"],
                            tenant_id=tenant["id"],
                            role="user",
                            email=user["email"],
                            full_name=user["full_name"],
                            password_hash="!locked",
                        )
                    )
            first_user_id = tenant["users"][0]["id"]
            if await session.get(Conversation, tenant["conversation_id"]) is None:
                session.add(
                    Conversation(
                        id=tenant["conversation_id"],
                        tenant_id=tenant["id"],
                        user_id=first_user_id,
                    )
                )
        await session.commit()

    print("seed 完成：")
    for tenant in TENANTS:
        print(f"  {tenant['name']}（仅 {tenant['subject']}）")
        print(f"    tenant_id = {tenant['id']}")
        for user in tenant["users"]:
            print(f"      user {user['full_name']} = {user['id']}")
        print(f"    conversation_id = {tenant['conversation_id']}")


async def main() -> None:
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
