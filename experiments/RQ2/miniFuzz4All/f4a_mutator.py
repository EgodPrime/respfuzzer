import random
import re
from concurrent.futures import ThreadPoolExecutor

import openai

from respfuzzer.models import Seed
from respfuzzer.utils.config import get_config

cfg = get_config("fuzz4all")
llm_cfg = get_config("llm")

client = openai.OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])


# miniFuzz4All 走 openai.ChatCompletion（system + user 两条消息），
# 思考内容由服务端放进 message.reasoning_content，取 content 即已剔除；
# _extract_pure_code 再兜底剥离混入正文的思考标签与 markdown 围栏。
# token 统计就地累计，通过 record_result 上报。
_f4a_llm_calls: int = 0
_f4a_input_tokens: int = 0
_f4a_output_tokens: int = 0


def _update_f4a_stats(response) -> None:
    global _f4a_llm_calls, _f4a_input_tokens, _f4a_output_tokens
    _f4a_llm_calls += 1
    usage = getattr(response, "usage", None)
    if usage is not None:
        # Completion API：prompt_tokens + completion_tokens 字段存在
        _f4a_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        _f4a_output_tokens += getattr(usage, "completion_tokens", 0) or 0


def get_f4a_stats() -> dict:
    return {
        "llm_calls": _f4a_llm_calls,
        "input_tokens": _f4a_input_tokens,
        "output_tokens": _f4a_output_tokens,
        "total_tokens": _f4a_input_tokens + _f4a_output_tokens,
    }


def reset_f4a_stats() -> None:
    global _f4a_llm_calls, _f4a_input_tokens, _f4a_output_tokens
    _f4a_llm_calls = 0
    _f4a_input_tokens = 0
    _f4a_output_tokens = 0


# Chat 路径 system 提示：强制纯代码输出（思考内容走 reasoning_content 字段）。
CODE_SYSTEM_PROMPT = (
    "You are a code mutation engine. You can only complete code snippets.\n"
    "Output ONLY pure Python code: no explanation, no markdown fences, "
    "no thinking tags, no prose."
)

