import sqlite3
from contextlib import contextmanager

import pytest

from tracefuzz.models import Argument, Seed
from tracefuzz.repos import seed_table
from tracefuzz.repos.seed_table import (
    create_seed,
    create_seeds,
    get_seed,
    get_seed_by_function_id,
    get_seed_by_function_name,
    get_seeds,
    get_seeds_iter,
)


@pytest.fixture
def sample_seed():
    return Seed(
        id=None,
        func_id=42,
        library_name="libx",
        func_name="libx.foo",
        args=[Argument(arg_name="a", type="int", pos_type="positional")],
        function_call="libx.foo(1)",
    )


@pytest.fixture(autouse=True)
def use_in_memory_db(monkeypatch):
    """Provide an in-memory sqlite DB and monkeypatch seed_table.get_db_cursor to use it."""
    conn = sqlite3.connect(":memory:")

    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS seed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            func_id INTEGER,
            library_name TEXT,
            func_name TEXT,
            args TEXT,
            function_call TEXT
        )"""
    )
    conn.commit()

    @contextmanager
    def _get_db_cursor(commit: bool = True):
        c = conn.cursor()
        try:
            yield c
            if commit:
                conn.commit()
        finally:
            c.close()

    monkeypatch.setattr(seed_table, "get_db_cursor", _get_db_cursor)

    yield

    conn.close()


def test_create_and_get_seed(sample_seed):
    sid = create_seed(sample_seed)
    assert isinstance(sid, int)

    retrieved = get_seed(sid)
    assert retrieved is not None
    assert retrieved.func_id == sample_seed.func_id
    assert retrieved.library_name == sample_seed.library_name
    assert retrieved.func_name == sample_seed.func_name
    assert retrieved.function_call == sample_seed.function_call
    assert len(retrieved.args) == 1
    assert retrieved.args[0].arg_name == sample_seed.args[0].arg_name

    # cleanup
    with seed_table.get_db_cursor() as cur:
        cur.execute("DELETE FROM seed WHERE id = ?", (sid,))


def test_create_seeds_and_getters(sample_seed):
    s1 = Seed(**sample_seed.model_dump())
    s1.func_id = 1
    s1.library_name = "libA"
    s1.func_name = "libA.one"

    s2 = Seed(**sample_seed.model_dump())
    s2.func_id = 2
    s2.library_name = "libA"
    s2.func_name = "libA.two"

    ids = create_seeds([s1, s2])
    assert isinstance(ids, list) and len(ids) == 2

    # get by function name
    got = get_seed_by_function_name("libA.one")
    assert got is not None and got.func_name == "libA.one"

    # get by function id
    got2 = get_seed_by_function_id(2)
    assert got2 is not None and got2.func_id == 2

    # get_seeds by library
    seeds_lib = get_seeds(library_name="libA")
    names = {s.func_name for s in seeds_lib}
    assert "libA.one" in names and "libA.two" in names

    # get_seeds without filters should return all
    all_seeds = get_seeds()
    assert len(all_seeds) >= 2

    # iterator
    iter_names = {s.func_name for s in get_seeds_iter(library_name="libA")}
    assert "libA.one" in iter_names and "libA.two" in iter_names

    # cleanup
    with seed_table.get_db_cursor() as cur:
        cur.execute("DELETE FROM seed WHERE id IN (?, ?)", (ids[0], ids[1]))


def test_get_seeds_with_both_filters(sample_seed):
    """Cover branch where both library_name and func_name filters are used (AND)."""
    a = Seed(**sample_seed.model_dump())
    a.func_id = 10
    a.library_name = "L"
    a.func_name = "L.alpha"

    b = Seed(**sample_seed.model_dump())
    b.func_id = 11
    b.library_name = "L"
    b.func_name = "L.beta"

    id_a = create_seed(a)
    id_b = create_seed(b)
    try:
        # filter by both library and func_name -> should only return the matching single row
        res = get_seeds(library_name="L", func_name="L.alpha")
        assert len(res) == 1 and res[0].func_name == "L.alpha"

        iter_res = list(get_seeds_iter(library_name="L", func_name="L.alpha"))
        assert len(iter_res) == 1 and iter_res[0].func_name == "L.alpha"
    finally:
        with seed_table.get_db_cursor() as cur:
            cur.execute("DELETE FROM seed WHERE id IN (?, ?)", (id_a, id_b))


def test_nonexistent_returns_none():
    assert get_seed(99999) is None
    assert get_seed_by_function_name("no.such.func") is None
    assert get_seed_by_function_id(99999) is None
