"""Redis-backed fixed-window rate limiting."""

from infrastructure.redis import get_redis_client


FIXED_WINDOW_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
local ttl = redis.call("TTL", KEYS[1])
if count == 1 or ttl < 0 then
    redis.call("EXPIRE", KEYS[1], ARGV[2])
end
if count <= tonumber(ARGV[1]) then
    return 1
end
return 0
"""


async def check(bucket: str, identity: str, limit: int, window_s: int) -> bool:
    client = get_redis_client()
    key = f"rl:{bucket}:{identity}"
    allowed = await client.eval(FIXED_WINDOW_SCRIPT, 1, key, limit, window_s)
    return bool(allowed)
