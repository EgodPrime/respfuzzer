from types import SimpleNamespace

import pytest

from tracefuzz.lib.fuzz import llm_mutator as lm


def test_make_fake_history_structure():
    seed = SimpleNamespace(func_name="foo", function_call="foo(1)")
    prompt = "please mutate"
    history = lm.make_fake_history(seed, prompt)

    assert isinstance(history, list)
    # first message is the system instruction
    assert history[0]["role"] == "system"
    assert "helpful programming assistant" in history[0]["content"]
    # the assistant content should contain the original function call
    assert history[2]["role"] == "assistant"
    assert history[2]["content"] == "foo(1)"
    # last message is the user prompt passed through
    assert history[-1]["role"] == "user"
    assert history[-1]["content"] == prompt


def test_llm_mutate_invalid_types():
    seed = SimpleNamespace(func_name="f", function_call="f()")
    # mutation_type below range
    with pytest.raises(ValueError):
        lm.llm_mutate(seed, -1)

    # mutation_type equal to len(PROMPT_MUTATE) is out of range
    with pytest.raises(ValueError):
        lm.llm_mutate(seed, len(lm.PROMPT_MUTATE))


def test_llm_mutate_calls_query_and_passes_messages(monkeypatch):
    seed = SimpleNamespace(func_name="f", function_call="f()")
    captured = {}

    def fake_query(messages):
        # capture the messages for assertions and return a dummy mutated code
        captured["messages"] = messages
        return "# mutated code"

    monkeypatch.setattr(lm, "query", fake_query)

    out = lm.llm_mutate(seed, 0)
    assert out == "# mutated code"

    # ensure the conversation history contains the expected pieces
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert "You are a helpful programming assistant" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert seed.func_name in msgs[1]["content"]
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == seed.function_call
    assert msgs[3]["role"] == "user"
    # the prompt used should be the one from PROMPT_MUTATE[0]
    assert msgs[3]["content"] == lm.PROMPT_MUTATE[0]


def test_random_llm_mutate_uses_random_and_delegates(monkeypatch):
    seed = SimpleNamespace(func_name="f", function_call="f()")

    # stub llm_mutate so we can observe the mutation_type passed in
    recorded = {}

    def fake_llm_mutate(s, mt):
        recorded["seed"] = s
        recorded["mutation_type"] = mt
        return f"code-{mt}"

    monkeypatch.setattr(lm, "llm_mutate", fake_llm_mutate)

    # force random.randint to return 2 deterministically
    monkeypatch.setattr("random.randint", lambda a, b: 2)

    result = lm.random_llm_mutate(seed)
    assert result == "code-2"
    assert recorded["seed"] is seed
    assert recorded["mutation_type"] == 2


def test_check_syntax_valid_and_invalid():
    valid_code = "def foo():\n    return 42"
    invalid_code = "def foo(:\n    return 42"

    assert lm.check_syntax(valid_code) is True
    assert lm.check_syntax(invalid_code) is False


def test_check_semantic_calls_in_code():
    # 以内置模块math为例
    seed = SimpleNamespace(func_name="math.sqrt", function_call="math.sqrt(4)")
    valid_code = "import math\nmath.sqrt(16)"
    invalid_code = "def bar():\n    return 42"  # does not call math.sqrt
    assert lm.check_semantic(seed, valid_code) is True
    assert lm.check_semantic(seed, invalid_code) is False


def test_batch_random_llm_mutate(monkeypatch):
    seed = SimpleNamespace(func_name="f", function_call="f()")
    n = 5
    max_workers = 2

    # stub random_llm_mutate to return predictable code
    def fake_random_llm_mutate(s):
        return f"mutated-code-for-{s.func_name}"

    monkeypatch.setattr(lm, "random_llm_mutate", fake_random_llm_mutate)

    results = lm.batch_random_llm_mutate(seed, n, max_workers)

    assert len(results) == n
    for code in results:
        assert code == f"mutated-code-for-{seed.func_name}"


def test_batch_random_llm_mutate_valid_only(monkeypatch):
    seed = SimpleNamespace(func_name="math.sqrt", function_call="math.sqrt(4)")
    n = 5
    max_workers = 2

    # Prepare a sequence of codes to be returned by the fake mutator
    codes = [
        "import math\nmath.sqrt(16)",  # valid
        "def foo():\n    return 42",  # invalid (no math.sqrt)
        "import math\nmath.sqrt(25)",  # valid
        "def bar(:\n    return 0",  # invalid syntax
        "import math\nmath.sqrt(36)",  # valid
        "import math\nmath.max(1,2)",  # invalid (no math.sqrt)
    ]
    code_iter = iter(codes)

    def fake_random_llm_mutate(s):
        return next(code_iter)

    monkeypatch.setattr(lm, "random_llm_mutate", fake_random_llm_mutate)
    results = lm.batch_random_llm_mutate_valid_only(seed, n, max_workers)

    assert len(results) == 3  # only 3 valid codes
