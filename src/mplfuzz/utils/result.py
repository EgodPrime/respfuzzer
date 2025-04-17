from typing import Generic, TypeVar, Union

T = TypeVar("T")  # 成功值的类型
E = TypeVar("E")  # 错误值的类型


class Result(Generic[T, E]):
    def __init__(self, is_ok: bool, value: Union[T, E]):
        self.is_ok = is_ok
        self._value = value

    @property
    def value(self) -> T:
        if not self.is_ok:
            raise ValueError("Cannot get value from Err")
        return self._value

    @property
    def error(self) -> E:
        if self.is_ok:
            raise ValueError("Cannot get error from Ok")
        return self._value

    def is_ok(self) -> bool:
        return self.is_ok

    def is_err(self) -> bool:
        return not self.is_ok

    @classmethod
    def Ok(cls, value: T = None) -> "Result[T, E]":
        return cls(True, value)

    @classmethod
    def Err(cls, error: E) -> "Result[T, E]":
        return cls(False, error)

    def __repr__(self):
        return f"Ok({self._value})" if self.is_ok else f"Err({self._value})"


Ok = Result.Ok
Err = Result.Err
__all__ = ["Result", "Ok", "Err"]
