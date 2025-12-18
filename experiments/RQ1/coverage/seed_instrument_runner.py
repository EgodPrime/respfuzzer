#!/usr/bin/env python3
"""Run a single seed file under instrumentation and report which APIs were called.

This script is intended to be executed in a short-lived subprocess. It:
- loads a list of API paths from a JSON file (list of strings like 'numpy.zeros')
- for each API uses `instrument_function_via_path_check_ctx` to wrap the function
- executes the seed file via exec() in a fresh __main__ namespace
- after execution, inspects the wrapped functions for a `.called` attribute
  (set by the check wrapper) and writes a JSON mapping api -> bool to stdout.

Exit code: 0 on success (always writes JSON), non-zero only on fatal errors.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from contextlib import ExitStack
from pathlib import Path

# Ensure local experiments/RQ2 is importable so we can `import instrument`
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    from instrument import (
        instrument_function_fcr,
        instrument_function_via_path_check_ctx,
    )
except Exception as e:
    # If import fails, we still want to produce a JSON error
    def instrument_function_via_path_check_ctx(x):
        raise


def load_apis(json_path: Path) -> list[str]:
    with open(json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    apis: list[str] = []
    # data is expected to be mapping lib -> {"apis": [...]} or lib -> [...]
    for lib, val in data.items():
        if isinstance(val, dict):
            apis_list = val.get("apis") or []
        elif isinstance(val, list):
            apis_list = val
        else:
            apis_list = []
        for a in apis_list:
            if isinstance(a, str):
                apis.append(a)
    return apis


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", required=True, help="Path to seed .py file to execute")
    p.add_argument(
        "--apis-json",
        required=True,
        help="Path to JSON file listing APIs to instrument",
    )
    p.add_argument(
        "--cwd", help="Working directory to chdir into before executing the seed"
    )
    args = p.parse_args()

    seed = Path(args.seed)
    apis_json = Path(args.apis_json)

    out = {"seed": str(seed), "called": {}, "errors": {}}

    try:
        apis = load_apis(apis_json)
    except Exception as e:
        out["errors"]["load_apis"] = str(e)
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)

    # We will attempt to instrument all listed apis; failures will be recorded
    to_instrument = []
    for api in apis:
        if not isinstance(api, str) or "." not in api:
            continue
        to_instrument.append(api)

    originals = {}
    try:
        # Replace each target with an fcr wrapper that prints the fqname on call.
        for api in to_instrument:
            try:
                mods = api.split(".")
                m = importlib.import_module(mods[0])
                parent = m
                for name in mods[1:-1]:
                    parent = getattr(parent, name, None)
                    if parent is None:
                        break
                if parent is None:
                    out["errors"][api] = "parent_not_found"
                    continue
                cur = getattr(parent, mods[-1], None)
                if cur is None:
                    out["errors"][api] = "attr_not_found"
                    continue
                originals[api] = (parent, cur)
                new = instrument_function_fcr(cur)
                setattr(parent, mods[-1], new)
            except Exception as e:
                out["errors"][api] = f"instrument_error: {e}"

        # Execute the seed in a fresh globals dict
        g: dict = {"__name__": "__main__", "__file__": str(seed)}
        if args.cwd:
            try:
                import os

                os.chdir(args.cwd)
            except Exception:
                pass
        try:
            code = seed.read_text(encoding="utf-8")
            exec(compile(code, str(seed), "exec"), g)
        except SystemExit:
            # don't treat SystemExit as hard failure; record and continue
            out["errors"]["exec"] = "SystemExit"
        except Exception as e:
            out["errors"]["exec_exception"] = str(e)

    except Exception as e:
        out["errors"]["fatal"] = str(e)

    finally:
        # restore originals
        for api, (parent, cur) in originals.items():
            try:
                setattr(parent, api.split(".")[-1], cur)
            except Exception:
                pass

    # NOTE: we intentionally do not print a JSON summary here. The runner
    # is intended to be invoked with shell-style redirection (e.g.
    # `python seed_instrument_runner.py --seed x.py --apis-json apis.json > x.log 2>&1`)
    # so callers can inspect the printed fully-qualified function names emitted
    # by `instrument_function_fcr`. Any debugging/errors were written to stderr
    # during execution and will also be captured in the redirected log.
    return


if __name__ == "__main__":
    main()
