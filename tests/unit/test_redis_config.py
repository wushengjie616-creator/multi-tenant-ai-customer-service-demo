from app.core.config import Settings
from app.core.redis import create_redis_client


def test_redis_context_and_pool_defaults_are_explicit():
    configured = Settings(
        context_max_messages=7,
        context_ttl_seconds=123,
        redis_max_connections=9,
    )

    assert configured.context_max_messages == 7
    assert configured.context_ttl_seconds == 123
    assert configured.redis_max_connections == 9


async def test_redis_client_consumes_connection_pool_limit(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "7")

    client = create_redis_client()
    try:
        assert client.connection_pool.max_connections == 7
    finally:
        await client.aclose()
