from enum import IntEnum
from typing import override

from pydantic import BaseModel


class PosType(IntEnum):
    PositionalOnly = 1
    KeywordOnly = 2


class Argument(BaseModel):
    name: str
    type: str = "unknown"
    pos_type: int


class ArgumentExpr(BaseModel):
    name: str
    expr: str


class Solution(BaseModel):
    api_name: str
    api_exprs: list[ArgumentExpr]
    expect_result: str

    def __repr__(self):
        return f"{self.api_name}({", ".join([f"{arg_expr.name}={arg_expr.expr}" for arg_expr in self.api_exprs])})"

    def __str__(self):
        return self.__repr__()

class API(BaseModel):
    name: str
    source: str
    args: list[Argument]
    ret_type: str = "unknown"
    mcp_code: str = ""
    solutions: list[Solution] = []

    def __repr__(self):
        return f"{self.name}({", ".join(f"{arg.name}[{arg.pos_type}]" for arg in self.args)})->{self.ret_type}"

    def __str__(self):
        return self.__repr__()