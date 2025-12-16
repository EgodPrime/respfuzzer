"""
Provides a small, deterministic RNG and a set of mutation helpers
ported from the original Rust implementation in this repository.
"""
import struct

# RNG state and utilities
_STATE: int = 4399
_MASK64 = (1 << 64) - 1


def set_random_state(seed: int) -> None:
    """Set the internal RNG state."""
    global _STATE
    _STATE = seed & _MASK64


def get_random_state() -> int:
    """Get the internal RNG state."""
    return _STATE


def hash64(x: int) -> int:
    x &= _MASK64
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9 & _MASK64
    x = (x ^ (x >> 27)) * 0x94D049BB133111EB & _MASK64
    x = x ^ (x >> 31)
    return x & _MASK64


def randint(max_: int) -> int:
    """Generate a pseudo-random integer in [0, max_).

    Matches the semantics of the original `ri` function: returns 0
    when `max_ <= 1` and mutates the global state.
    """
    global _STATE
    if max_ <= 1:
        return 0
    t1 = _STATE
    t1 = (t1 * 0x5DEECE66D + 0xB) & _MASK64
    t2 = t1 % max_
    _STATE = hash64(_STATE)
    return int(t2)


# Mutation constants
INTERESTING_8 = [-128, -1, 0, 1, 16, 32, 64, 100, 127]
INTERESTING_16 = [
    -32768,
    -129,
    -128,
    -1,
    0,
    1,
    16,
    32,
    64,
    100,
    127,
    128,
    255,
    256,
    512,
    1000,
    1024,
    4096,
    32767,
]
INTERESTING_32 = [
    -2147483648,
    -1006630464,
    -32769,
    -32768,
    -129,
    -128,
    -1,
    0,
    1,
    16,
    32,
    64,
    100,
    127,
    128,
    255,
    256,
    512,
    1000,
    1024,
    4096,
    32767,
    32768,
    65535,
    65536,
    100663045,
    2147483647,
]

ARITH_MAX = 35
MAX_STR_LEN = 1 * 1024 * 1024
HAVOC_BLK_SMALL = 32
HAVOC_BLK_MEDIUM = 128
HAVOC_BLK_LARGE = 1500
HAVOC_BLK_XL = 32768


def _swap16(x: int) -> int:
    return ((x << 8) | (x >> 8)) & 0xFFFF


def _swap32(x: int) -> int:
    return (((x << 24) | (x >> 24)) | ((x << 8) & 0x00FF0000) | ((x >> 8) & 0x0000FF00)) & 0xFFFFFFFF


def _choose_block_len(limit: int) -> int:
    if limit <= 0:
        return 1
    r = randint(3)
    if r == 0:
        min_v, max_v = 1, HAVOC_BLK_SMALL
    elif r == 1:
        min_v, max_v = HAVOC_BLK_SMALL, HAVOC_BLK_MEDIUM
    else:
        if randint(10) != 0:
            min_v, max_v = HAVOC_BLK_MEDIUM, HAVOC_BLK_LARGE
        else:
            min_v, max_v = HAVOC_BLK_LARGE, HAVOC_BLK_XL

    if min_v >= limit:
        min_v = 1

    return min_v + randint(min(max_v, limit) - min_v + 1)


def _bitflip_1(ar: bytearray) -> None:
    l = len(ar)
    if l == 0:
        return
    p = randint(l)
    ar[p >> 3] ^= (128 >> (p & 7)) & 0xFF


def _byte_interesting(ar: bytearray) -> None:
    l = len(ar)
    if l == 0:
        return
    p = randint(l)
    n = INTERESTING_8[randint(len(INTERESTING_8))]
    ar[p] = n & 0xFF


def _word_interesting(ar: bytearray) -> None:
    l = len(ar)
    if l < 2:
        return
    p = randint(l - 1)
    n = INTERESTING_16[randint(len(INTERESTING_16))] & 0xFFFF
    n = n if randint(2) != 0 else _swap16(n)
    ar[p : p + 2] = n.to_bytes(2, "little")


def _dword_interesting(ar: bytearray) -> None:
    l = len(ar)
    if l < 4:
        return
    p = randint(l - 3)
    n = INTERESTING_32[randint(len(INTERESTING_32))] & 0xFFFFFFFF
    n = n if randint(2) != 0 else _swap32(n)
    ar[p : p + 4] = n.to_bytes(4, "little")


def _byte_arith(ar: bytearray) -> None:
    l = len(ar)
    if l == 0:
        return
    p = randint(l)
    if randint(2) != 0:
        ar[p] = (ar[p] - (1 + randint(ARITH_MAX))) & 0xFF
    else:
        ar[p] = (ar[p] + (1 + randint(ARITH_MAX))) & 0xFF


