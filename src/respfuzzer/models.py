import uuid
from enum import IntEnum
from typing import Protocol

from pydantic import BaseModel, Field, computed_field, model_validator


class Argument(BaseModel):
    arg_name: str
    type: str = "unknown"
    pos_type: str
    default_value: str = ""  # 参数默认值（Python 字面量字符串）；空串=无默认，'None'=默认值为 None，与 type 的 'unknown' 哨兵风格一致



def _coerce_func_path(data):
    """旧数据兼容（F4）：老 JSON 记录用键 `func_name`，新模型字段为 `func_path`。

    读取旧 data/*.json 时把传入 dict 的 `func_name` 前置映射为 `func_path`，
    使旧数据无需迁移脚本即可被 Function/Seed.model_validate 加载；
    新数据本身用 `func_path`，此钩子无副作用。"""
    if isinstance(data, dict) and "func_path" not in data and "func_name" in data:
        data["func_path"] = data.pop("func_name")
    return data

class Function(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    library_name: str | None = None
    func_path: str  # 公开导出路径（func_path；Q1 全量改名自 func_name）
    real_path: str = ""
    source: str
    args: list[Argument]
    ret_type: str = "unknown"
    is_builtin: int = 0
    # --- ED-14a 分层元数据 ---
    function_type: str = "user_defined"  # 'user_defined' | 'built_in'；提取时由 is_builtin 映射
    docstring: str = ""                  # 可空；ED-14a 方案B：进入 RQ1 seed prompt
    # C-10 分桶落地：签名是否解析成功。False = 三源(stub/inspect/docstring)全缺，
    # 不再 drop 也不再抛错——失败字段留空并收录，由下游显式识别/过滤。
    signature_resolved: bool = True

    @computed_field
    @property
    def has_full_type_hints(self) -> bool:
        '''全 Argument.type 均非 unknown/空 才算满类型注解（ED-14a，由 args 派生）。'''
        # 未解析（signature_resolved=False）的占位条目不算满注解：空 args 的 all() 是空真，
        # 必须显式短路避免把失败占位误判为满注解（失败字段留空）。
        return self.signature_resolved and all(
            a.type not in ("unknown", "") for a in self.args
        )

    def __repr__(self):
        """
        Return a string representation of the function signature.
        """
        return f"{self.func_path}({', '.join(f'{arg.arg_name}[{arg.pos_type}]:{arg.type}' for arg in self.args)})->{self.ret_type}"

    def __str__(self):
        """
        Return the string representation of the function (same as __repr__).
        """
        return self.__repr__()


    @model_validator(mode="before")
    @classmethod
    def _coerce_old_func_name(cls, data):
        return _coerce_func_path(data)
    @model_validator(mode="after")
    def set_library_name(self) -> "Function":
        if self.library_name is None and "." in self.func_path:
            self.library_name = self.func_path.split(".")[0]
        return self



class LibraryModule(BaseModel):
    """pub_mod 模型（C-10 两段法，§8.2）。

    双层原则在模块层面的延续（是 `Function.func_path/real_path` 的模块级对应物）：
    - `mod_path`：公开模块路径（func_path 在模块层的标签），由「公开属性绑定链」登记；
    - `real_path`：物理模块身份（= 模块对象 `__name__`），普通公开模块与 mod_path 相同，
      模块级别名（如 `import a.b._c as c`）时二者分叉。
    - `is_public`：隐私判定判给记录路径 `mod_path`，**不判物理名 `real_path`**——
      否则公开别名的私有模块会被误判为私有而漏掉其函数。
    """
    real_path: str  # 物理模块身份
    mod_path: str   # 公开模块路径

    @computed_field
    @property
    def is_public(self) -> bool:
        return not any(seg.startswith("_") for seg in self.mod_path.split("."))
class Seed(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    func_id: int
    library_name: str
    func_path: str
    args: list[Argument]
    function_call: str


    @model_validator(mode="before")
    @classmethod
    def _coerce_old_func_name(cls, data):
        return _coerce_func_path(data)

class Mutant(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    func_id: int
    seed_id: int
    library_name: str
    func_path: str
    args: list[Argument]
    function_call: str


class HasCode(Protocol):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    library_name: str
    func_path: str
    function_call: str


class ExecutionResultType(IntEnum):
    OK = 0
    CALLFAIL = 0b001
    ABNORMAL = 0b010
    TIMEOUT = 0b100
