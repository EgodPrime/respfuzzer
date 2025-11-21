from unittest import mock

import pytest

from respfuzzer.lib.agentic_function_resolver import Attempter
from respfuzzer.models import Function


class MockFunction(Function):
    def __init__(self):
        super().__init__(func_name="example.func", source="mock", args=[])


def test_generate_extracts_code_tag_and_returns_inner_content():
    at = Attempter()

    mock_response = mock.Mock()
    # mimic new-style client response used in the file: choices[0].message.content
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content="<code>print('ok')</code>"))
    ]

    with mock.patch(
        "respfuzzer.lib.agentic_function_resolver.client.chat.completions.create",
        return_value=mock_response,
    ):
        code = at.generate(MockFunction(), [])

    assert "print('ok')" == code


def test_generate_accepts_triple_backticks_fallback():
    at = Attempter()
    mock_response = mock.Mock()
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content="```py\nprint('bk')\n```"))
    ]

    with mock.patch(
        "respfuzzer.lib.agentic_function_resolver.client.chat.completions.create",
        return_value=mock_response,
    ):
        code = at.generate(MockFunction(), [])

    assert "print('bk')" in code


def test_generate_raises_after_retries_on_errors():
    at = Attempter()

    with mock.patch(
        "respfuzzer.lib.agentic_function_resolver.client.chat.completions.create",
        side_effect=Exception("boom"),
    ):
        with pytest.raises(Exception):
            at.generate(MockFunction(), [])


from unittest import mock
