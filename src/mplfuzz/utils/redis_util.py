import redis

from mplfuzz.utils.config import get_config


def get_redis_client():
    """
    获取 Redis 客户端实例
    """
    config = get_config("redis").unwrap()
    return redis.Redis(host=config["host"], port=config["port"], db=config["db"], decode_responses=True)
