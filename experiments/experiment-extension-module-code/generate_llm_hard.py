#!/usr/bin/env python3
"""
Script B (Hard): Generate sample programs for library APIs using LLM WITH C/C++ source code.
Reads from /home/yb/respfuzzer/output/resp/manifest.json, uses the pre-stored C/C++ source
file paths, and sends both to LLM to generate a more informed sample program.

Usage: uv run python scripts/generate_llm_hard.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from respfuzzer.utils.config import get_config

llm_cfg = get_config("llm")

import openai

client = openai.Client(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])

# Libraries with C/C++ implementation
LIBRARIES = {
    "numpy": "https://github.com/numpy/numpy.git",
    "pandas": "https://github.com/pandas-dev/pandas.git",
    "scipy": "https://github.com/scipy/scipy.git",
    "torch": "https://github.com/pytorch/pytorch.git",
    "paddle": "https://github.com/PaddlePaddle/Paddle.git",
}


def get_source_dir(library_name: str) -> Path | None:
    """Get or clone library source code to local cache."""
    source_dir = Path.home() / ".cache" / f"{library_name}_source"

    if source_dir.exists():
        print(f"  Using cached {library_name} source at {source_dir}")
        return source_dir

    if library_name not in LIBRARIES:
        print(f"  No repository URL for {library_name}")
        return None

    print(f"  Cloning {library_name} source to {source_dir}...")
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", LIBRARIES[library_name], str(source_dir)],
            check=True,
            capture_output=True,
        )
        print(f"  Successfully cloned {library_name} source")
        return source_dir
    except subprocess.CalledProcessError as e:
        print(f"  Failed to clone {library_name}: {e}")
        return None


def read_c_source(source_file: str, source_dir: Path) -> str | None:
    """Read C/C++ source content from the pre-stored source file path."""
    if not source_file:
        return None

    # source_file is stored as relative path from source_dir
    # We need to find the actual file
    full_path = source_dir / source_file
    if full_path.exists():
        try:
            content = full_path.read_text(errors='ignore')
            return f"File: {source_file}\n\n{content[:10000]}"
        except Exception:
            pass
    return None


def generate_sample_with_source(func_name: str, library_name: str, c_source: str | None) -> str | None:
    """Ask LLM to generate a sample program using the C/C++ source code context."""
    if c_source:
        prompt = f"""请给我生成一个 {library_name} 的 {func_name} API 的示例程序。

这个API的C/C++实现源码如下：
<code>
{c_source}
</code>

请根据这个C/C++实现源码，生成一个更准确的{library_name} API示例程序。

要求：
1. 只生成代码，用 <code></code> 包裹
2. 不要生成 ``` 包裹
3. 不要生成 code 以外的任何内容

请生成代码："""
    else:
        prompt = f"""请给我生成一个 {library_name} 的 {func_name} API 的示例程序。

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
                        "content": f"你是一个代码生成助手，擅长生成{library_name} API的示例程序，并且能够根据C/C++源码理解API的实现细节。",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
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

    output_dir = Path(__file__).parent / "output" / "llm_hard"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(manifest)} functions from manifest...")

    # Get source directories for all libraries
    print("Preparing source code for all libraries...")
    source_dirs = {}
    for library_name in LIBRARIES.keys():
        source_dirs[library_name] = get_source_dir(library_name)

    results = []
    for i, item in enumerate(manifest):
        func_name = item["func_name"]
        library_name = item["library_name"]
        source_file = item.get("source_file")
        func_name_short = func_name.split(".")[-1]
        filename = f"{library_name}_{func_name_short}.py"
        filepath = output_dir / filename

        print(f"  [{i+1}/{len(manifest)}] {func_name}", end=" ... ")

        # Read C source from pre-stored path
        c_source = None
        if source_file and library_name in source_dirs:
            source_dir = source_dirs[library_name]
            if source_dir:
                c_source = read_c_source(source_file, source_dir)

        if c_source:
            print(f"[found C source] ", end="")
        else:
            print(f"[no C source] ", end="")

        # Generate sample with C source context
        code = generate_sample_with_source(func_name, library_name, c_source)

        result = {
            "library_name": library_name,
            "func_name": func_name,
            "success": code is not None,
            "has_c_source": c_source is not None,
            "source_file": source_file,
            "c_source": c_source if c_source else None,
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
    has_source_count = sum(1 for r in results if r.get("has_c_source"))
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{len(results)} sample programs generated")
    print(f"         {has_source_count} functions had C/C++ source code found")
    print(f"Output directory: {output_dir}")

    # Write manifest
    manifest_out_path = output_dir / "manifest.json"
    with open(manifest_out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Manifest: {manifest_out_path}")


if __name__ == "__main__":
    main()
