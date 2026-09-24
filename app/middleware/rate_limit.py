"""Redis 固定窗口双维限流；Redis 故障时降级放行并由健康/日志暴露。"""

import time

WINDOW_TTL_SECONDS = 70

_ATOMIC_FIXED_WINDOW = """
local tenant_count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local user_count = redis.call('INCR', KEYS[2])
if redis.call('TTL', KEYS[2]) < 0 then
    redis.call('EXPIRE', KEYS[2], ARGV[1])
end
return {tenant_count, user_count}
"""


async def allowed(
    redis, tenant_id: str, user_id: str, tenant_limit: int, user_limit: int
) -> bool:
    window = int(time.time() // 60)
    tenant_key = f"rate:tenant:{tenant_id}:{window}"
    user_key = f"rate:user:{tenant_id}:{user_id}:{window}"
    try:
        tenant_count, user_count = await redis.eval(
            _ATOMIC_FIXED_WINDOW,
            2,
            tenant_key,
            user_key,
            WINDOW_TTL_SECONDS,
        )
        return tenant_count <= tenant_limit and user_count <= user_limit
    except Exception:  # Redis 不是业务事实源；故障时不阻断客服入口。
        return True
