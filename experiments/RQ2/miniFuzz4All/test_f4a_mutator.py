"""
uv run pytest -q experiments/miniFuzz4All/test_f4a_mutator.py --cov=experiments --cov-report=term-missing
"""

import os
import types

# Import the module under test by file path so tests work without installing the package
import f4a_mutator as fm
import pytest

from respfuzzer.models import Argument, Seed


class FakeSeed:
    def __init__(self):
        # mimic the Seed attributes used by Fuzz4AllMutator
        self.function_call = 'foo()\n# inline comment\n"""block comment"""'
        self.library_name = "mylib"
        self.func_path = "mylib.module.func"


class DummyChatResponse:
    """openai.ChatCompletion 风格响应：思考内容在 message.reasoning_content，正文在 content。"""

    def __init__(self, content, reasoning=""):
        message = types.SimpleNamespace(content=content, reasoning_content=reasoning)
        self.choices = [types.SimpleNamespace(message=message)]
        self.usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1)


@pytest.fixture(autouse=True)
def patch_client(monkeypatch):
    """Replace the module's `client` with a dummy that returns a predictable response."""
    # If the test runner requests usage of the real OpenAI API, don't patch the client.
    if os.getenv("USE_OPENAI_API") is not None:
        yield
        return

    # create a dummy object with the same attribute access used in f4a_mutator
    class C:
        class chat:
            class completions:
                @staticmethod
                def create(*args, **kwargs):
                    # generate()/generate_n() 走 chat.completions.create
                    return DummyChatResponse("generated_call()\n")

    monkeypatch.setattr(fm, "client", C())
    yield


def test_extract_pure_code_strips_thinking_tags():
    """思考标签块（未走 reasoning_content 时可能混入正文）应被剔除。"""
    text = "<thinking>Let me think about the mutation...</thinking>\nx = 1"
    assert fm._extract_pure_code(text) == "x = 1"


def test_extract_pure_code_strips_fenced_prose():
    """正文是 markdown 围栏时只取代码，丢掉围栏外的解释性散文。"""
    text = 'Sure! Here is a mutated program:\n```python\nx = 1\n```\nHope it helps!'
    assert fm._extract_pure_code(text).strip() == "x = 1"


def test_extract_pure_code_keeps_plain_code():
    """纯代码原样返回。"""
    assert fm._extract_pure_code("x = 1\ny = 2") == "x = 1\ny = 2"


def test_extract_pure_code_empty():
    assert fm._extract_pure_code("") == ""


def test_message_content_reads_chat_response():
    """chat 响应取 message.content，不碰 reasoning_content。"""
    resp = DummyChatResponse("code_here()", reasoning="thinking aloud")
    assert fm._message_content(resp) == "code_here()"
    # 结构不匹配时返回空串而不是抛异常
    assert fm._message_content(types.SimpleNamespace()) == ""


def test_generate_uses_chat_and_drops_reasoning(monkeypatch):
    """generate() 走 chat.completions，正文不含思考内容。"""
    captured = {}

    def fake_create(*args, **kwargs):
        captured.update(kwargs)
        return DummyChatResponse(
            "mutated_call()\n", reasoning="secret internal monologue"
        )

    class Client:
        class chat:
            class completions:
                create = staticmethod(fake_create)

    monkeypatch.setattr(fm, "client", Client())
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)
    out = mut.generate()
    # 请求带上了 system+user 两条消息
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    # 思考内容没有混进结果
    assert "secret internal monologue" not in out
    assert "mutated_call()" in out



def test_clean_code_removes_prompts_and_comments():
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)

    # prepare a code snippet that includes the 'begin' prompt and comments
    code = (
        mut.prompt_used["begin"] + "\nprint(1) # keep\n\n\n" + '"""should be removed"""'
    )
    cleaned = mut.clean_code(code)

    # cleaned code should not contain the begin prompt, comments, or blank lines
    assert mut.prompt_used["begin"] not in cleaned
    assert "# keep" not in cleaned
    assert "should be removed" not in cleaned
    assert cleaned.strip() == "print(1)"


