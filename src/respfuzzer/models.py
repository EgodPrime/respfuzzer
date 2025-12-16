from enum import IntEnum
import uuid
from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class Argument(BaseModel):
    arg_name: str
    type: str = "unknown"
    pos_type: str


class Function(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    library_name: str | None = None
    func_name: str
    source: str
    args: list[Argument]
    ret_type: str = "unknown"
    is_builtin: int = 0

    def __repr__(self):
        """
        Return a string representation of the function signature.
        """
        return f"{self.func_name}({', '.join(f'{arg.arg_name}[{arg.pos_type}]:{arg.type}' for arg in self.args)})->{self.ret_type}"

    def __str__(self):
        """
        Return the string representation of the function (same as __repr__).
        """
        return self.__repr__()

    @model_validator(mode="after")
    def set_library_name(self) -> "Function":
        if self.library_name is None and "." in self.func_name:
            self.library_name = self.func_name.split(".")[0]
        return self

class Seed(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    func_id: int
    library_name: str
    func_name: str
    args: list[Argument]
    function_call: str


class Mutant(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    func_id: int
    seed_id: int
    library_name: str
    func_name: str
    args: list[Argument]
    function_call: str


class HasCode(Protocol):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    library_name: str
    func_name: str
    function_call: str


class ExecutionResultType(IntEnum):
    OK = 0
    CALLFAIL = 0b001
    ABNORMAL = 0b010
    TIMEOUT = 0b100
