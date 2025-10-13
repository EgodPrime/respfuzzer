import pytest

from tracefuzz.db.solve_history_table import (
    create_solve_history,
    delete_solve_history,
    get_solve_history,
)
from tracefuzz.models import Argument, Function


@pytest.fixture
def sample_function():
    """Create a sample Function object for testing"""
    return Function(
        id=1,
        library_name="test_library",
        func_name="test_function",
        source="def test_function(x: int) -> str: pass",
        args=[Argument(arg_name="x", type="int", pos_type="positional")],
        ret_type="str",
    )


@pytest.fixture
def sample_history():
    """Create a sample history list for testing"""
    return [
        {
            "role": "attempter",
            "content": "Generated code: <code>import test_library; test_library.test_function(1)</code>",
        },
        {
            "role": "executor",
            "content": "Execution failed: TypeError: 'int' object is not callable",
        },
        {
            "role": "reasoner",
            "content": "The function call is incorrect. The function should be called with proper arguments.",
        },
    ]


def test_create_solve_history(sample_function, sample_history):
    """Test creating a solve history record"""
    try:
        record_id = create_solve_history(sample_function, sample_history)
        assert record_id is not None
        assert isinstance(record_id, int)
    finally:
        if record_id:
            delete_solve_history(record_id)


def test_get_solve_history(sample_function, sample_history):
    """Test retrieving a solve history record"""
    record_id = None
    try:
        record_id = create_solve_history(sample_function, sample_history)
        retrieved_history = get_solve_history(record_id)
        assert retrieved_history == sample_history
    finally:
        if record_id:
            delete_solve_history(record_id)


def test_get_nonexistent_solve_history():
    """Test retrieving a non-existent solve history record"""
    retrieved_history = get_solve_history(99999)  # Assuming this ID does not exist
    assert retrieved_history is None
