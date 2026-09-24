"""初始化演示数据：租户 / 用户 / 会话（幂等）。"""

import asyncio
import uuid

from app.core.database import async_session, engine
from app.models import Conversation, Tenant, User

DEMO_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEMO_CONVERSATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
DEMO_OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")


async def seed() -> None:
    async with async_session() as session:
        if await session.get(Tenant, DEMO_TENANT_ID) is None:
            session.add(Tenant(id=DEMO_TENANT_ID, name="演示租户"))
        if await session.get(User, DEMO_USER_ID) is None:
            # email/password_hash 自 P1 起 NOT NULL。password_hash 用不可登录占位
            # （与迁移 0003 的 backfill 一致），演示流程走 token 签发、不走 /auth/login。
            session.add(
                User(
                    id=DEMO_USER_ID,
                    tenant_id=DEMO_TENANT_ID,
                    role="user",
                    email="demo@example.com",
                    password_hash="!locked",
                )
            )
        if await session.get(Conversation, DEMO_CONVERSATION_ID) is None:
            session.add(
                Conversation(
                    id=DEMO_CONVERSATION_ID,
                    tenant_id=DEMO_TENANT_ID,
                    user_id=DEMO_USER_ID,
                )
            )
        if await session.get(User, DEMO_OTHER_USER_ID) is None:
            session.add(User(id=DEMO_OTHER_USER_ID, tenant_id=DEMO_TENANT_ID, role="user", email="other@example.com", password_hash="!locked"))
        await session.commit()
    print("seed 完成：")
    print(f"  tenant_id       = {DEMO_TENANT_ID}")
    print(f"  user_id         = {DEMO_USER_ID}")
    print(f"  conversation_id = {DEMO_CONVERSATION_ID}")


async def main() -> None:
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
