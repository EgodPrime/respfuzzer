# tests/test_mutator.py
from mplfuzz.mutate import chain_rng_get_current_state, chain_rng_init
from mplfuzz.mutator import (
    get_type,
    mutate_auto,
    mutate_bool,
    mutate_bytearray,
    mutate_bytes,
    mutate_complex,
    mutate_dict,
    mutate_float,
    mutate_frozenset,
    mutate_int,
    mutate_list,
    mutate_list_clip,
    mutate_list_expand,
    mutate_list_random_one,
    mutate_param_list,
    mutate_set,
    mutate_str,
    mutate_tuple,
)

# -----------------------------
# 基础类型测试
# -----------------------------


def test_mutate_bool():
    assert mutate_bool(True) == False
    assert mutate_bool(False) == True


def test_mutate_int():
    result = mutate_int(10)
    assert isinstance(result, int)
    assert result != 10  # Should be different after mutation


def test_mutate_float():
    result = mutate_float(10.5)
    assert isinstance(result, float)
    assert result != 10.5  # Should be different after mutation


def test_mutate_str():
    result = mutate_str("test")
    assert isinstance(result, str)
    assert result != "test"  # Should be different after mutation


def test_mutate_bytes():
    result = mutate_bytes(b"test")
    assert isinstance(result, bytes)
    assert result != b"test"  # Should be different after mutation


def test_mutate_complex():
    result = mutate_complex(complex(1, 2))
    assert isinstance(result, complex)
    assert result != complex(1, 2)  # Should be different after mutation


# -----------------------------
# 容器类型测试
# -----------------------------
def test_mutate_list_expand():
    # 测试空列表
    assert mutate_list_expand([]) == [], "空列表不应被扩展"

    # 测试非空列表
    original_list = [1, 2, 3]
    result = mutate_list_expand(original_list)
    assert isinstance(result, list)
    assert len(result) == len(original_list) + 1, "列表应扩展一个元素"
    assert all(x in original_list for x in result[:-1]), "原始元素应保留"
    assert result[-1] != original_list[0], "新增元素应与原元素不同"


def test_mutate_list_random_one():
    # 测试长度为1的列表
    chain_rng_init(2144)
    a1 = mutate_list_random_one([1])
    chain_rng_init(2144)
    a2 = mutate_auto(1)
    assert a1[0] == a2, "长度为1的列表应被完全替换"

    # 测试长度大于1的列表
    original_list = [1, 2, 3]
    result = mutate_list_random_one(original_list)
    assert isinstance(result, list)
    assert len(result) == len(original_list), "列表长度应保持不变"
    assert any(x in result for x in original_list), "只有一个元素应被变异"


def test_mutate_list_clip():
    # 测试长度为0或1的列表
    assert mutate_list_clip([]) == []
    assert mutate_list_clip([1]) == [1]

    # 测试长度大于1的列表
    original_list = [1, 2, 3, 4, 5]
    result = mutate_list_clip(original_list)
    assert isinstance(result, list)
    assert all(x in original_list for x in result)
    assert result != original_list  # 结果应该与原列表不同


def test_mutate_bytearray():
    # 测试空字节数组
    assert mutate_bytearray(bytearray()) == bytearray()

    # 测试非空字节数组
    original_bytearray = bytearray(b"test")
    result = mutate_bytearray(original_bytearray)
    assert isinstance(result, bytearray)
    assert result != original_bytearray  # 结果应该与原字节数组不同
    assert all(0 <= x <= 255 for x in result)  # 确保值在有效范围内


def test_mutate_list():
    result = mutate_list([1, 2, 3])
    assert isinstance(result, list)
    assert result != [1, 2, 3]  # Should be different after mutation


def test_mutate_tuple():
    result = mutate_tuple((1, 2, 3))
    assert isinstance(result, tuple)
    assert result != (1, 2, 3)  # Should be different after mutation


def test_mutate_set():
    result = mutate_set({1, 2, 3})
    assert isinstance(result, set)
    assert result != {1, 2, 3}  # Should be different after mutation


def test_mutate_frozenset():
    result = mutate_frozenset(frozenset({1, 2, 3}))
    assert isinstance(result, frozenset)
    assert result != frozenset({1, 2, 3})  # Should be different after mutation


def test_mutate_dict_empty():
    result = mutate_dict({})
    assert isinstance(result, dict)
    assert result == {}, "空字典经过 mutate_dict 应该仍然为空字典"


def test_mutate_dict():
    result = mutate_dict({"a": 1, "b": 2})
    assert isinstance(result, dict)
    assert result != {"a": 1, "b": 2}  # Should be different after mutation


# -----------------------------
# 对象实例测试
# -----------------------------


def test_mutate_instance():
    class TestClass:
        def __init__(self, x):
            self.x = x

    obj1 = TestClass(10)
    obj2 = TestClass(10)
    result1 = mutate_auto(obj1)
    result2 = mutate_auto(obj2)
    assert isinstance(result1, TestClass)
    assert isinstance(result2, TestClass)
    assert result1.x != obj1.x
    assert result2.x != obj2.x
    assert result1.x != result2.x


