from respfuzzer.lib.fuzz.mutate import (
    get_random_state,
    mutate_bytes,
    mutate_float,
    mutate_int,
    mutate_str,
    randint,
    set_random_state,
)


def test_set_get_random_state():
    s1 = get_random_state()
    set_random_state(s1 * 2)
    s2 = get_random_state()
    assert s2 == s1 * 2


def test_randint():
    max_value = 100
    r = randint(max_value)
    assert 0 <= r < max_value


def test_chain_rand():
    for _ in range(10):
        randint(2144)
    s = get_random_state()
    sample1 = []
    for _ in range(10):
        sample1.append(randint(123456))
    sample2 = []
    set_random_state(s)
    for _ in range(10):
        sample2.append(randint(123456))
    assert sample1 == sample2


def test_int_mutate():
    original = 234123
    mutated = mutate_int(original)
    assert isinstance(mutated, int)
    assert mutated != original  # Ensure mutation occurred


def test_float_mutate():
    original = 3.14
    mutated = mutate_float(original)
    assert isinstance(mutated, float)
    assert mutated != original  # Ensure mutation occurred


def test_str_mutate():
    original = "hello"
    mutated = mutate_str(original)
    assert isinstance(mutated, str)
    assert mutated != original  # Ensure mutation occurred


def test_bytes_mutate():
    original = b"hello"
    mutated = mutate_bytes(original)
    assert isinstance(mutated, bytes)
    assert mutated != original  # Ensure mutation occurred


def test_a_lot_of_mutations():
    original_int = 123456
    original_float = 1.23456
    original_str = "fuzzing"
    original_bytes = b"fuzzing"

    for _ in range(100000):
        mutated_int = mutate_int(original_int)
        mutated_float = mutate_float(original_float)
        mutated_str = mutate_str(original_str)
        mutated_bytes = mutate_bytes(original_bytes)

        assert isinstance(mutated_int, int)
        assert isinstance(mutated_float, float)
        assert isinstance(mutated_str, str)
        assert isinstance(mutated_bytes, bytes)