_THINKING_BLOCK_RE = re.compile(r"<\s*thinking\s*>.*?<\s*/\s*thinking\s*>", re.S | re.I)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\n(.*?)(?:```|$)", re.S)


def _message_content(response) -> str:
    """取 chat 响应正文；思考内容在 reasoning_content，天然不进入返回值。"""
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    content = getattr(message, "content", None)
    return content or ""


def _extract_pure_code(text: str) -> str:
    """自动剔除思考内容，返回纯代码正文。

    1) 删除 ``<thinking>...</thinking>`` 标签块（模型没走 reasoning_content
       时可能混进正文）；
    2) 有 markdown 代码围栏时只取围栏内代码（顺带丢掉围栏外的解释性散文）。
    """
    if not text:
        return ""
    # 剔除 thinking 块后清掉残留前导空白（thinking 块通常位于正文开头）
    text = _THINKING_BLOCK_RE.sub("", text).lstrip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1)
    return text


class Fuzz4AllMutator:
    """
    从Fuzz4All源码中抽离出来的单独使用的变异器类。
    """

    def __init__(self, seed: Seed):
        self.prev_example: str = None
        self.current_code: str = seed.function_call
        self.p_strategy: int = cfg.get("p_strategy", 2)  # Fuzz4All默认值为2
        self.library_name = seed.library_name
        self.package_path, self.func_name = seed.func_path.rsplit(".", 1)

        self.prompt_used = {
            # https://github.com/fuzz4all/fuzz4all/blob/main/config/targeted/qiskit_linear_function.yaml
            # commit: 0bf42fe
            # Lines: 52
            "begin": f"from {self.package_path} import {self.func_name}\n",
            # https://github.com/fuzz4all/fuzz4all/blob/main/config/targeted/qiskit_linear_function.yaml
            # commit: 0bf42fe
            # Lines: 50
            "separator": '"""Create function call in %s in Python that uses the %s API"""'
            % (self.library_name, seed.func_path),
        }

        # https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/target.py
        # commit: 0bf42fe
        # Lines: 80-89
        self.m_prompt = '"""Please create a mutated program that modifies the previous generation"""'
        self.se_prompt = '"""Please create a semantically equivalent program to the previous generation"""'
        self.c_prompt = (
            '"""Please combine the two previous programs into a single program"""'
        )

        self.initial_prompt: str = (
            self.prompt_used["separator"] + "\n" + self.prompt_used["begin"]
        )
        self.prompt: str = self.initial_prompt
        self.stop_sequences = [
            self.m_prompt,
            self.se_prompt,
            self.c_prompt,
            self.prompt_used["separator"],
            self.prompt_used["begin"],
            "<eom>",
        ]

    def clean(self, code: str) -> str:
        """
        https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/QISKIT/QISKIT.py

        commit: 0bf42fe

        Lines: 112-114
        """
        code = self._comment_remover(code)
        return code

    def clean_code(self, code: str) -> str:
        """
        https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/QISKIT/QISKIT.py

        commit: 0bf42fe

        Lines: 116-127
        """
        code = code.replace(self.prompt_used["begin"], "").strip()
        code = self._comment_remover(code)
        code = "\n".join(
            [
                line
                for line in code.split("\n")
                if line.strip() != "" and line.strip() != self.prompt_used["begin"]
            ]
        )
        return code

    def remove_unsed_imports(self, code: str) -> str:
        import autoflake

        return autoflake.fix_code(code, remove_all_unused_imports=True)

    def _comment_remover(self, code: str) -> str:
        """
        https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/QISKIT/QISKIT.py

        commit: 0bf42fe

        Lines: 129-136
        """
        # Remove inline comments
        code = re.sub(r"#.*", "", code)
        # Remove block comments
        code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)
        code = re.sub(r"'''.*?'''", "", code, flags=re.DOTALL)
        return code

    def update_strategy(self, code: str) -> str:
        """
        https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/target.py

        commit: 0bf42fe

        Lines: 297-312
        """
        while 1:
            strategy = random.randint(0, self.p_strategy)
            # We add this patch to avoid strategy 3 when there is no previous example
            if self.prev_example is None and strategy == 3:
                strategy = 1
            # generate new code using separator
            if strategy == 0:
                return f"\n{code}\n{self.prompt_used['separator']}\n"
            # mutate existing code
            elif strategy == 1:
                return f"\n{code}\n{self.m_prompt}\n"
            # semantically equivalent code generation
            elif strategy == 2:
                return f"\n{code}\n{self.se_prompt}\n"
            # combine previous two code generations
            else:
                return f"\n{self.prev_example}\n{self.prompt_used['separator']}\n{code}\n{self.c_prompt}\n"

    def update(self) -> None:
        """
        https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/target.py

        commit: 0bf42fe

        Lines: 297-312
        """
        self.prompt = (
            self.prompt_used["separator"]
            + self.update_strategy(self.current_code)
            + self.prompt_used["begin"]
            + "\n"
        )
        self.prev_example = self.current_code

    def generate(self) -> str:
        """
        https://github.com/fuzz4all/fuzz4all/blob/main/Fuzz4All/target/target.py

        commit: 0bf42fe

        Lines: 247-282
        """
        self.update()
        response = client.chat.completions.create(
            model=llm_cfg["model_name"],
            messages=[
                {"role": "system", "content": CODE_SYSTEM_PROMPT},
                {"role": "user", "content": self.prompt},
            ],
            max_tokens=1024,
            temperature=0.7,
            top_p=1,
            presence_penalty=1,
            n=1,
            stop=self.stop_sequences,
        )
        _update_f4a_stats(response)
        # logger.debug(f"LLM response: {response}")
        new_code = _extract_pure_code(_message_content(response)).strip()

        self.current_code = self.prompt_used["begin"] + "\n" + self.clean(new_code)
        self.current_code = self.remove_unsed_imports(self.current_code)
        return self.current_code

    def generate_n(self, cnt: int) -> list[str]:
        """
        生成多个变异体。
        """
        self.update()
        mutants = []
        messages = [
            {"role": "system", "content": CODE_SYSTEM_PROMPT},
            {"role": "user", "content": self.prompt},
        ]
        with ThreadPoolExecutor(max_workers=cnt) as executor:
            futures = [
                executor.submit(
                    client.chat.completions.create,
                    model=llm_cfg["model_name"],
                    messages=messages,
                    max_tokens=1024,
                    temperature=0.7,
                    top_p=1,
                    presence_penalty=1,
                    n=1,
                    stop=self.stop_sequences,
                )
                for _ in range(cnt)
            ]
            for future in futures:
                response = future.result()
                _update_f4a_stats(response)
                new_code = _extract_pure_code(_message_content(response)).strip()
                mutant_code = (
                    self.prompt_used["begin"] + "\n" + self.clean_code(new_code)
                )
                mutant_code = self.remove_unsed_imports(mutant_code)
                mutants.append(mutant_code)
        return mutants
