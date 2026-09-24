"""Seed an idempotent multi-tenant identity pool for reproducible load tests."""

import asyncio
import os

from app.core.database import async_session, engine
from app.models import Conversation, Tenant, User
from loadtests.identities import identity_for


async def seed(pool_size: int) -> None:
    async with async_session() as session:
        for index in range(pool_size):
            identity = identity_for(index)
            if await session.get(Tenant, identity.tenant_id) is None:
                session.add(
                    Tenant(
                        id=identity.tenant_id,
                        name=f"压测租户-{index:04d}",
                        features=["assistant", "knowledge", "finance", "reminder"],
                    )
                )
            if await session.get(User, identity.user_id) is None:
                session.add(
                    User(
                        id=identity.user_id,
                        tenant_id=identity.tenant_id,
                        role="user",
                        email=f"load-{index:04d}@example.invalid",
                        password_hash="!loadtest-locked",
                    )
                )
            if await session.get(Conversation, identity.conversation_id) is None:
                session.add(
                    Conversation(
                        id=identity.conversation_id,
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                    )
                )
        await session.commit()
    print(f"seeded {pool_size} deterministic load-test identities")


async def main() -> None:
    pool_size = int(os.getenv("LOADTEST_POOL_SIZE", "500"))
    if not 1 <= pool_size <= 10_000:
        raise ValueError("LOADTEST_POOL_SIZE must be between 1 and 10000")
    await seed(pool_size)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