# -----------------------------
# 总体测试
# -----------------------------
def test_mutate_param_list():
    # 测试空列表
    assert mutate_param_list([]) == []

    # 测试长度为1的列表
    assert mutate_param_list([1]) == [1]

    # 测试长度大于1的列表
    original_list = [1, 2, 3, 4, 5]
    result = mutate_param_list(original_list)
    assert isinstance(result, list)
    assert result != original_list  # 结果应该与原列表不同
    assert all(isinstance(x, int) for x in result)  # 确保所有元素都是整数


# -----------------------------
# 获取类型测试
# -----------------------------


def test_get_type():
    assert get_type(10) == "int"
    assert get_type(10.5) == "float"
    assert get_type(complex(1, 2)) == "complex"
    assert get_type(True) == "bool"
    assert get_type("test") == "str"
    assert get_type(b"test") == "bytes"
    assert get_type([1, 2, 3]) == "list"
    assert get_type((1, 2, 3)) == "tuple"
    assert get_type({1, 2, 3}) == "set"
    assert get_type(frozenset({1, 2, 3})) == "frozenset"
    assert get_type({"a": 1, "b": 2}) == "dict"
    assert get_type(object()) == "instance"


# -----------------------------
# 自动类型检测测试
# -----------------------------


def test_mutate_auto():
    # Test with various types
    assert isinstance(mutate_auto(10), int)
    assert isinstance(mutate_auto(10.5), float)
    assert isinstance(mutate_auto(complex(1, 2)), complex)
    assert isinstance(mutate_auto(True), bool)
    assert isinstance(mutate_auto("test"), str)
    assert isinstance(mutate_auto(b"test"), bytes)
    assert isinstance(mutate_auto([1, 2, 3]), list)
    assert isinstance(mutate_auto((1, 2, 3)), tuple)
    assert isinstance(mutate_auto({1, 2, 3}), set)
    assert isinstance(mutate_auto(frozenset({1, 2, 3})), frozenset)
    assert isinstance(mutate_auto({"a": 1, "b": 2}), dict)
    assert isinstance(mutate_auto(object()), object)


# -----------------------------
# 测试确定性变异（相同种子下结果相同）
# -----------------------------


def test_deterministic_int():
    chain_rng_init(123456789)
    result1 = mutate_int(10)
    chain_rng_init(123456789)
    result2 = mutate_int(10)
    assert result1 == result2, "在相同种子下，int 变异结果不一致"


def test_deterministic_float():
    chain_rng_init(123456789)
    result1 = mutate_float(10.5)
    chain_rng_init(123456789)
    result2 = mutate_float(10.5)
    assert result1 == result2, "在相同种子下，float 变异结果不一致"


def test_deterministic_str():
    chain_rng_init(123456789)
    result1 = mutate_str("test")
    chain_rng_init(123456789)
    result2 = mutate_str("test")
    assert result1 == result2, "在相同种子下，str 变异结果不一致"


def test_deterministic_bytes():
    chain_rng_init(123456789)
    result1 = mutate_bytes(b"test")
    chain_rng_init(123456789)
    result2 = mutate_bytes(b"test")
    assert result1 == result2, "在相同种子下，bytes 变异结果不一致"


def test_deterministic_list():
    chain_rng_init(123456789)
    result1 = mutate_list([1, 2, 3])
    chain_rng_init(123456789)
    result2 = mutate_list([1, 2, 3])
    assert result1 == result2, "在相同种子下，list 变异结果不一致"


def test_deterministic_dict():
    chain_rng_init(123456789)
    result1 = mutate_dict({"a": 1, "b": 2})
    chain_rng_init(123456789)
    result2 = mutate_dict({"a": 1, "b": 2})
    assert result1 == result2, "在相同种子下，dict 变异结果不一致"


def test_deterministic_instance():
    class TestClass:
        def __init__(self, x):
            self.x = x

    obj = TestClass(10)
    chain_rng_init(123456789)
    result1 = mutate_auto(obj)
    chain_rng_init(123456789)
    result2 = mutate_auto(obj)
    assert result1.x == result2.x, "在相同种子下，instance 变异结果不一致"


def test_complex_situation():
    # 构造一个包含5种不同类型的复杂嵌套数据
    data = [
        123,  # int
        45.6,  # float
        "hello",  # str
        b"world",  # bytes
        {"a": [1, 2, 3]},  # dict with list
    ]

    # 第一次变异
    data1 = mutate_auto(data)
    # 第二次变异
    data2 = mutate_auto(data1)

    # 获取当前随机状态
    key = chain_rng_get_current_state()

    # 第三次变异，得到 result1
    result1 = mutate_auto(data2)

    # 重置随机状态为 key
    chain_rng_init(key)

    # 再次对 data2 变异一次，得到 result2
    result2 = mutate_auto(data2)

    # 验证结果是否一致
    assert result1 == result2, "在相同种子下，复杂数据的变异结果不一致"
