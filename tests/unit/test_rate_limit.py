from app.middleware.rate_limit import allowed


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.keys_seen = []

    async def eval(self, script, key_count, tenant_key, user_key, ttl):
        assert key_count == 2
        assert "INCR" in script
        assert "EXPIRE" in script
        self.keys_seen.append((tenant_key, user_key))
        for key in (tenant_key, user_key):
            self.values[key] = self.values.get(key, 0) + 1
            self.ttls[key] = int(ttl)
        return [self.values[tenant_key], self.values[user_key]]


async def test_rate_limit_applies_tenant_and_user_dimensions():
    redis = FakeRedis()
    assert await allowed(redis, "t1", "u1", tenant_limit=2, user_limit=1)
    assert not await allowed(redis, "t1", "u1", tenant_limit=2, user_limit=1)
    assert not await allowed(redis, "t1", "u2", tenant_limit=2, user_limit=10)


async def test_rate_limit_fails_open_when_redis_is_down():
    class BrokenRedis:
        async def eval(self, *args):
            raise ConnectionError

    assert await allowed(BrokenRedis(), "t1", "u1", 1, 1)


async def test_rate_limit_atomic_operation_sets_ttl_and_isolates_tenants():
    redis = FakeRedis()

    assert await allowed(redis, "tenant-a", "same-user", 10, 10)
    assert await allowed(redis, "tenant-b", "same-user", 10, 10)

    (tenant_a_key, user_a_key), (tenant_b_key, user_b_key) = redis.keys_seen
    assert tenant_a_key.startswith("rate:tenant:tenant-a:")
    assert user_a_key.startswith("rate:user:tenant-a:same-user:")
    assert tenant_b_key.startswith("rate:tenant:tenant-b:")
    assert user_b_key.startswith("rate:user:tenant-b:same-user:")
    assert set(redis.ttls.values()) == {70}


async def test_user_limit_is_shared_across_conversations():
    redis = FakeRedis()

    # conversation_id 刻意不进入 allowed 接口和 key，无法用新建会话绕过 user 配额。
    assert await allowed(redis, "tenant-a", "user-a", tenant_limit=10, user_limit=1)
    assert not await allowed(redis, "tenant-a", "user-a", tenant_limit=10, user_limit=1)
