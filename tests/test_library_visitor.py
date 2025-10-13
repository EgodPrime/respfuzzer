from tracefuzz.library_visitor import LibraryVisitor
from tracefuzz.models import Function


def test_visit_ast():
    """Test basic functionality with numpy library"""
    visitor = LibraryVisitor("ast")
    for function in visitor.visit():
        assert isinstance(function, Function)


def test_visit_numpy():
    """Test basic functionality with numpy library"""
    visitor = LibraryVisitor("numpy")
    functions = list(visitor.visit())
    assert len(functions) > 0


def test_visit_nonexistent_library():
    """Test error handling when library is not found"""
    visitor = LibraryVisitor("nonexistent_library")
    functions = list(visitor.visit())
    assert len(functions) == 0
