from mplfuzz.library_visitor import LibraryVisitor
from mplfuzz.models import API


def test_visit_numpy():
    visitor = LibraryVisitor("numpy")
    for api in visitor.visit():
        assert isinstance(api, API)
        break
