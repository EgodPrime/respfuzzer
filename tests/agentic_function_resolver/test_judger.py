from unittest import mock

import pytest

from tracefuzz.lib.agentic_function_resolver import Judger
from tracefuzz.models import Argument, Function


class MockFunction(Function):
    def __init__(self):
        super().__init__(
            func_name="example.module.func",
            source="mock",
            args=[Argument(arg_name="x", type="int", pos_type="POSITIONAL_OR_KEYWORD")],
        )


def test_judge_parses_json_response(monkeypatch):
    j = Judger()
    mock_response = mock.Mock()
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content='{"valid": true, "reason": "direct call"}'))
    ]

    monkeypatch.setattr(
        "tracefuzz.lib.agentic_function_resolver.client.chat.completions.create",
        lambda *a, **k: mock_response,
    )

    out = j.judge("import example.module as m; m.func(1)", MockFunction())
    assert out["valid"] is True
    assert "direct call" in out["reason"]


def test_judge_fallback_heuristic_true(monkeypatch):
    j = Judger()
    mock_response = mock.Mock()
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content="Yes, the code calls the target function"))
    ]

    monkeypatch.setattr(
        "tracefuzz.lib.agentic_function_resolver.client.chat.completions.create",
        lambda *a, **k: mock_response,
    )

    out = j.judge("something", MockFunction())
    assert out["valid"] is True


def test_judge_fallback_heuristic_false(monkeypatch):
    j = Judger()
    mock_response = mock.Mock()
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content="No, there is no invocation here"))
    ]

    monkeypatch.setattr(
        "tracefuzz.lib.agentic_function_resolver.client.chat.completions.create",
        lambda *a, **k: mock_response,
    )

    out = j.judge("something else", MockFunction())
    assert out["valid"] is False


def test_judge_raises_on_api_error(monkeypatch):
    j = Judger()

    def raise_err(*a, **k):
        raise Exception("api down")

    monkeypatch.setattr(
        "tracefuzz.lib.agentic_function_resolver.client.chat.completions.create",
        raise_err,
    )

    with pytest.raises(Exception):
        j.judge("code", MockFunction())


def test_judge_parses_json_embedded_in_text(monkeypatch):
    j = Judger()
    # model returns some text and an embedded json block
    content = 'Some commentary. {"valid": false, "reason": "not present"} End'
    mock_response = mock.Mock()
    mock_response.choices = [mock.Mock(message=mock.Mock(content=content))]

    monkeypatch.setattr(
        "tracefuzz.lib.agentic_function_resolver.client.chat.completions.create",
        lambda *a, **k: mock_response,
    )

    out = j.judge("code", MockFunction())
    assert out["valid"] is False
    assert "not present" in out["reason"]
