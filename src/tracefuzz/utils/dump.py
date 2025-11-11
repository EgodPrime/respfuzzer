import pickle
from typing import Any


def dump_any_obj(obj: Any) -> bytes:
    res = b""
    try:
        res = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        res = str(obj).encode("utf-8")
    return res
