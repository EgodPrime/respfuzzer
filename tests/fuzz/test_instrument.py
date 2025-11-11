from tracefuzz.lib.fuzz.instrument import (
    instrument_function_check,
    instrument_function_via_path_check_ctx,
)


def test_instrument_function_check():
    def sample_function(x, y):
        return x + y

    instrumented_func = instrument_function_check(sample_function)

    # Before calling, the called attribute should be False
    assert not instrumented_func.called

    # Call the instrumented function
    result = instrumented_func(2, 3)
    assert result == 5

    # After calling, the called attribute should be True
    assert instrumented_func.called


def test_instrument_function_via_path_check_ctx():
    import math

    full_func_path = "math.sqrt"

    with instrument_function_via_path_check_ctx(full_func_path) as instrumented_func:
        assert instrumented_func is not None

        # Before calling, the called attribute should be False
        assert not instrumented_func.called

        # Call the instrumented function
        result = math.sqrt(16)
        assert result == 4.0

        # After calling, the called attribute should be True
        assert instrumented_func.called
