from tracefuzz.models import Function

def test_function_str():
    func = Function(
        func_name="math.add",
        source="def add(a, b): return a + b",
        args=[
            {"arg_name": "a", "type": "int", "pos_type": "positional"},
            {"arg_name": "b", "type": "int", "pos_type": "positional"},
        ],
        ret_type="int",
    )
    expected_str = "math.add(a[positional]:int, b[positional]:int)->int"
    assert str(func) == expected_str    