def _word_arith(ar: bytearray) -> None:
    l = len(ar)
    if l < 2:
        return
    p = randint(l - 1)
    n = 1 + randint(ARITH_MAX)
    val = int.from_bytes(ar[p : p + 2], "little")
    if randint(2) != 0:
        if randint(2) != 0:
            mutated = (val - (1 + n)) & 0xFFFF
        else:
            mutated = _swap16((_swap16(val) - n) & 0xFFFF)
    else:
        if randint(2) != 0:
            mutated = (val + (1 + n)) & 0xFFFF
        else:
            mutated = _swap16((_swap16(val) + n) & 0xFFFF)
    ar[p : p + 2] = mutated.to_bytes(2, "little")


def _dword_arith(ar: bytearray) -> None:
    l = len(ar)
    if l < 4:
        return
    p = randint(l - 3)
    n = 1 + randint(ARITH_MAX)
    val = int.from_bytes(ar[p : p + 4], "little")
    if randint(2) != 0:
        if randint(2) != 0:
            mutated = (val - (1 + n)) & 0xFFFFFFFF
        else:
            mutated = _swap32((_swap32(val) - n) & 0xFFFFFFFF)
    else:
        if randint(2) != 0:
            mutated = (val + (1 + n)) & 0xFFFFFFFF
        else:
            mutated = _swap32((_swap32(val) + n) & 0xFFFFFFFF)
    ar[p : p + 4] = mutated.to_bytes(4, "little")


def _byte_random(ar: bytearray) -> None:
    l = len(ar)
    if l == 0:
        return
    p = randint(l)
    ar[p] ^= (1 + randint(255)) & 0xFF


def _bytes_random(ar: bytearray) -> None:
    l = len(ar)
    if l < 2:
        return
    copy_len = _choose_block_len(l - 1)
    copy_from = randint(l - copy_len + 1)
    copy_to = randint(l - copy_len + 1)
    if randint(4) != 0:
        if copy_from != copy_to:
            # emulate memmove/copy
            chunk = ar[copy_from : copy_from + copy_len]
            ar[copy_to : copy_to + copy_len] = chunk
    else:
        fill_byte = (randint(255) if randint(2) != 0 else ar[randint(l)]) & 0xFF
        for i in range(copy_to, copy_to + copy_len):
            if i < len(ar):
                ar[i] = fill_byte


def _random_delete_bytes(ar: bytearray) -> None:
    l = len(ar)
    if l < 2:
        return
    del_len = randint(l - 1)
    del_from = randint(l - del_len)
    del ar[del_from : del_from + del_len]


def _random_grow_bytes(ar: bytearray) -> None:
    l = len(ar)
    if l == 0 or l > MAX_STR_LEN:
        return
    clone_or_insert = randint(4)
    if clone_or_insert != 0:
        growth_len = _choose_block_len(l)
        growth_from = randint(l - growth_len + 1)
    else:
        growth_len = _choose_block_len(HAVOC_BLK_XL)
        growth_from = 0

    growth_to = randint(l)
    new_buf = bytearray()
    new_buf.extend(ar[:growth_to])
    if clone_or_insert != 0:
        new_buf.extend(ar[growth_from : growth_from + growth_len])
    else:
        fill_byte = (randint(256) if randint(2) != 0 else ar[randint(l)]) & 0xFF
        new_buf.extend(bytes([fill_byte]) * growth_len)
    new_buf.extend(ar[growth_to:])
    new_buf.append(0)
    ar[:] = new_buf


def _havoc(ar: bytearray, is_str: bool) -> None:
    use_stacking = 1 << (1 + randint(7))
    for _ in range(use_stacking):
        op = randint(11) if is_str else randint(9)
        if op == 0:
            _bitflip_1(ar)
        elif op == 1:
            _byte_interesting(ar)
        elif op == 2:
            _word_interesting(ar)
        elif op == 3:
            _dword_interesting(ar)
        elif op == 4:
            _byte_arith(ar)
        elif op == 5:
            _word_arith(ar)
        elif op == 6:
            _dword_arith(ar)
        elif op == 7:
            _byte_random(ar)
        elif op == 8:
            _bytes_random(ar)
        elif op == 9:
            if is_str:
                _random_delete_bytes(ar)
        elif op == 10:
            if is_str:
                _random_grow_bytes(ar)


# Public mutate helpers
def mutate_int(value: int) -> int:
    b = bytearray(value.to_bytes(4, "little", signed=True))
    _havoc(b, False)
    return int.from_bytes(bytes(b), "little", signed=True)


def mutate_float(value: float) -> float:
    b = bytearray(struct.pack("<f", float(value)))
    _havoc(b, False)
    return struct.unpack("<f", bytes(b))[0]


def mutate_str(value: str) -> str:
    b = bytearray(value.encode("utf-8", errors="replace"))
    _havoc(b, True)
    return bytes(b).decode("utf-8", errors="replace")


def mutate_bytes(value: bytes) -> bytes:
    b = bytearray(value)
    _havoc(b, False)
    return bytes(b)


__all__ = [
    "set_random_state",
    "get_random_state",
    "randint",
    "mutate_int",
    "mutate_float",
    "mutate_str",
    "mutate_bytes",
]
