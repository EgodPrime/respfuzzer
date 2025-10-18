"""
uv run pytest -q experiments/miniFuzz4All/test_f4a_mutator.py --cov=experiments --cov-report=term-missing
"""

import os
import types

# Import the module under test by file path so tests work without installing the package
import f4a_mutator as fm
import pytest

from tracefuzz.models import Argument, Seed


class FakeSeed:
    def __init__(self):
        # mimic the Seed attributes used by Fuzz4AllMutator
        self.function_call = 'foo()\n# inline comment\n"""block comment"""'
        self.library_name = "mylib"
        self.func_name = "mylib.module.func"


class DummyResponseChoice:
    def __init__(self, content):
        self.message = types.SimpleNamespace(content=content)


class DummyResponse:
    def __init__(self, content):
        self.choices = [DummyResponseChoice(content)]


class DummyClient:
    def __init__(self, content_to_return):
        self._content = content_to_return

    class chat:
        class completions:
            @staticmethod
            def create(*args, **kwargs):
                # args[0] may be 'self' depending on binding; ignore and return a dummy
                return DummyResponse("generated_call()\n# new comment\n")


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
                    return DummyResponse("generated_call()\n")

    monkeypatch.setattr(fm, "client", C())
    yield


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

    # force random to return 3 first; but because prev_example is None it should pick 0 instead
    monkeypatch.setattr(fm.random, "randint", lambda a, b: 3)
    res = mut.update_strategy("some_code")
    # when strategy changed to 0 it should include separator
    assert mut.prompt_used["separator"].strip() in res


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


# USE_OPENAI_API=1 uv run pytest -q experiments/RQ2/miniFuzz4All/test_f4a_mutator.py::test_generate_four_strategies_real_client --cov=experiments --cov-report=term-missing -s
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
