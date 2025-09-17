from functools import wraps
from typing import Callable, Generic, Type, TypeVar, Union

T = TypeVar("T")  # 成功值的类型
E = TypeVar("E")  # 错误值的类型
U = TypeVar("U")
V = TypeVar("V")


class Result(Generic[T, E]):
    def __init__(self, is_ok: bool, value: Union[T, E]):
        self.is_ok = is_ok
        self._value = value

    @property
    def value(self) -> T:
        if not self.is_ok:
            raise ValueError(f"Cannot get value from Err : {self._value}")
        return self._value

    @property
    def error(self) -> E:
        if self.is_ok:
            raise ValueError("Cannot get error from Ok")
        return self._value

    @property
    def is_err(self) -> bool:
        return not self.is_ok

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self._value if self.is_ok else default

    def unwrap_or_else(self, generator: Callable[[E], T]) -> T:
        return self._value if self.is_ok else generator(E)

    def map(self, func: Callable[[T], U]) -> "Result[U, E]":
        if self.is_ok:
            return Ok(func(self._value))
        else:
            return self

    def map_err(self, func: Callable[[E], U]) -> "Result[T, U]":
        if self.is_err:
            return Err(func(self._value))
        else:
            return self

    def and_then(self: "Result[T,E]", func: Callable[[T], "Result[U, V]"]) -> "Result[U, V]":
        if self.is_ok:
            return func(self._value)
        else:
            return self

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


def resultify(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            res = func(*args, **kwargs)
            if isinstance(res, Result):
                return res
            else:
                return Ok(res)
        except Exception as e:
            return Err(e)

    return wrapper


__all__ = ["Result", "Ok", "Err", "resultify"]
