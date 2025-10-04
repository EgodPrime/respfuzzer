from unittest import mock

import pytest

from tracefuzz.agentic_function_resolver import Function, Attempter


# 模拟 Function 对象
class MockFunction(Function):
    def __init__(self):
        super().__init__(func_name="example_function", source="mock", args=[], ret_type="str")


# 创建测试用 Function 实例
mock_function = MockFunction()


# 测试正常生成代码的情况
@mock.patch("tracefuzz.agentic_function_resolver.client.chat.completions.create")
def test_generate_success(mock_create):
    # 创建 mock_response 对象
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content="<code>print('hello')</code>"))]
    mock_create.return_value = mock_response

    history = []
    result = Attempter().generate(mock_function, history)

    assert result == "print('hello')"
    assert len(history) == 1
    assert history[0]["role"] == "attempter"
    assert "<code>print('hello')</code>" in history[0]["content"]


# 测试缺少 <code> 前缀的异常情况
@mock.patch("tracefuzz.agentic_function_resolver.client.chat.completions.create")
def test_generate_missing_prefix(mock_create):
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content="print('hello')"))]
    mock_create.return_value = mock_response

    with pytest.raises(Exception, match="Prefix missing"):
        Attempter().generate(mock_function, [])


# 测试缺少 </code> 后缀的异常情况
@mock.patch("tracefuzz.agentic_function_resolver.client.chat.completions.create")
def test_generate_missing_suffix(mock_create):
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content="<code>print('hello'"))]
    mock_create.return_value = mock_response

    with pytest.raises(Exception, match="Suffix missing"):
        Attempter().generate(mock_function, [])


# 测试外部 Function 抛出异常的情况
@mock.patch("tracefuzz.agentic_function_resolver.client.chat.completions.create")
def test_generate_function_error(mock_create):
    mock_create.side_effect = Exception("Function error")

    with pytest.raises(Exception, match="生成函数调用时发生错误：Function error"):
        Attempter().generate(mock_function, [])
