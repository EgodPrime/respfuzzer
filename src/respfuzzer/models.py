import uuid
from enum import IntEnum
from typing import Protocol

from pydantic import BaseModel, Field, model_validator



def split_public_name(public_name: str) -> tuple[str, str]:
    """把公开限定名拆成 ``(module_path, func_name)``。

    约定 ``func_path = module_path + '.' + func_name``；裸名（无包前缀）时
    ``module_path`` 为空串。

    >>> split_public_name("spacy.blank")
    ('spacy', 'blank')
    >>> split_public_name("scipy.integrate.solve_ivp")
    ('scipy.integrate', 'solve_ivp')
    """
    module_path, _, func_name = public_name.rpartition(".")
    return module_path, func_name

class Argument(BaseModel):
    arg_name: str
    type: str = "unknown"
    pos_type: str
    # 参数默认值（Python 字面量字符串，如 "1"、"'x'"、"[1, 2]"、"...")。
    # 空串 "" 表示该参数没有默认值（哨兵，与 type 的 "unknown" 同风格）；
    # 默认值为 None 时记为 "None"，与"无默认"可区分。
    default_value: str = ""


class Function(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    library_name: str | None = None
    # 完整公开路径（如 'spacy.blank'），即 module_path + '.' + func_name
    func_path: str
    # 模块/包路径（如 'spacy'），func_path 的前缀；构造时可省略，由
    # func_path 自动派生（见 set_module_and_name）
    module_path: str = ""
    # 函数名部分（如 'blank'），func_path = module_path + '.' + func_name；
    # 构造时可省略，由 func_path 自动派生
    func_name: str = ""
    # 实际源码所在模块路径（可能因别名/`__all__`/re-export 与 func_path 不同）
    real_path: str = ""
    source: str
    args: list[Argument]
    ret_type: str = "unknown"
    is_builtin: int = 0
    # 签名是否真正解析成功【MC2-11】：builtin 走 stub/inspect/docstring 三阶解析链，
    # 三源全缺时不再丢弃函数，而是留空 args/ret_type 并置 False（占位条目），
    # 由下游显式识别。旧数据（无该字段）缺省为 True，保持兼容。
    signature_resolved: bool = True
    # ---- 函数分层打标【MC6-1】（纯静态、无 LLM）----
    implementation_type: str = "pure-python"
    docstring_quality: str = "without-docstring"
    type_annotation_completeness: str = "不完整注解"
    docstring: str = ""

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

    @model_validator(mode="after")
    def set_module_and_name(self) -> "Function":
        """强制不变量 ``func_path == module_path + '.' + func_name``。

        ``module_path``/``func_name`` 留空时按 ``func_path`` 派生（单一真源），
        两者都给但与 ``func_path`` 不一致时以 ``func_path`` 为准重算。
        """
        module_path, func_name = split_public_name(self.func_path)
        if not self.module_path:
            self.module_path = module_path
        if not self.func_name:
            self.func_name = func_name
        if f"{self.module_path}.{self.func_name}".lstrip(".") != self.func_path:
            # 显式传入与路径矛盾：回到 func_path 的真源，避免下游拿到错名字
            self.module_path, self.func_name = module_path, func_name
        return self

    @model_validator(mode="after")
    def set_library_name(self) -> "Function":
        if self.library_name is None and "." in self.func_path:
            self.library_name = self.func_path.split(".")[0]
        return self


class Seed(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int >> 64)
    func_id: int
    library_name: str
    func_path: str
    args: list[Argument]
    function_call: str


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
