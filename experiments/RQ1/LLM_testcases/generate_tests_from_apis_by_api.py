"""Generate example programs for specific APIs of libraries using a large-model API.

This script is based on `generate_tests_from_apis.py` but prompts for a specific
API name: "Generate an example program for {library} {api}.".

Behavior and flags mirror the original script: concurrency, model/api-base,
per-api count, verbose, etc. Output layout: <outdir>/<lib>/<lib>__<api>_001.py

"""
from __future__ import annotations

import asyncio
import os
import json
import argparse
import random
from typing import Dict, Any
import re

try:
    import httpx
except Exception:
    raise SystemExit("Please install httpx (pip install httpx) to run this script")

OPENAI_API_URL = "http://192.168.1.45:8021/v1/chat/completions"

MODEL_API_KEYS: Dict[str, str] = {
    "yb": os.environ.get("OPENAI_API_KEY", None),
    "gpt-4o-mini": os.environ.get("OPENAI_API_KEY_GPT4OMINI", None),
}

DEFAULT_MODEL = "yb"


async def call_llm(api_key: str, prompt: str, model: str = DEFAULT_MODEL, api_base: str = OPENAI_API_URL, max_tokens: int = 512, timeout: int = 60) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if not api_base.startswith("http"):
        api_base = "http://" + api_base

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(api_base, headers=headers, json=payload)
        text = ""
        try:
            text = resp.text
        except Exception:
            text = ""
        return {"status_code": resp.status_code, "text": text, "json": None}


def extract_code_blocks(text: str) -> str | None:
    if not text:
        return None
    py_blocks = re.findall(r"```(?:python)?\n(.*?)```", text, flags=re.S | re.I)
    if py_blocks:
        return "\n\n".join(b.strip() for b in py_blocks)
    any_blocks = re.findall(r"```\n?(.*?)```", text, flags=re.S)
    if any_blocks:
        return "\n\n".join(b.strip() for b in any_blocks)
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
    attempt = 0
    backoff_base = 1.5
    while attempt <= retries:
        attempt += 1
        try:
            # Acquire semaphore only for the network call so we don't hold a slot during backoff
            async with sema:
                result = await call_llm(api_key, prompt, model=model, api_base=api_base)

            status = result.get("status_code", 0)
            raw_text = result.get("text", "")
            if 200 <= status < 300:
                content = None
                try:
                    parsed = json.loads(raw_text)
                    choices = parsed.get("choices") or []
                    if choices:
                        msg = choices[0].get("message") or {}
                        content = msg.get("content")
                except Exception:
                    parsed = None
                code_text = None
                try:
                    text_to_search = content or raw_text or ""
                    code_text = extract_code_blocks(text_to_search)
                except Exception:
                    code_text = None
                return {"ok": True, "status": status, "content": content, "response_text": raw_text, "code": code_text, "error": None}
            else:
                if status in (429, 500, 502, 503, 504):
                    wait = backoff_base ** attempt + random.random()
                    await asyncio.sleep(wait)
                    continue
                else:
                    return {"ok": False, "status": status, "response_text": raw_text, "code": None, "error": None}
        except Exception as e:
            if attempt > retries:
                return {"ok": False, "status": 0, "response_text": "", "code": None, "error": str(e)}
            wait = backoff_base ** attempt + random.random()
            await asyncio.sleep(wait)
            continue


