import copy
from typing import Dict, FrozenSet, List, Set, Tuple

from tracefuzz.mutate import (
    randint,
    mutate_bytes,
    mutate_float,
    mutate_int,
    mutate_str,
)

VALUE_TYPES = [
    (bool, "bool"),
    (int, "int"),
    (float, "float"),
    (complex, "complex"),
    (str, "str"),
    (bytes, "bytes"),
    (List, "list"),
    (Tuple, "tuple"),
    (Set, "set"),
    (FrozenSet, "frozenset"),
    (Dict, "dict"),
    (object, "instance"),
]


def get_type(old_val) -> str:
    """
    Get the type string for a given value.

    Args:
        old_val: The value to check type for.

    Returns:
        str: The type string.

    Raises:
        ValueError: If the value type is unknown.
    """
    for type_, val in VALUE_TYPES:
        if isinstance(old_val, type_):  # type: ignore
            return val
    raise ValueError(f"Unknown value type {old_val}")


def mutate_auto(old_val):
    """
    Automatically mutate a value based on its type.
    Args:
        old_val: The value to mutate.
    Returns:
        Mutated value of the same type.
    """
    if old_val is None:
        return None
    if isinstance(old_val, object) and type(old_val) is type(object):
        return old_val
    type_ = get_type(old_val)
    func_name = f"mutate_{type_}"
    func = globals()[func_name]
    new_val = func(old_val)
    return new_val


def mutate_complex(old_val: complex) -> complex:
    new_real = mutate_float(old_val.real)
    new_imag = mutate_float(old_val.imag)
    new_val = complex(new_real, new_imag)
    return new_val


def mutate_bool(old_val: bool) -> bool:
    return not old_val


def mutate_list_clip(old_val: list) -> list:
    a = len(old_val)
    if a <= 1:
        return old_val

    b = randint(a)
    c = randint(a)
    while b == c:
        c = randint(a)
    if b > c:
        tmp = b
        b = c
        c = tmp
    return old_val[b:c]


def mutate_list_dup(old_val: list) -> list:
    dup_times = 2 + randint(8)
    new_val = old_val * dup_times
    MAX_LEN = 100000
    new_val = new_val[:MAX_LEN]
    return new_val


def mutate_list_expand(old_val: list) -> list:
    a = len(old_val)
    if a == 0:
        return old_val
    index = randint(len(old_val) - 1)
    t = old_val[index]
    new_t = mutate_auto(t)
    new_val = copy.deepcopy(old_val)
    new_val.append(new_t)
    return new_val


def mutate_list_random_one(old_val: list) -> list:
    a = len(old_val)
    if a == 0:
        return old_val
    elif a == 1:
        return [mutate_auto(old_val[0])]
    b = randint(a - 1)
    new_val = mutate_auto(old_val[b])
    return old_val[:b] + [new_val] + old_val[b + 1 :]


def mutate_list(old_val: list) -> list:
    a = [mutate_list_dup, mutate_list_random_one, mutate_list_expand, mutate_list_clip]
    b = randint(len(a))
    mt = a[b]
    return mt(old_val)


def mutate_tuple(old_val: tuple) -> tuple:
    return tuple(mutate_list(list(old_val)))


def mutate_bytearray(old_val: bytearray) -> bytearray:
    tmp_val = list(old_val)
    new_val = [min(max(0, x), 255) for x in mutate_list(tmp_val)]
    return bytearray(new_val)


def mutate_set(old_val: set) -> set:
    a = [mutate_list_random_one, mutate_list_expand]
    b = randint(len(a))
    mt = a[b]
    return set(mt(list(old_val)))


def mutate_frozenset(old_val: frozenset) -> frozenset:
    return frozenset(mutate_set(set(old_val)))


def mutate_dict(old_val: dict) -> dict:
    new_val = copy.deepcopy(old_val)
    keys = list(new_val.keys())
    if len(keys) == 0:
        return old_val
    a = randint(len(keys))
    mt_key = keys[a]
    new_val[mt_key] = mutate_auto(new_val[mt_key])
    return new_val


def mutate_instance(old_val: object) -> object:
    try:
        new_val = copy.deepcopy(old_val)
        members = dir(new_val)
        members = [x for x in members if not x.startswith("__")]
        if len(members) == 0:
            return old_val
        a = randint(len(members))
        mt_member = members[a]
        new_member = mutate_auto(getattr(new_val, mt_member))
        setattr(new_val, mt_member, new_member)
    except Exception:
        pass
    return new_val


def mutate_param_list(old_val: List[object]) -> List:
    a = len(old_val)
    if a <= 1:
        return old_val
    new_val = copy.deepcopy(old_val)
    mt_num = randint(a) + 1
    mt_idx = []
    while mt_num > 0:
        x = randint(a)
        if x not in mt_idx:
            mt_idx.append(x)
            mt_num -= 1
    for i in mt_idx:
        new_val[i] = mutate_auto(new_val[i])
    return new_val


__all__ = [
    "mutate_auto",
    "mutate_bool",
    "mutate_int",
    "mutate_float",
    "mutate_complex",
    "mutate_str",
    "mutate_bytes",
    "mutate_list",
    "mutate_tuple",
    "mutate_bytearray",
    "mutate_set",
    "mutate_frozenset",
    "mutate_dict",
    "mutate_instance",
    "mutate_param_list",
]
