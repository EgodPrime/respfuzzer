import copy
from typing import Any, Dict, FrozenSet, Iterator, List, Set, Tuple

from respfuzzer.lib.fuzz.mutate import (
    mutate_bytes,
    mutate_float,
    mutate_int,
    mutate_str,
    randint,
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


def get_type(val: Any) -> str:
    """
    Get the type string for a given value.

    Args:
        val: The value to check type for.

    Returns:
        str: The type string.
    """
    for t, val in VALUE_TYPES:
        if isinstance(val, t):  # type: ignore
            return val
    return "other"


def mutate_auto(old_val: Any) -> Any:
    """
    Automatically mutate a value based on its type.
    Args:
        old_val: The value to mutate.
    Returns:
        Mutated value of the same type.
    """
    _type = get_type(old_val)
    match _type:
        case "bool":
            return mutate_bool(old_val)
        case "int":
            return mutate_int(old_val)
        case "float":
            return mutate_float(old_val)
        case "complex":
            return mutate_complex(old_val)
        case "str":
            return mutate_str(old_val)
        case "bytes":
            return mutate_bytes(old_val)
        case "list":
            return mutate_list(old_val)
        case "tuple":
            return mutate_tuple(old_val)
        case "bytearray":
            return mutate_bytearray(old_val)
        case "set":
            return mutate_set(old_val)
        case "frozenset":
            return mutate_frozenset(old_val)
        case "dict":
            return mutate_dict(old_val)
        case "instance":
            return mutate_instance(old_val)
        case _:
            return old_val


def mutate_bool(old_val: bool) -> bool:
    return not old_val


def mutate_complex(old_val: complex) -> complex:
    new_real = mutate_float(old_val.real)
    new_imag = mutate_float(old_val.imag)
    new_val = complex(new_real, new_imag)
    return new_val


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
    old_val.append(new_t)
    return old_val


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
    keys = list(old_val.keys())
    if len(keys) == 0:
        return old_val
    a = randint(len(keys))
    mt_key = keys[a]
    old_val[mt_key] = mutate_auto(old_val[mt_key])
    return old_val


def mutate_instance(old_val: object) -> object:
    try:
        members = dir(old_val)
        members = [x for x in members if not x.startswith("__")]
        if len(members) == 0:
            return old_val
        a = randint(len(members))
        mt_member = members[a]
        new_member = mutate_auto(getattr(old_val, mt_member))
        setattr(old_val, mt_member, new_member)
    except Exception:
        pass
    return old_val


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


def mutate_param_list_deterministic(old_pl: List[object]) -> Iterator[List[object]]:
    """
    This version generates all possible single mutations for each parameter in the list.
    """
    a = len(old_pl)
    if a == 0:
        yield old_pl
        return
    for i in range(a):
        original_value = old_pl[i]
        try:
            new_value = copy.deepcopy(original_value)
            for _ in range(10):
                mutated_value = mutate_auto(new_value)
                new_pl = copy.deepcopy(old_pl)
                new_pl[i] = mutated_value
                yield new_pl
                new_value = mutated_value
        except Exception:
            continue
