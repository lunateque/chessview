"""Redis-backed fixed-window rate limiting."""

from infrastructure.redis import get_redis_client


async def check(bucket: str, identity: str, limit: int, window_s: int) -> bool:
    client = get_redis_client()
    key = f"rl:{bucket}:{identity}"
    count = int(await client.incr(key))
    if count == 1:
        await client.expire(key, window_s)
    return count <= limit
