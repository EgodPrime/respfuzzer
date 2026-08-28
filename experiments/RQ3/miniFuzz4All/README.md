# miniFuzz4All

Mini reproduction of the **Fuzz4All** baseline used in RQ3 of the RespFuzzer study.

## What this is

`miniFuzz4All` is **not** the official Fuzz4All system, nor a from-scratch reimplementation.
It is an **isolated, adapted copy** of Fuzz4All's core mutation logic:

- It is extracted from the official Fuzz4All repository (**commit `0bf42fe`**, https://github.com/fuzz4all/fuzz4all), specifically from `Fuzz4All/target/target.py` and `Fuzz4All/target/QISKIT/QISKIT.py`, and from the targeted config `config/targeted/qiskit_linear_function.yaml`.
- It is adapted to run Fuzz4All's mutation strategy against **RespFuzzer's seeds** for the 12 Python libraries in this study.

Every mutator method in `f4a_mutator.py` carries a header comment pointing to the exact upstream file, commit, and line range it was taken from. See the source for those pointers.

## Fidelity contract (inputs for the E-7 verification)

The behavior points that must stay aligned with the official Fuzz4All logic are listed below. The E-7 checklist verifies each one against the official source at commit `0bf42fe`.

| # | Behavior point | Upstream reference | Status in miniFuzz4All |
|---|----------------|--------------------|------------------------|
| C1 | Initial prompt = separator + begin fragments | `qiskit_linear_function.yaml` L50 / L52 | `prompt_used["separator"]` + `prompt_used["begin"]` |
| C2 | Mutation instruction prompts (mutate / semantic-equiv / combine / separator) | `target.py` L80-89 | `m_prompt`, `se_prompt`, `c_prompt`, separator |
| C3 | Strategy sampling (0..p_strategy, default p_strategy=2) | `target.py` L297-312 (`update_strategy`) | `update_strategy()` |
| C4 | Stop sequences passed to the LLM | Fuzz4All prompt assembly | `stop_sequences` list |
| C5 | Result cleaning (remove prompt, comments, blank lines) | `QISKIT.py` L116-127 (`clean_code`) / `_comment_remover` L129-136 | `clean_code()`, `_comment_remover()` |
| C6 | `self.update()` rebuilds prompt from separator + strategy + begin | `target.py` L297-312 | `update()` |
| C7 | LLM `generate()` returns cleaned code and updates current_code | `target.py` L247-282 | `generate()` |

## Documented adaptation points (deviations from official, all intentional)

| # | Adaptation | Why |
|---|-----------|-----|
| A1 | `prefix = "You are Qwen3-Coder. ..."` prepended to the prompt | Fuzz4All's default model/prompt framing is replaced to match the LLM backend used in this study (see `config.toml`, `[llm]`) |
| A2 | `update_strategy()`: when `prev_example` is `None`, strategy 3 (combine) is remapped to strategy 1 | avoid combining with a non-existent previous example on the first step (documented inline in the source) |
| A3 | LLM called via `client.completions.create(...)` with the configured `stop` sequences and decode settings | Fuzz4All's own driver is replaced by this standalone one; decode params are recorded for reproducibility |
| A4 | `p_strategy`, max_tokens, temperature, top_p, presence_penalty, n read from config | keep reproducible and consistent with the study's LLM settings |
| A5 | Seeds come from RespFuzzer `Seed` objects (library_name / func_name / function_call) | integration with RespFuzzer's RQ2 seed bench |

Lines in the official files may drift after commit `0bf42fe`; the line numbers above refer to that snapshot.
