#!/usr/bin/env python3
"""
Script A (Easy): Generate sample programs for numpy APIs using LLM.
Reads from /home/yb/respfuzzer/output/resp/manifest.json and sends a simple prompt to LLM
asking it to generate a sample program for each function.

Usage: uv run python scripts/generate_llm_easy.py
"""

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from respfuzzer.utils.config import get_config

llm_cfg = get_config("llm")

import openai

client = openai.Client(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])


def generate_sample_for_function(func_name: str, library_name: str) -> str | None:
    """Ask LLM to generate a sample program for the given function."""
    prompt = f"""请给我生成一个 numpy 的 {func_name} API 的示例程序。

要求：
1. 只生成代码，用 <code></code> 包裹
2. 不要生成 ``` 包裹
3. 不要生成 code 以外的任何内容

请生成代码："""

    last_exc = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=llm_cfg["model_name"],
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个代码生成助手，擅长生成numpy API的示例程序。",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            code = response.choices[0].message.content.strip()
            if "<code>" in code and "</code>" in code:
                return code.split("<code>")[1].split("</code>")[0]
            if "```" in code:
                parts = code.split("```")
                if len(parts) >= 3:
                    return parts[1].strip()
                elif len(parts) >= 2:
                    return parts[1].strip()
            if code:
                return code
        except Exception as e:
            last_exc = e
            time.sleep(1 + attempt)
            continue

    print(f"      Error: {last_exc}")
    return None


def main():
    manifest_path = Path(__file__).parent / "manifest.json"

    if not manifest_path.exists():
        print(f"Error: {manifest_path} not found")
        sys.exit(1)

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    output_dir = Path(__file__).parent / "output" / "llm_easy"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(manifest)} functions from manifest...")

    results = []
    for i, item in enumerate(manifest):
        func_name = item["func_name"]
        library_name = item["library_name"]
        func_name_short = func_name.split(".")[-1]
        filename = f"{library_name}_{func_name_short}.py"
        filepath = output_dir / filename

        print(f"  [{i+1}/{len(manifest)}] {func_name}", end=" ... ")

        code = generate_sample_for_function(func_name, library_name)

        result = {
            "library_name": library_name,
            "func_name": func_name,
            "success": code is not None,
            "output_file": str(filepath),
        }

        if code:
            with open(filepath, "w") as f:
                f.write(code)
            print("OK")
            result["code"] = code
        else:
            print("FAILED")
            result["code"] = None

        results.append(result)

    # Summary
    success_count = sum(1 for r in results if r["success"])
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{len(results)} sample programs generated")
    print(f"Output directory: {output_dir}")

    # Write manifest
    manifest_out_path = output_dir / "manifest.json"
    with open(manifest_out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Manifest: {manifest_out_path}")


if __name__ == "__main__":
    main()
