
## 1. 种子生成 Attempter（RQ1 五模式）

来源：`src/respfuzzer/lib/agentic_function_resolver.py`

### 1.1 system prompt（五模式共用，`AbstractAttempter.generate`）

````
你是一个代码生成助手，你的名字是attempter，擅长根据用户提供的信息信息生成函数调用。
````
调用参数：`temperature=0.7`、`max_tokens=8000`、`chat_template_kwargs={"enable_thinking": False}`、`reasoning={"enabled": False}`。

### 1.2 五个 Attempter 的 user prompt（骨架相同，差异在输入区）

骨架 = **注意 6 条 + 例子 + "现在任务开始"**；输入区差异：`-wodoc` 把 `function.model_dump_json()` 换成构造的函数签名（无 docstring）、`-wofb` 无 `<history>`、`-woboth` 两者皆无、`-rag` 用 `<related_ctx>` 替代 `<history>`。

#### AttempterRespFuzzer（Full，有 docstring + history）

```python
prompt = f"""任务:
请根据`function`和`history`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成``` 
3. 不要生成`code`以外的任何内容 
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<history>
...
</history>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
<history>
{history}
</history>
"""
```

#### AttempterWithoutDocs（无 docstring，函数签名由参数类型重建）

```python
prompt = f"""任务:
请根据`function`和`history`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<history>
...
</history>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{func_sig}
</function>
<history>
{history}
</history>
"""
```

#### AttempterWithoutFeedback（有 docstring，无 history）

```python
prompt = f"""任务:
请根据`function`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
"""
```

#### AttempterWithoutDocsAndFeedback（无 docstring 且无 history）

```python
prompt = f"""任务:
请根据`function`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{func_sig}
</function>
"""
```

#### AttempterRAG（检索上下文替代 history）

```python
prompt = f"""任务:
请根据`function`和`related_ctx`中的信息以及相关上下文来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<related_ctx>
...
</related_ctx>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
<related_ctx>
{related_ctx}
</related_ctx>
"""
```

### 1.3 Reasoner：执行结果解释（`Reasoner.explain`）

system prompt：

````
你是一个代码调试助手，擅长解释代码错误并提供修正建议。
````
user prompt：

```python
prompt = f"""<code>\n{code}\n</code>\n<result>\n{result["stderr"]}\n</result>\n`code`中的代码在执行后得到报错`result`，请对这一执行结果进行解释，以指导代码编写人员进行修正指导。输出结果应为一段话，用<explain></explain>包裹。如果缺少文件，则提示Attempter通过open创建相应的临时文件。如果是参数错误，则提示Attempter调整参数的创建和初始化过程。请确保解释内容具体且有针对性。"""
```

调用参数：`temperature=0.7`、`max_tokens=8000`，同 1.1。

---

## 2. RQ3 语义变异（RQ3 消融实验）

来源：`src/respfuzzer/lib/fuzz/llm_mutator.py`

### 2.1 变异指令四元组 `PROMPT_MUTATE`（按 `mutation_type` 选取其一）

```python
PROMPT_MUTATE = (
    '"""Please create a program that mutates the input parameters of the target function call"""',
    '"""Please create a semantically equivalent program to the previous generation"""',
    '"""Please create a program that calls other functions from the target library to form a chain of function calls"""',
    '"""Please create a simplified version of the previous generation"""',
)
```

### 2.2 主模板 `MUTATE_PROMPT`（`{format_instructions}` 由 Pydantic parser 注入，约束输出字段 `code`，且要求纯代码无解释）

```python
MUTATE_PROMPT = PromptTemplate(
    template="""You are a professional programmer. Your task is to generate a mutated version of the given code
snippet according to the mutation instruction.
    
Code snippet:
{func_call}

Mutation instruction:
{mutation_instruction}

Target function name:
{func_name}

The mutated code should call the target function and meet the mutation instruction. Do not include any explanations or comments or print statements. The mutated code should be short and concise. Do not wrap the code with fences like \`\`\`.

{format_instructions}
    """,
    input_variables=["func_name", "func_call", "mutation_instruction"],
    partial_variables={"format_instructions": PureCodeParser.get_format_instructions()}
)
```

---

## 3. Fuzz4All 基线 prompt（RQ2 对比臂，verbatim 抄自上游 fuzz4all commit 0bf42fe）

来源：`experiments/RQ2/miniFuzz4All/f4a_mutator.py`

### system prompt（chat 路径，强制纯代码输出）

```python
CODE_SYSTEM_PROMPT = (
    "You are a code mutation engine. You can only complete code snippets.\n"
    "Output ONLY pure Python code: no explanation, no markdown fences, "
    "no thinking tags, no prose."
)
```

### separator / begin 模板

```python
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
```

### 变异指令

```python
self.m_prompt = '"""Please create a mutated program that modifies the previous generation"""'
```

### 语义等价指令

