import sqlite3
from contextlib import contextmanager

import pytest

from tracefuzz.models import Argument, Function
from tracefuzz.repos import function_table
from tracefuzz.repos.function_table import (
    create_function,
    get_function,
    get_function_iter,
    get_functions,
)


@pytest.fixture
def sample_function():
    """Create a sample Function object for testing"""
    return Function(
        id=None,
        library_name="test_library",
        func_name="test_library.test_function",
        source="def test_function(x: int) -> str: pass",
        args=[Argument(arg_name="x", type="int", pos_type="positional")],
        ret_type="str",
    )


# Use an in-memory SQLite DB for tests in this module to avoid touching real data.
@pytest.fixture(autouse=True)
def use_in_memory_db(monkeypatch):
    conn = sqlite3.connect(":memory:")

    # create the function table schema in the shared in-memory DB
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS function (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            func_name TEXT, 
            library_name TEXT,
            source TEXT, 
            args TEXT, 
            ret_type TEXT,
            is_builtin INTEGER DEFAULT 0
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

    # Patch the get_db_cursor used inside the function_table module to use the in-memory DB
    monkeypatch.setattr(function_table, "get_db_cursor", _get_db_cursor)

    yield

    conn.close()


def test_create_and_get_function(sample_function):
    """Test creating a function record and retrieving it"""
    record_id = None
    try:
        record_id = create_function(sample_function)
        assert record_id is not None
        # get_function looks up by func_name (stored as the full name)
        retrieved = get_function(sample_function.func_name)
        assert retrieved is not None
        assert sample_function.func_name == retrieved.func_name
    finally:
        # cleanup
        if record_id:
            from tracefuzz.repos.base import get_db_cursor

            with get_db_cursor() as cur:
                cur.execute("DELETE FROM function WHERE id = ?", (record_id,))


def test_get_functions_and_iter(sample_function):
    """Test get_functions and get_function_iter with library filter"""
    record_ids = []
    try:
        # insert two functions for same library
        f1 = Function(**sample_function.model_dump())
        f1.func_name = f"{sample_function.library_name}.func_a"
        f2 = Function(**sample_function.model_dump())
        f2.func_name = f"{sample_function.library_name}.func_b"

        id1 = create_function(f1)
        id2 = create_function(f2)
        record_ids.extend([id1, id2])

        funcs = get_functions(sample_function.library_name)
        # Expect at least the two we inserted
        names = {f.func_name for f in funcs}
        assert f1.func_name in names
        assert f2.func_name in names

        # Test iterator
        iter_names = {
            f.func_name for f in get_function_iter(sample_function.library_name)
        }
        assert f1.func_name in iter_names
        assert f2.func_name in iter_names
    finally:
        if record_ids:
            from tracefuzz.repos.base import get_db_cursor

            with get_db_cursor() as cur:
                cur.execute(
                    "DELETE FROM function WHERE id IN ({seq})".format(
                        seq=",".join("?" for _ in record_ids)
                    ),
                    tuple(record_ids),
                )


def test_get_nonexistent_function():
    """Requesting a non-existent function should return None"""
    assert get_function("this_function_does_not_exist") is None


def test_get_functions_with_none(sample_function):
    """When library_name is None, get_functions should return all functions"""
    # insert one record
    f = Function(**sample_function.model_dump())
    f.func_name = "otherlib.foo"
    _id = create_function(f)
    try:
        funcs = get_functions(None)
        names = {x.func_name for x in funcs}
        assert "otherlib.foo" in names
    finally:
        from tracefuzz.repos.base import get_db_cursor

        with get_db_cursor() as cur:
            cur.execute("DELETE FROM function WHERE id = ?", (_id,))


def test_get_function_iter_none(sample_function):
    """Iterator without library filter should yield all functions"""
    f = Function(**sample_function.model_dump())
    f.func_name = "yetanother.func"
    _id = create_function(f)
    try:
        iter_names = {ff.func_name for ff in get_function_iter(None)}
        assert "yetanother.func" in iter_names
    finally:
        from tracefuzz.repos.base import get_db_cursor

        with get_db_cursor() as cur:
            cur.execute("DELETE FROM function WHERE id = ?", (_id,))
