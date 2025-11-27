"""Generate example programs for libraries using a large-model API, in parallel.

This script reads a JSON file mapping library -> {"apis": [...], "count": N}
(or a simpler mapping library -> [apis]) and for each library sends the prompt

    Generate an example program for {library}

to a large language model API (OpenAI-compatible REST) and stores the responses
as JSON files.

Concurrency: default 100 parallel requests (configurable). Uses asyncio + httpx.

Usage (example):
  export OPENAI_API_KEY="sk-..."
  python3 generate_tests_from_apis.py \
      --input experiments/RQ2/api_list_tracefuzz-20251013-RQ1-111.db.json \
      --outdir experiments/RQ2/generated_tests \
      --parallel 100

Notes:
- Requires `httpx` (async) package. Install with `pip install httpx`.
- The script assumes an OpenAI-compatible REST endpoint at https://api.openai.com/v1/chat/completions.
  You can override `--api-base` if using a different provider.
- Keep your API key safe. The script reads it from OPENAI_API_KEY env var.

"""
from __future__ import annotations

import asyncio
import os
import json
import argparse
import math
import random
from typing import Dict, Any
import re

try:
    import httpx
except Exception as e:
    raise SystemExit("Please install httpx (pip install httpx) to run this script")

OPENAI_API_URL = "http://192.168.1.45:8021/v1/chat/completions"

# Model-specific API keys mapping. You can populate keys here keyed by model name
# e.g. {"gpt-4o-mini": "sk-...", "yb": "sk-..."}
# For safety it's recommended to use environment variables. The script will
# prefer the mapping value if present and non-empty, otherwise fallback to
# OPENAI_API_KEY env var.
MODEL_API_KEYS: Dict[str, str] = {
    # fill in or leave empty to use environment variables
    # Avoid defaulting to a bogus key; prefer None so main() can detect absence.
    "yb": os.environ.get("OPENAI_API_KEY", None),
    "gpt-4o-mini": os.environ.get("OPENAI_API_KEY_GPT4OMINI", None),
}

DEFAULT_MODEL = "yb"  # change as needed


async def call_llm(api_key: str, prompt: str, model: str = DEFAULT_MODEL, api_base: str = OPENAI_API_URL, max_tokens: int = 512, timeout: int = 60) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    # Ensure api_base has a scheme
    if not api_base.startswith("http"):
        api_base = "http://" + api_base

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(api_base, headers=headers, json=payload)
        text = None
        try:
            text = resp.text
        except Exception:
            text = ""
        return {"status_code": resp.status_code, "text": text, "json": None}


def extract_code_blocks(text: str) -> str | None:
    """Extract code from LLM response text.

    Prefer fenced ```python code blocks. If none found, fall back to any
    fenced code block, then to a heuristic that looks for Python-like lines.
    Returns the concatenated code as a string, or None if nothing found.
    """
    if not text:
        return None

    # 1) fenced blocks with explicit python
    py_blocks = re.findall(r"```(?:python)?\n(.*?)```", text, flags=re.S | re.I)
    if py_blocks:
        return "\n\n".join(b.strip() for b in py_blocks)

    # 2) any fenced code block
    any_blocks = re.findall(r"```\n?(.*?)```", text, flags=re.S)
    if any_blocks:
        return "\n\n".join(b.strip() for b in any_blocks)

    # 3) fallback: look for first Python-like region starting with import/def/class
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("import ", "from ", "def ", "class ")):
            start = i
            break
    if start is not None:
        return "\n".join(lines[start:]).strip()

    return None


