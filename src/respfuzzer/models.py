import uuid
from enum import IntEnum
from typing import Protocol

from pydantic import AliasChoices, BaseModel, Field, model_validator


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
    # 兼容旧数据集键名【MC6-2】：存量 data/*_functions.json（历史分支产物）用
    # ``func_path`` 存公开导出路径，新提取路径与代码统一用 ``func_name``；
    # 两者语义相同，读入时任一键都接受（序列化仍只写 ``func_name``）。
    func_name: str = Field(validation_alias=AliasChoices("func_name", "func_path"))
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
    # 实现类型：pure-python（Python 源码可提取的纯 Python 函数）/ extended（C 扩展等内置实现）
    implementation_type: str = "pure-python"
    # 文档质量：with-docstring（docstring 去空白后非空）/ without-docstring
    docstring_quality: str = "without-docstring"
    # 类型注解完整性：完整注解（具名参数与返回值全部带注解）/ 不完整注解
    type_annotation_completeness: str = "不完整注解"
    # 函数原始 docstring 文本【MC6-2】：RQ1 的 RAG 基线以「限定名 + docstring」
    # 作为检索 query，prompt 也需要它；存量数据集带该字段，旧数据缺失时默认空串。
    docstring: str = ""

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
