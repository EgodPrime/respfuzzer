from enum import IntEnum

from pydantic import BaseModel, model_validator
from inspect import _ParameterKind as PosType

class Argument(BaseModel):
    arg_name: str
    type: str = "unknown"
    pos_type: str


class Function(BaseModel):
    id: int | None = None
    library_name: str | None = None
    func_name: str
    source: str
    args: list[Argument]
    ret_type: str = "unknown"

    def __repr__(self):
        return f"{self.func_name}({", ".join(f"{arg.arg_name}[{arg.pos_type}]" for arg in self.args)})->{self.ret_type}"

    def __str__(self):
        return self.__repr__()

    @model_validator(mode="after")
    def generate_attributes(self):
        if not self.library_name:
            self.library_name = self.func_name.split(".")[0]
        return self


class ArgumentExpr(BaseModel):
    name: str
    expr: str


class Seed(BaseModel):
    id: int | None = None
    func_id: int
    library_name: str
    func_name: str
    args: list[Argument]
    function_call: str

class ChatHistory(BaseModel):
    id: int | None = None
    function_id: int
    library_name: str
    func_name: str
    history: list[dict[str,str]]


class ExecutionResultType(IntEnum):
    OK = 0
    CALLFAIL = 0b001
    ABNORMAL = 0b010
    TIMEOUT = 0b100
