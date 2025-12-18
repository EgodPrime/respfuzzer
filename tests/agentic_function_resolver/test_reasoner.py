from unittest import mock

import pytest
from respfuzzer.lib.agentic_function_resolver import Reasoner


def test_explain_extracts_explain_tag():
    mock_response = mock.Mock()
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content="<explain>fix this</explain>"))
    ]

    with mock.patch(
        "respfuzzer.lib.agentic_function_resolver.client.chat.completions.create",
        return_value=mock_response,
    ):
        text = Reasoner().explain("code", {"stderr": "error"})

    assert text == "fix this"


def test_explain_returns_raw_if_no_tags():
    mock_response = mock.Mock()
    mock_response.choices = [
        mock.Mock(message=mock.Mock(content="some explanation without tags"))
    ]

    with mock.patch(
        "respfuzzer.lib.agentic_function_resolver.client.chat.completions.create",
        return_value=mock_response,
    ):
        text = Reasoner().explain("code", {"stderr": "err"})

    assert "some explanation" in text


def test_explain_raises_on_api_errors():
    with mock.patch(
        "respfuzzer.lib.agentic_function_resolver.client.chat.completions.create",
        side_effect=Exception("api fail"),
    ):
        with pytest.raises(Exception):
            Reasoner().explain("code", {"stderr": "err"})
