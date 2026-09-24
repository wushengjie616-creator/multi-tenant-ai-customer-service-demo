"""Redis 客户端工厂：所有进程统一消费连接池配置。"""

from redis.asyncio import from_url

from app.core.config import Settings, settings


def create_redis_client(config: Settings | None = None):
    current = config or Settings()
    return from_url(
        current.redis_url,
        decode_responses=True,
        max_connections=current.redis_max_connections,
    )


redis_client = create_redis_client(settings)
