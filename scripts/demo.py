"""演示脚本：seed -> 发送消息 -> 验证幂等 -> 轮询回复。

用法：先 make up，再 make demo（在 compose 网络内跑）。
"""

import asyncio
import os
import uuid

import httpx

from app.core.security import create_access_token
from scripts.seed_data import DEMO_CONVERSATION_ID, DEMO_TENANT_ID, DEMO_USER_ID, seed

API_URL = os.getenv("API_URL", "http://api:8000")


async def main() -> None:
    await seed()

    # P1 起 /webhooks/im/messages 与 /conversations/{id}/messages 要求 JWT，
    # 身份/租户只来自 token。演示用户直接用其 id 签发 token。
    token = create_access_token(
        tenant_id=str(DEMO_TENANT_ID), user_id=str(DEMO_USER_ID), role="user"
    )
    headers = {"Authorization": f"Bearer {token}"}

    message_id = f"demo-msg-{uuid.uuid4().hex[:12]}"
    payload = {
        "message_id": message_id,
        "tenant_id": str(DEMO_TENANT_ID),
        "user_id": str(DEMO_USER_ID),
        "conversation_id": str(DEMO_CONVERSATION_ID),
        "content": "你好，我想了解课程退费政策",
    }

    async with httpx.AsyncClient(base_url=API_URL, timeout=10.0) as client:
        r1 = await client.post("/webhooks/im/messages", json=payload, headers=headers)
        print(f"[1] 首次发送 -> {r1.status_code} {r1.json()}")

        r2 = await client.post("/webhooks/im/messages", json=payload, headers=headers)
        print(f"[2] 重复发送 -> {r2.status_code} {r2.json()}")

        for _ in range(20):
            await asyncio.sleep(0.5)
            r3 = await client.get(
                f"/conversations/{DEMO_CONVERSATION_ID}/messages",
                headers=headers,
            )
            msgs = r3.json().get("messages", [])
            if any(m["role"] == "assistant" for m in msgs):
                print("[3] 收到回复：")
                for m in msgs:
                    print(f"    [{m['role']}] {m['content']}")
                return
        print("[3] 超时未收到回复")


if __name__ == "__main__":
    asyncio.run(main())