async def generate_all_for_apis(input_json: str, outdir: str, api_key: str, parallel: int = 100, model: str = DEFAULT_MODEL, api_base: str = OPENAI_API_URL, count_per_api: int = 1, verbose: bool = False) -> None:
    with open(input_json, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    libs = {}
    for lib, val in data.items():
        if isinstance(val, dict):
            apis = val.get("apis") or []
        elif isinstance(val, list):
            apis = val
        else:
            apis = []
        libs[lib] = apis

    os.makedirs(outdir, exist_ok=True)
    if verbose:
        print(f"Loaded {len(libs)} libraries from {input_json}")
        print(f"Output directory: {outdir}")

    sema = asyncio.Semaphore(parallel)
    tasks = []
    for lib, apis in libs.items():
        lib_safe = lib.replace("/", "_").replace("\\", "_")
        lib_dir = os.path.join(outdir, lib_safe)
        os.makedirs(lib_dir, exist_ok=True)
        for api in apis:
            api_safe = str(api).replace("/", "_").replace("\\", "_")
            for i in range(1, count_per_api + 1):
                prompt = f"Generate an example program demonstrating {api} from {lib}\n# Example number: {i}"
                tasks.append((lib, lib_dir, api_safe, i, call_with_retries(sema, api_key, prompt, model, api_base)))

    total = len(tasks)
    if verbose:
        print(f"Scheduling {total} requests ({count_per_api} per api) with parallel={parallel}")
    if total == 0:
        print("No requests to run.")
        return

    coros = [t[4] for t in tasks]
    metas = [(t[0], t[1], t[2], t[3]) for t in tasks]

    async def _wrap(lib, lib_dir, api_safe, idx, coro):
        try:
            res = await coro
            return (lib, lib_dir, api_safe, idx, res, None)
        except Exception as e:
            return (lib, lib_dir, api_safe, idx, None, e)

    running = [asyncio.create_task(_wrap(m[0], m[1], m[2], m[3], c)) for m, c in zip(metas, coros)]

    completed = 0
    progress_lock = asyncio.Lock()
    def _render(comp: int, tot: int) -> str:
        try:
            pct = int(comp * 100 / tot)
        except Exception:
            pct = 0
        bar_width = 30
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "-" * (bar_width - filled)
        return f"Progress: [{bar}] {comp}/{tot} ({pct}%) files written"

    async def _inc():
        nonlocal completed
        async with progress_lock:
            completed += 1
            s = _render(completed, total)
            print(s, end="\r", flush=True)
            if completed == total:
                print()

    for fut in asyncio.as_completed(running):
        lib, lib_dir, api_safe, idx, res, exc = await fut
        fname = f"{lib.replace('/', '_').replace('\\', '_')}__{api_safe}_{idx:03d}.py"
        py_path = os.path.join(lib_dir, fname)
        # Always write a .py file. If the task raised an exception or no code was
        # extracted, write a small placeholder Python file containing a comment
        # and the raw response (inside a triple-quoted string) so the output
        # directory contains only .py files.
        if exc is not None:
            try:
                with open(py_path, "w", encoding="utf-8") as ef:
                    ef.write(f"# task failed with exception: {exc}\n")
            except Exception:
                pass
            await _inc()
            continue

        if res is None:
            try:
                with open(py_path, "w", encoding="utf-8") as ef:
                    ef.write("# no result returned\n\n")
            except Exception:
                pass
            await _inc()
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
                        ef.write('\n""\"')
                except Exception:
                    pass
        else:
            # no code extracted — write a .py file with the raw response in a
            # triple-quoted string so the file is valid Python and contains the
            # debugging text.
            try:
                with open(py_path, "w", encoding="utf-8") as nf:
                    nf.write("# no code extracted from LLM response\n\n")
                    nf.write('"""\n')
                    nf.write(str(res.get("response_text") or res.get("error") or ""))
                    nf.write('\n"""\n')
            except Exception:
                pass
        await _inc()

    print(f"Completed {total} requests. Python files in: {outdir}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--outdir", "-o", required=True)
    parser.add_argument("--parallel", "-p", type=int, default=100)
    parser.add_argument("--count-per-api", "-c", type=int, default=1)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL)
    parser.add_argument("--api-base", default=OPENAI_API_URL)
    args = parser.parse_args()

    api_key = MODEL_API_KEYS.get(args.model) or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("No API key found. Set OPENAI_API_KEY or populate MODEL_API_KEYS for the chosen model.")

    asyncio.run(
        generate_all_for_apis(
            args.input,
            args.outdir,
            api_key,
            parallel=args.parallel,
            model=args.model,
            api_base=args.api_base,
            count_per_api=args.count_per_api,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