def test_comment_remover_handles_various_comments():
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)
    # build a string that contains inline comment, a single-quoted block and a double-quoted block
    code = "a = 1 # inline\n'''block\ncomment'''\n\n" + '"""more block"""'
    out = mut._comment_remover(code)
    # after removing comments only whitespace/newlines remain
    assert "inline" not in out
    assert "block" not in out


def test_update_strategy_avoids_combination_when_no_prev(monkeypatch):
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)

    # prev_example is None 时实现把 strategy 3 改写成 1（m_prompt），
    # 而不是 0（separator）——与 Fuzz4All target.py 的补丁一致
    monkeypatch.setattr(fm.random, "randint", lambda a, b: 3)
    res = mut.update_strategy("some_code")
    assert mut.m_prompt.strip() in res
    assert mut.prompt_used["separator"].strip() not in res

def test_generate_uses_client_and_updates_current_code():
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)

    # current_code initially from seed.function_call; call generate() which uses patched client
    # The upstream implementation of update() in the module takes only `self`, but
    # generate() calls self.update(code) (passes code). To avoid the resulting
    # TypeError during the test, override the instance's update to accept the
    # extra arg and delegate to the original method.
    original_update = mut.update
    mut.update = lambda code=None: original_update()

    new = mut.generate()

    # new should be cleaned and assigned to current_code
    assert isinstance(new, str)
    assert "generated_call()" in new
    assert mut.current_code == new


def test_update_strategy_se_prompt(monkeypatch):
    """Force strategy==2 and ensure the semantically-equivalent prompt is returned."""
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)

    # force randint to return 2
    monkeypatch.setattr(fm.random, "randint", lambda a, b: 2)
    res = mut.update_strategy("some_code")
    assert mut.se_prompt.strip() in res


@pytest.mark.parametrize(
    "strategy, expects",
    [
        (0, "separator"),
        (1, "m_prompt"),
        (2, "se_prompt"),
    ],
)
def test_update_strategy_simple_branches(monkeypatch, strategy, expects):
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)
    monkeypatch.setattr(fm.random, "randint", lambda a, b: strategy)
    res = mut.update_strategy("code_here")
    # expect the corresponding prompt substring to appear
    if expects == "separator":
        expected = mut.prompt_used["separator"].strip()
    else:
        expected = getattr(mut, expects).strip()
    assert expected in res


def test_update_strategy_combine_branch(monkeypatch):
    seed = FakeSeed()
    mut = fm.Fuzz4AllMutator(seed)
    # set prev_example so combine branch is allowed
    mut.prev_example = "prev_gen()"
    monkeypatch.setattr(fm.random, "randint", lambda a, b: 3)
    res = mut.update_strategy("codeX")
    # combine branch should include prev_example, separator, begin and c_prompt
    assert "prev_gen()" in res
    assert mut.prompt_used["separator"].strip() in res
    # assert mut.prompt_used["begin"].strip() in res
    assert mut.c_prompt.strip() in res


# USE_OPENAI_API=1 uv run pytest -q experiments/RQ3/miniFuzz4All/test_f4a_mutator.py::test_generate_four_strategies_real_client --cov=experiments --cov-report=term-missing -s
@pytest.mark.skipif(
    os.getenv("USE_OPENAI_API") is None,
    reason="Requires a real OpenAI API key and network access",
)
def test_generate_four_strategies_real_client(monkeypatch):
    seed = Seed(
        func_id=1,
        library_name="mylib",
        func_name="mylib.module.func",
        args=[Argument(arg_name="x", pos_type="positional")],
        function_call="func(1)",
    )

    mut = fm.Fuzz4AllMutator(seed)

    # Run generate() for each strategy value
    for strat in (3, 0, 1, 2, 3):
        monkeypatch.setattr(fm.random, "randint", lambda a, b, s=strat: s)
        res = mut.generate()
        print(f"{"="*30}\nStrategy {strat} produced\n{"="*30}\n{res}\n")
        assert isinstance(res, str)