async def call_with_retries(sema: asyncio.Semaphore, api_key: str, prompt: str, model: str, api_base: str, retries: int = 5) -> dict:
    """Call the LLM with retries and return parsed result and extracted code.

    Semaphore limits concurrent *active network calls* only. We acquire the
    semaphore for each single network attempt and release it before any backoff
    sleep so other tasks can make progress while this one waits to retry.

    Returns a dict: {"ok": bool, "status": int, "content": str|None, "response_text": str, "code": str|None, "error": str|None}
    This function no longer writes JSON files; caller is responsible for saving code.
    """
    attempt = 0
    backoff_base = 1.5
    while attempt <= retries:
        attempt += 1
        try:
            # Acquire semaphore only for the duration of the network call
            async with sema:
                result = await call_llm(api_key, prompt, model=model, api_base=api_base)

            status = result.get("status_code", 0)
            raw_text = result.get("text", "")
            if 200 <= status < 300:
                # Try parse JSON response to extract assistant content
                content = None
                try:
                    parsed = json.loads(raw_text)
                    choices = parsed.get("choices") or []
                    if choices:
                        msg = choices[0].get("message") or {}
                        content = msg.get("content")
                except Exception:
                    parsed = None

                # extract code from content if present
                code_text = None
                try:
                    text_to_search = content or raw_text or ""
                    code_text = extract_code_blocks(text_to_search)
                except Exception:
                    code_text = None

                return {"ok": True, "status": status, "content": content, "response_text": raw_text, "code": code_text, "error": None}
            else:
                # transient errors: 429, 500, 502, 503, 504
                if status in (429, 500, 502, 503, 504):
                    wait = backoff_base ** attempt + random.random()
                    # don't hold semaphore while sleeping; loop will retry
                    await asyncio.sleep(wait)
                    continue
                else:
                    return {"ok": False, "status": status, "response_text": raw_text, "code": None, "error": None}
        except Exception as e:
            # network or unexpected
            if attempt > retries:
                return {"ok": False, "status": 0, "response_text": "", "code": None, "error": str(e)}
            wait = backoff_base ** attempt + random.random()
            await asyncio.sleep(wait)
            continue


