from mplfuzz.models import API, MCPAPI, Argument, PosType


def test_mcp_api():
    api = MCPAPI(
        name="example.api",
        source="""api(x, y=1):
return x+y""",
        args=[
            Argument(arg_name="x", pos_type=PosType.PositionalOnly),
            Argument(arg_name="y", pos_type=PosType.KeywordOnly, default="1"),
        ],
    )
    mcp_api = MCPAPI.model_validate(api, from_attributes=True)
    code = mcp_api.to_mcp_code()
    print(code)
    assert "example__api" in code
    assert "x = eval(x)" in code
    assert "y = eval(y)" in code
    assert "result = example.api(x, y=y)" in code
