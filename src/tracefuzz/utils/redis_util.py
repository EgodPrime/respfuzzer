import redis

from tracefuzz.utils.config import get_config


def get_redis_client() -> redis.Redis:
    """
    获取 Redis 客户端实例
    """
    config = get_config("redis")
    return redis.Redis(
        host=config["host"], port=config["port"], db=config["db"], decode_responses=True
    )