```python
self.se_prompt = '"""Please create a semantically equivalent program to the previous generation"""'
```

### 合并指令

```python
self.c_prompt = (
            '"""Please combine the two previous programs into a single program"""'
        )
```

`stop_sequences` = 上述三条指令 + separator + begin + `<eom>`。

---

## 4. RQ4 漏洞挖掘 pipeline prompt（留档）

> RQ4 目录现在只保留复现脚本 `vul_*.py` 与 `vul_links.md`；下面三条 pipeline prompt
> 采自此前的 `summary_from_logs` / `simple_replay` / `filter` 脚本（已从仓库移除），
> 在此留档，作为挖掘→回放→过滤三步的 prompt 记录。

### 4.1 stderr 相似度判定（`llm_judge_similarity`）
来源（已移除的 `experiments/RQ4/simple_replay.py`）

```python
prompt = f""" ## Task
You are a security researcher. Given two stderr outputs from program executions, determine if they indicate the same underlying issue.
You should consider the context and content of the error messages, not just exact string matches.

## Examples

### Example 1: very similar

<stderr_orig>
Segmentation fault (core dumped)
</stderr_orig>
<stderr_new>
Segmentation fault (core dumped)
</stderr_new>
<is_similar>True</is_similar>

### Example 2: similar stack traces and very similar error messages

<stderr_orig>
Traceback (most recent call last):
  File "example.py", line 10, in <module>
    main()
  File "example.py", line 6, in main
    result = 1 / 0
ZeroDivisionError: division by zero
</stderr_orig>
<stderr_new>
Traceback (most recent call last):
  File "test.py", line 5, in <module>
    compute()
  File "test.py", line 2, in compute
    value = 10 / 0
ZeroDivisionError: division by zero
</stderr_new>
<is_similar>True</is_similar>

## Start
<stderr_orig>{stderr_orig}</stderr_orig>
<stderr_new>{stderr_new}</stderr_new>
"""
```

### 4.2 PoC 合成（`synthesize_poc`，失败重试时追加 Failed Attempt 历史）

```python
prompt_base = f""" ## Task
You are a security researcher. Given the following `function_call` that caused an `stderr` when executed with certain mutated parameters `mutated_params_snapshot`, generate a minimal Proof of Concept (POC) code snippet that reproduces the same error.

## Constraints
- The POC should be as minimal as possible while still reproducing the error.
- Use the provided `function_call` and `mutated_params_snapshot` to construct the POC.
- Output only the POC code snippet without any additional explanations or comments or useless print statements.
- Ouput should be warpped in <poc></poc> tags.
- The `mutated_params_snapshot` provides args and kwargs, but both of them are serialized as bytes by pickle. You need to deserialize them before use.
- You can deserialize the args and kwargs using `pickle.loads`.

## Example
<function_call>
from some_library import vulnerable_function
vulnerable_function(user_input="malicious_payload")
</function_call>
<mutated_params_snapshot>
args=('malicious_payload',), kwargs={{}}
</mutated_params_snapshot>
<stderr>
Segmentation fault (core dumped)
</stderr>
<poc>
from some_library import vulnerable_function
vulnerable_function(user_input="malicious_payload")
</poc>
"""
```

重试历史片段：

```python
prompt_history += f"""
## Failed Attempt
<poc>{poc_code_snippet}</poc>
<output>{pure_stderr}</output>
"""
```

### 4.3 漏洞真伪判定 + 风险分级（`filter.py:judge_is_vulnerable`）
来源（已移除的 `experiments/RQ4/filter.py`）

system prompt：

````
You are a helpful assistant that helps people find bugs in code.
````
user prompt（含判断标准、风险 0–3 分级与 5 个示例）：

