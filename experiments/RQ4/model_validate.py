import os
import pathlib

import openai

base_url = "http://192.168.1.44:8021"
model_name = "qwen3-30b-a3b"
api_key = ""

client = openai.OpenAI(api_key=api_key, base_url=base_url)


def generate_code(prompt: str) -> str:
    sys_prompt = "根据用户提供的代码片段，生成剩下的代码来帮助用户完成函数的实现。只输出剩下的代码。不要任何解释。不要包含```符号。"
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1024,
        top_p=1,
        n=1,
        stop=None,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    # ./SecurityEval/Testcases_Prompt/*/*.py
    propmpt_dir = pathlib.Path("./SecurityEval/Testcases_Prompt")

    # ./SecurityEval/Testcases_Qwen3
    output_dir = pathlib.Path("./SecurityEval/Testcases_Qwen3")
    output_dir.mkdir(exist_ok=True)

    for prompt_file in propmpt_dir.rglob("*.py"):
        with open(prompt_file, "r") as f:
            prompt = f.read()

        generated_code = generate_code(prompt)
        full_code = prompt + "\n" + generated_code

        relative_path = prompt_file.relative_to(propmpt_dir)
        output_file = output_dir / relative_path
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w") as f:
            f.write(full_code)

        print(f"Generated code for {prompt_file} -> {output_file}")
