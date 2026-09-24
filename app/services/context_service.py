"""Redis 短期会话上下文。

PostgreSQL 始终是完整消息历史的事实源；本模块仅维护可过期的最近消息副本。
"""

import json

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.models import Conversation, Message

log = get_logger(__name__)


class ConversationContextStore:
    def __init__(self, redis, *, max_messages: int, ttl_seconds: int):
        self.redis = redis
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self._needs_rebuild: set[str] = set()

    async def append(self, tenant_id, conversation_id, message: dict) -> bool:
        return await self.append_many(tenant_id, conversation_id, [message])

    async def append_many(
        self, tenant_id, conversation_id, messages: list[dict]
    ) -> bool:
        if not messages:
            return True
        key = self._key(tenant_id, conversation_id)
        encoded = [json.dumps(self._normalize(message), ensure_ascii=False) for message in messages]
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.rpush(key, *encoded)
                pipe.ltrim(key, -self.max_messages, -1)
                pipe.expire(key, self.ttl_seconds)
                await pipe.execute()
            return True
        except Exception as exc:  # Redis 是可重建副本，写失败不阻断聊天。
            self._needs_rebuild.add(key)
            log.warning(
                "redis context append failed tenant_id=%s conversation_id=%s error=%s",
                tenant_id,
                conversation_id,
                type(exc).__name__,
            )
            return False

    async def read(self, tenant_id, conversation_id) -> list[dict]:
        values = await self.redis.lrange(self._key(tenant_id, conversation_id), 0, -1)
        return [self._normalize(json.loads(value)) for value in values]

    async def get_for_llm(
        self,
        session,
        tenant_id,
        conversation_id,
        *,
        current_message_id: str | None = None,
    ) -> list[dict]:
        key = self._key(tenant_id, conversation_id)
        summary = await session.scalar(select(Conversation.summary).where(
            Conversation.tenant_id == tenant_id,
            Conversation.id == conversation_id,
        ))
        summary_message = ([{
            "message_id": "conversation-summary",
            "role": "system",
            "content": f"早期会话摘要：\n{summary}",
        }] if summary else [])
        cache_available = True
        cached = []
        if key not in self._needs_rebuild:
            try:
                cached = await self.read(tenant_id, conversation_id)
            except Exception as exc:
                cache_available = False
                self._needs_rebuild.add(key)
                log.warning(
                    "redis context read failed tenant_id=%s conversation_id=%s error=%s",
                    tenant_id,
                    conversation_id,
                    type(exc).__name__,
                )

        if cached:
            return summary_message + self._without_current(cached, current_message_id)

        rows = await session.scalars(
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.conversation_id == conversation_id,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(self.max_messages + (1 if current_message_id else 0))
        )
        history = [
            {
                "message_id": row.message_id,
                "role": row.role,
                "content": row.content,
            }
            for row in reversed(rows.all())
        ]
        history = self._without_current(history, current_message_id)[-self.max_messages :]
        if history and cache_available:
            await self._replace(tenant_id, conversation_id, history)
        return summary_message + history

    async def _replace(self, tenant_id, conversation_id, messages: list[dict]) -> bool:
        key = self._key(tenant_id, conversation_id)
        encoded = [json.dumps(self._normalize(message), ensure_ascii=False) for message in messages]
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.delete(key)
                if encoded:
                    pipe.rpush(key, *encoded)
                    pipe.ltrim(key, -self.max_messages, -1)
                    pipe.expire(key, self.ttl_seconds)
                await pipe.execute()
            self._needs_rebuild.discard(key)
            return True
        except Exception as exc:
            self._needs_rebuild.add(key)
            log.warning(
                "redis context rebuild failed tenant_id=%s conversation_id=%s error=%s",
                tenant_id,
                conversation_id,
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _normalize(message: dict) -> dict:
        return {
            "message_id": str(message.get("message_id", "")),
            "role": str(message["role"]),
            "content": str(message["content"]),
        }

    @staticmethod
    def _without_current(messages: list[dict], current_message_id: str | None) -> list[dict]:
        if not current_message_id:
            return messages
        return [message for message in messages if message.get("message_id") != current_message_id]

    @staticmethod
    def _key(tenant_id, conversation_id) -> str:
        return f"context:{tenant_id}:{conversation_id}"


conversation_context = ConversationContextStore(
    redis_client,
    max_messages=settings.context_max_messages,
    ttl_seconds=settings.context_ttl_seconds,
)