```python
prompt = f"""## Task
你是一个资深的安全研究员，专门负责分析漏洞复现结果。
你需要根据`source`, `poc`和`stderr`判断该复现结果是否意味着一个崩溃或者潜在的安全漏洞。

## 判断标准
- 如果错误描述中包含明显的崩溃信息（如 segmentation fault, buffer overflow, null pointer dereference 等），则判断为真实漏洞。
- 如果不包含明显的崩溃信息，则结合目标函数源码和POC代码进行分析，判断是否可能存在实现上的漏洞。
- 如果有明确的边界检查或者错误处理机制或者报错信息表明是用户使用错误（例如"the ... should ..."），则判断为否。
- 如果无法确定，则倾向于判断为否。

## 预期输出形式
<is_bug>True/False</is_bug>
<risk_level>0/1/2/3</risk_level>  # 0表示无风险（明显的用户使用错误），1表示缺少边界检查或者Docstring未做出明确边界约定等低风险漏洞，2表示即使用户小心使用也可能触发的漏洞，3表示明确存在崩溃、溢出、远程代码执行等高风险漏洞。
<reasoning>你的推理过程</reasoning>

## Examples

### Example 1: 无文档提示，也无边界检查， 判断为低风险漏洞
<source>
def copy_buffer(input):
    ''' Copies input buffer'''
    buffer = [0] * 10
    for i in range(len(input)):
        buffer[i] = input[i]
    return buffer
</source>
<poc>
copy_buffer([1,2,3,4,5,6,7,8,9,10,11,12])
</poc>
<stderr>
IndexError: list assignment index out of range
</stderr>
<is_bug>True</is_bug>
<risk_level>1</risk_level>
<reasoning>
虽然错误信息是 IndexError，但结合源码可以看出函数并没有进行边界检查，并且Docstring也没有明确说明输入长度限制。因此这是一个低风险漏洞，攻击者可以利用这个漏洞导致程序异常终止。
</reasoning>

### Example 2: 有文档提示，且有边界检查， 判断为非漏洞
<source>
def copy_buffer(input):
    ''' Copies input buffer of max length 10'''
    if len(input) > 10:
        raise ValueError("Input buffer too large")
    buffer = [0] * 10
    for i in range(len(input)):
        buffer[i] = input[i]
    return buffer
</source>
<poc>
copy_buffer([1,2,3,4,5,6,7,8,9,10,11,12])
</poc>
<stderr>
ValueError: Input buffer too large
</stderr>
<is_bug>False</is_bug>
<risk_level>0</risk_level>
<reasoning>
函数的Docstring明确说明了输入长度的限制，并且源码中也有相应的边界检查机制。因此这个错误是由于用户输入不符合要求引起的，不构成漏洞。
</reasoning>

### Example 3: 报错时有明确的用户使用错误提示， 判断为非漏洞
<source>
def divide(a, b):
    ''' Divides a by b'''
    if b == 0:
        raise ValueError("Denominator cannot be zero")
    return a / b
</source>
<poc>
divide(10, 0)
</poc>
<stderr>
ValueError: Denominator cannot be zero
</stderr>
<is_bug>False</is_bug>
<risk_level>0</risk_level>
<reasoning>
错误信息明确指出了用户的使用错误（分母不能为零），因此这不构成漏洞。
</reasoning>

### Example 4: 明显的崩溃， 判断为高风险漏洞
<source>
from _core import getx
def access_memory(index):
    ''' Accesses memory at given index'''
    buffer = [0] * 10
    return getx(buffer, index)
</source>
<poc>
access_memory(1000)
</poc>
<stderr>
Segmentation fault (core dumped)
</stderr>
<is_bug>True</is_bug>
<risk_level>3</risk_level>
<reasoning>
错误信息显示发生了段错误（Segmentation fault），这表明程序试图访问非法内存地址。结合源码可以看出函数没有进行边界检查，因此这是一个高风险漏洞。
</reasoning>

### Example 5: 文档中表明为安全使用，但实际实现中缺少边界检查， 用户使用时非常容易触发， 判断为中风险漏洞
<source>
def get_item(lst, index):
    ''' Gets item at index from list safely'''
    return lst[index]
</source>
<poc>
get_item([1,2,3], 10)
</poc>
<stderr>
IndexError: list index out of range
</stderr>
<is_bug>True</is_bug>
<risk_level>2</risk_level>
<reasoning>
虽然Docstring中表明该函数是安全使用的，但实际上函数并没有进行边界检查，用户在使用时非常容易触发 IndexError 异常。因此这是一个中风险漏洞。
</reasoning>

## Start
<source>
{func_source}
</source>
<poc>
{poc}
</poc>
<stderr>
{stderr}
</stderr>
"""
```

---

## 5. 附录：扩展模块实验 prompt（非正文实验）

来源：`experiments/experiment-extension-module-code/generate_test_cases_with_source.py`（C/C++ 源码是否入 prompt 的对照实验）

### 5.1 有 C 源码上下文的 prompt

```python
prompt = (
            f"<function>\n{function.model_dump_json()}\n</function>\n"
            f"<code>\n{code}\n</code>\n"
            "请判断上面的 `code` 是否包含对 `function` 的有效调用（例如完整包路径的函数调用或显式的通过别名能够唯一映射到目标函数的调用）。"
            ' 输出应为 JSON 对象，形如 {"valid": true, "reason": "..."}。'
            f'合理的import形式为"from {package_path} import {func_name}"'
            f'合理的调用形式为"{func_name}(...) "'
        )
```

### 5.2 无 C 源码上下文的 prompt（对照）

```python
prompt = f"""<code>\n{code}\n</code>\n<result>\n{result["stderr"]}\n</result>\n`code`中的代码在执行后得到报错`result`，请对这一执行结果进行解释，以指导代码编写人员进行修正指导。输出结果应为一段话，用<explain></explain>包裹。如果缺少文件，则提示Attempter通过open创建相应的临时文件。如果是参数错误，则提示Attempter调整参数的创建和初始化过程。请确保解释内容具体且有针对性。"""
```