async def generate_all(input_json: str, outdir: str, api_key: str, parallel: int = 100, model: str = DEFAULT_MODEL, api_base: str = OPENAI_API_URL, per_lib_count: int = 100, multiplier: int = 1, verbose: bool = False) -> None:
    # load input
    with open(input_json, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Accept structures like:
    # - {lib: {"apis": [...], "count": N}}  OR
    # - {lib: [...]} (list of apis)
    # We'll compute a per-library count as: len(apis) * multiplier (if apis present),
    # otherwise fall back to the provided per_lib_count parameter.
    libs_info = {}
    for lib, val in data.items():
        if isinstance(val, dict):
            apis = val.get("apis") or []
        elif isinstance(val, list):
            apis = val
        else:
            apis = []
        libs_info[lib] = apis

    os.makedirs(outdir, exist_ok=True)

    if verbose:
        print(f"Loaded {len(libs_info)} libraries from {input_json}")
        print(f"Output directory: {outdir}")

    sema = asyncio.Semaphore(parallel)
    tasks = []
    # For each library, create a subdir and schedule requests. By default we will
    # generate `count = max(1, len(apis) * multiplier)` examples for libraries
    # that provide an API list; otherwise we fall back to per_lib_count.
    for lib, apis in libs_info.items():
        lib_safe = lib.replace("/", "_").replace("\\", "_")
        lib_dir = os.path.join(outdir, lib_safe)
        os.makedirs(lib_dir, exist_ok=True)

        if apis:
            count = max(1, len(apis) * multiplier)
        else:
            count = per_lib_count

        for i in range(1, count + 1):
            prompt = f"Generate an example program for {lib}\n# Example number: {i}"
            tasks.append((lib, lib_dir, i, call_with_retries(sema, api_key, prompt, model, api_base)))

    # Run with progress
    total = len(tasks)
    if verbose:
        print(f"Scheduling {total} requests with parallel={parallel}")
    if total == 0:
        print("No libraries found in input file.")
        return

    # Run tasks and write code files
    if total == 0:
        print("No requests to run.")
        return

    # Extract the coroutines and keep metadata by creating real asyncio Tasks so
    # we can process results as they finish and write files incrementally.
    coros = [t[3] for t in tasks]
    metas = [(t[0], t[1], t[2]) for t in tasks]
    # Wrap coroutines so each returns its meta along with the result. This is
    # robust regardless of how asyncio yields completed tasks.
    async def _wrap_and_run(lib, lib_dir, idx, coro):
        try:
            res = await coro
            return (lib, lib_dir, idx, res, None)
        except Exception as e:
            return (lib, lib_dir, idx, None, e)

    running = [asyncio.create_task(_wrap_and_run(m[0], m[1], m[2], c)) for m, c in zip(metas, coros)]

    completed = 0
    progress_lock = asyncio.Lock()

    def _render_progress(comp: int, tot: int) -> str:
        # simple unicode bar
        try:
            pct = int(comp * 100 / tot)
        except Exception:
            pct = 0
        bar_width = 30
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "-" * (bar_width - filled)
        return f"Progress: [{bar}] {comp}/{tot} ({pct}%) files written"

    async def _inc_and_print():
        nonlocal completed
        async with progress_lock:
            completed += 1
            s = _render_progress(completed, total)
            # print on the same line and flush
            print(s, end="\r", flush=True)
            # when complete, move to next line
            if completed == total:
                print()

    # Process results as they become available so we don't wait for all to finish
    for fut in asyncio.as_completed(running):
        lib, lib_dir, idx, res, exc = await fut
        safe = lib.replace("/", "_").replace("\\", "_")
        py_path = os.path.join(lib_dir, f"{safe}_{idx:03d}.py")

        # Always write a .py file. Replace previous behavior of writing .err.txt
        # or .no_code.txt with a .py file that contains a short comment and the
        # raw response inside a triple-quoted string so the directory contains
        # only Python files.
        if exc is not None:
            try:
                with open(py_path, "w", encoding="utf-8") as ef:
                    ef.write(f"# task failed with exception: {exc}\n")
            except Exception:
                pass
            await _inc_and_print()
            continue

        if res is None:
            try:
                with open(py_path, "w", encoding="utf-8") as ef:
                    ef.write("# no result returned\n\n")
            except Exception:
                pass
            await _inc_and_print()
            continue

        if res.get("ok") and res.get("code"):
            try:
                with open(py_path, "w", encoding="utf-8") as cf:
                    cf.write(res.get("code") or "")
            except Exception as e:
                try:
                    with open(py_path, "w", encoding="utf-8") as ef:
                        ef.write(f"# failed to write extracted code: {e}\n\n")
                        ef.write('"""\n')
                        ef.write(str(res.get("response_text") or res.get("error") or ""))
                        ef.write('\n"""')
                except Exception:
                    pass
        else:
            try:
                with open(py_path, "w", encoding="utf-8") as nf:
                    nf.write("# no code extracted from LLM response\n\n")
                    nf.write('"""\n')
                    nf.write(str(res.get("response_text") or res.get("error") or ""))
                    nf.write('\n"""\n')
            except Exception:
                pass

        await _inc_and_print()

    print(f"Completed {total} requests. Python files in: {outdir}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", "-i", required=True, help="Input JSON file (library -> apis/count or library->list)")
    parser.add_argument("--outdir", "-o", required=True, help="Output directory to write per-library JSON responses")
    parser.add_argument("--parallel", "-p", type=int, default=100, help="Max parallel requests (default 100)")
    parser.add_argument("--count-per-lib", "-c", type=int, default=100, help="Number of samples to generate per library (default 100)")
    parser.add_argument("--multiplier", "-n", type=int, default=1, help="Multiplier to apply to the per-library API count (per-lib count = len(apis) * multiplier)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose progress output")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Model name to use")
    parser.add_argument("--api-base", default=OPENAI_API_URL, help="API base URL for chat completions endpoint")
    args = parser.parse_args()

    # Determine API key: prefer model-specific key from MODEL_API_KEYS if present
    api_key = MODEL_API_KEYS.get(args.model) or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "No API key found. Set OPENAI_API_KEY or populate MODEL_API_KEYS for the chosen model."
        )

    asyncio.run(
        generate_all(
            args.input,
            args.outdir,
            api_key,
            parallel=args.parallel,
            model=args.model,
            api_base=args.api_base,
            per_lib_count=args.count_per_lib,
            multiplier=args.multiplier,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
