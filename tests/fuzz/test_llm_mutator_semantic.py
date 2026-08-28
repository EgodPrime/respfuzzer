"""Tests for the C-6 semantic validity check (Algorithm 2 IsValidSemantic).

C-6 upgraded the mutation validity gate from AST-syntax-only to "compile + target
function call exists" (aligns the implementation with the paper Algorithm 2
IsValidSemantic). These tests cover:
- code that compiles AND contains a target call           -> valid (mutant returned)
- code that fails to compile                               -> None
- code that compiles but does NOT call the target function -> None
- qualified/attribute-chain calls to the target            -> valid
- a call to a different function (false positive)          -> None
"""

from respfuzzer.lib.fuzz.llm_mutator import _contains_target_call, is_valid_semantic
from respfuzzer.models import Mutant


def _mutant(func_name: str, function_call: str) -> Mutant:
    return Mutant(
        func_id=1,
        seed_id=1,
        library_name="numpy",
        func_path=func_name,
        args=[],
        function_call=function_call,
    )


def test_valid_bare_call():
    m = _mutant("asarray", "import numpy\nresult = asarray([1, 2, 3])\n")
    assert is_valid_semantic(m) is not None


def test_valid_qualified_call():
    m = _mutant("numpy.asarray", "import numpy\nresult = numpy.asarray([1, 2, 3])\n")
    assert is_valid_semantic(m) is not None


def test_invalid_syntax_returns_none():
    m = _mutant("asarray", "def broken(:\n    pass\n")
    assert is_valid_semantic(m) is None


def test_valid_syntax_but_no_target_call_returns_none():
    # compiles fine but never calls the target function asarray
    m = _mutant("asarray", "x = 1 + 2\nprint(x)\n")
    assert is_valid_semantic(m) is None


def test_call_to_different_function_returns_none():
    # calls asarray2 (suffix mismatch) and numpy.mean, never the target asarray
    m = _mutant(
        "asarray", "import numpy\nresult = numpy.asarray2([1])\na = numpy.mean([1])\n"
    )
    assert is_valid_semantic(m) is None


def test_target_present_among_other_calls_is_valid():
    # associated-call-expansion style: target call present among other calls
    m = _mutant(
        "asarray",
        "import numpy\ny = numpy.cumsum(numpy.asarray([1,2,3]))\nz = numpy.mean([1])\n",
    )
    assert is_valid_semantic(m) is not None


def test_deep_qualified_target_name():
    m = _mutant(
        "numpy.linalg.norm", "import numpy.linalg\nresult = numpy.linalg.norm([3, 4])\n"
    )
    assert is_valid_semantic(m) is not None


def test_empty_code_returns_none():
    m = _mutant("asarray", "")
    assert is_valid_semantic(m) is None


def test_target_as_name_but_not_called_returns_none():
    # asarray appears as an identifier but never as a called function
    m = _mutant("asarray", "import numpy\nasarray = numpy.asarray2\n")
    assert is_valid_semantic(m) is None


def test_contains_target_call_helper_qualified():
    import ast

    tree = ast.parse("numpy.linalg.norm([3,4])", mode="exec")
    assert _contains_target_call("norm", tree) is True
