from enum import IntEnum
from typing import override

from pydantic import BaseModel, model_validator


class PosType(IntEnum):
    PositionalOnly = 1
    KeywordOnly = 2


class Argument(BaseModel):
    arg_name: str
    type: str = "unknown"
    pos_type: int


class API(BaseModel):
    id: int|None = None
    library_name: str|None = None
    api_name: str
    source: str
    args: list[Argument]
    ret_type: str = "unknown"

    def __repr__(self):
        return f"{self.api_name}({", ".join(f"{arg.arg_name}[{arg.pos_type}]" for arg in self.args)})->{self.ret_type}"

    def __str__(self):
        return self.__repr__()
    
    @model_validator(mode="after")
    def generate_attributes(self):
        if not self.library_name:
            self.library_name = self.api_name.split(".")[0]
        return self


class MCPCode(BaseModel):
    id: int|None = None
    api_id: int
    library_name: str
    api_name: str
    mcpcode: str


class ArgumentExpr(BaseModel):
    name: str
    expr: str


class Solution(BaseModel):
    id: int|None = None
    api_id: int
    library_name: str
    api_name: str
    args: list[Argument]
    arg_exprs: list[ArgumentExpr]
    apicall_expr: str | None = None

    @model_validator(mode="after")
    def generate_attributes(self):
        # 构建一个从 name -> expr 的映射
        expr_map = {expr.name: expr.expr for expr in self.arg_exprs}
        # 构建参数表达式
        args_expr = []
        for arg in self.args:
            if arg.arg_name in expr_map:
                match arg.pos_type:
                    case 1:
                        args_expr.append(expr_map[arg.arg_name])
                    case 2:
                        args_expr.append(f"arg.name={expr_map[arg.arg_name]}")
        self.apicall_expr = f"{self.api_name}({', '.join(args_expr)})"
        return self


class Mutant(BaseModel):
    id: int|None = None
    solution_id: int
    library_name: str
    api_name: str
    apicall_expr_ori: str
    apicall_expr_new: str


class ExecutionResultType(IntEnum):
    OK = 0
    CALLFAIL = 0b001
    ABNORMAL = 0b010
    TIMEOUT = 0b100


class MutantExecution(BaseModel):
    id: int
    mutant_id: int
    library_name: str
    api_name: str
    result_type: int
    ret_code: int
    stdout: str
    stderr: str
