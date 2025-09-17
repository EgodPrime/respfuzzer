import ast
from unittest import mock

import pytest

from mplfuzz.agentic_api_resolver import API, ExecutionResultType, solve
from mplfuzz.models import Argument


# 模拟 API 对象
class MockAPI(API):
    def __init__(self):
        super().__init__(
            api_name="ast.literal_eval",
            source="mock",
            args=[Argument(arg_name="s", type="str", pos_type=1)],
            ret_type="object",
        )


# 创建测试用 API 实例
mock_api = MockAPI()


# 测试：代码第一次失败，Reasoner 提供解释后，第二次生成成功
@mock.patch("mplfuzz.agentic_api_resolver.Attempter.generate")
@mock.patch("mplfuzz.agentic_api_resolver.QueitExecutor.execute")
@mock.patch("mplfuzz.agentic_api_resolver.Reasoner.explain")
def test_solve_retry_with_reasoner(mock_explain, mock_execute, mock_generate):
    # 第一次生成错误代码
    mock_generate.side_effect = [
        "import ast; result = ast.literal_eval('invalid'); print(result)",  # 第一次生成错误
        'import ast; result = ast.literal_eval(\'{"key": "value"}\'); print(result)',  # 第二次生成正确
    ]

    # 第一次执行失败，第二次执行成功
    mock_execute.side_effect = [
        {
            "result_type": ExecutionResultType.ABNORMAL,
            "ret_code": 1,
            "stdout": "",
            "stderr": "ValueError: malformed node",
        },
        {"result_type": ExecutionResultType.OK, "ret_code": 0, "stdout": "{'key': 'value'}", "stderr": ""},
    ]

    # Reasoner 返回解释，引导生成正确代码
    mock_explain.return_value = '代码缺少必要的参数，应使用合法的字符串格式，例如 \'{"key": "value"}\'。'

    result = solve(mock_api)

    assert result == 'import ast; result = ast.literal_eval(\'{"key": "value"}\'); print(result)'


# 测试：Reasoner 提供错误解释导致最终失败
@mock.patch("mplfuzz.agentic_api_resolver.Attempter.generate")
@mock.patch("mplfuzz.agentic_api_resolver.QueitExecutor.execute")
@mock.patch("mplfuzz.agentic_api_resolver.Reasoner.explain")
def test_solve_reasoner_failure(mock_explain, mock_execute, mock_generate):
    # 第一次生成错误代码
    mock_generate.return_value = "import ast; result = ast.literal_eval('invalid'); print(result)"

    # 执行失败
    mock_execute.return_value = {
        "result_type": ExecutionResultType.ABNORMAL,
        "ret_code": 1,
        "stdout": "",
        "stderr": "ValueError: malformed node",
    }

    # Reasoner 返回错误解释，无法引导正确代码
    mock_explain.return_value = "代码缺少必要的参数，应使用合法的字符串格式，例如 'invalid'。"

    result = solve(mock_api)

    assert result is None  # 预算耗尽后返回 None
