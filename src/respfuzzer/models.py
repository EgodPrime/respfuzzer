import uuid
from enum import IntEnum
from typing import Protocol

from pydantic import BaseModel, Field, computed_field, model_validator


class Argument(BaseModel):
    arg_name: str
    type: str = "unknown"
    pos_type: str
    default_value: str = ""  # 参数默认值（Python 字面量字符串）；空串=无默认，'None'=默认值为 None，与 type 的 'unknown' 哨兵风格一致


class Function(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    library_name: str | None = None
    func_name: str
    real_path: str = ""
    source: str
    args: list[Argument]
    ret_type: str = "unknown"
    is_builtin: int = 0
    # --- ED-14a 分层元数据 ---
    function_type: str = "user_defined"  # 'user_defined' | 'built_in'；提取时由 is_builtin 映射
    docstring: str = ""                  # 可空；ED-14a 方案B：进入 RQ1 seed prompt

    @computed_field
    @property
    def has_full_type_hints(self) -> bool:
        """全 Argument.type 均非 'unknown'/'' 才算满类型注解（ED-14a，由 args 派生）。"""
        return all(a.type not in ("unknown", "") for a in self.args)

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
