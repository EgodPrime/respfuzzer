from unittest import mock

import pytest

from mplfuzz.agentic_api_resolver import API, ExecutionResultType, Reasoner


# 模拟 API 对象
class MockAPI(API):
    def __init__(self):
        super().__init__(api_name="example_api", source="mock", args=[], ret_type="str")


# 创建测试用 API 实例
mock_api = MockAPI()


# 测试正常生成解释的情况
@mock.patch("mplfuzz.agentic_api_resolver.client.chat.completions.create")
def test_explain_success(mock_create):
    # 模拟返回值
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content="<explain>代码缺少必要的参数</explain>"))]
    mock_create.return_value = mock_response

    code = "print('hello')"
    result = {"result_type": ExecutionResultType.ABNORMAL, "stderr": "Missing argument"}
    explanation = Reasoner().explain(code, result)

    assert explanation == "代码缺少必要的参数"


# 测试缺少 <explain> 前缀的异常情况
@mock.patch("mplfuzz.agentic_api_resolver.client.chat.completions.create")
def test_explain_missing_prefix(mock_create):
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content="代码缺少必要的参数</explain>"))]
    mock_create.return_value = mock_response

    code = "print('hello')"
    result = {"result_type": ExecutionResultType.ABNORMAL, "stderr": "Missing argument"}

    with pytest.raises(Exception, match="Prefix missing"):
        Reasoner().explain(code, result)


# 测试缺少 </explain> 后缀的异常情况
@mock.patch("mplfuzz.agentic_api_resolver.client.chat.completions.create")
def test_explain_missing_suffix(mock_create):
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content="<explain>代码缺少必要的参数"))]
    mock_create.return_value = mock_response

    code = "print('hello')"
    result = {"result_type": ExecutionResultType.ABNORMAL, "stderr": "Missing argument"}

    with pytest.raises(Exception, match="Suffix missing"):
        Reasoner().explain(code, result)


# 测试外部 API 抛出异常的情况
@mock.patch("mplfuzz.agentic_api_resolver.client.chat.completions.create")
def test_explain_api_error(mock_create):
    mock_create.side_effect = Exception("API error")

    code = "print('hello')"
    result = {"result_type": ExecutionResultType.ABNORMAL, "stderr": "Missing argument"}

    with pytest.raises(Exception, match="解释执行结果时发生错误：API error"):
        Reasoner().explain(code, result)
