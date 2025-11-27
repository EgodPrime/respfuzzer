#!/usr/bin/env python3
"""Orchestrate instrumentation-run over compiled seed files.

This script:
- finds all .py files under a base directory
- filters to files that compile (py_compile)
- for each compiled file, runs `seed_instrument_runner.py` in a subprocess with a timeout
- collects per-seed results and writes an aggregated JSON report
"""
from __future__ import annotations

import argparse
import json
import py_compile
from pathlib import Path
from typing import Dict, Any
import signal
from contextlib import redirect_stdout, redirect_stderr
import subprocess
import sys
import json as _json


RUNNER = Path(__file__).resolve().parent / 'seed_instrument_runner.py'


def is_compilable(path: Path) -> bool:
    try:
        py_compile.compile(str(path), doraise=True)
        return True
    except Exception:
        return False


def run_in_process(seed: Path, apis_json: Path, timeout: int, libs_to_instrument: list[str]) -> Dict[str, Any]:
    """Execute the seed in the current process with per-API instrumentation.

    Behavior:
    - Instruments each API by replacing the attribute with instrument.instrument_function_fcr(...)
    - Redirects stdout/stderr to a per-seed log file (seedname.log)
    - Uses signal.alarm to enforce a timeout (POSIX-only)
    - Restores original attributes after execution
    - Returns a dict with keys: seed, covered (list), errors (dict)
    """
    import importlib
    from instrument import instrument_function_fcr, covered_functions

    out: Dict[str, Any] = {'seed': str(seed), 'covered': [], 'errors': {}}

    # This function instruments and runs a single seed. Historically we
    # instrumented the API targets on a per-seed basis which adds a lot of
    # overhead. To keep execution in a single process and fast we prefer to
    # instrument targets once outside the seed loop and then execute many
    # seeds under that instrumentation. This helper remains for backward
    # compatibility but the orchestrator now instruments once in `main`.

    # Fallback behaviour: if instrumentation hasn't been applied, just run the
    # seed without further instrumentation (the caller should instrument once).
    out['errors'] = out.get('errors', {})

    # Ensure covered_functions is empty before run
    try:
        covered_functions.clear()
    except Exception:
        pass

    # Prepare log file and redirect stdout/stderr
    log_path = seed.with_suffix('.log')
    try:
        log_fh = open(log_path, 'w', encoding='utf-8')
    except Exception as e:
        out['errors']['log_open'] = str(e)
        return out

    def _alarm_handler(signum, frame):
        raise TimeoutError('seed_timeout')

    try:
        # Set alarm
        signal.signal(signal.SIGALRM, _alarm_handler)
        if timeout and timeout > 0:
            signal.alarm(timeout)

        with redirect_stdout(log_fh), redirect_stderr(log_fh):
            # Execute seed
            try:
                code = seed.read_text(encoding='utf-8')
                g: dict = {'__name__': '__main__', '__file__': str(seed)}
                exec(compile(code, str(seed), 'exec'), g)
            except TimeoutError:
                out['errors']['exec'] = 'timeout'
            except SystemExit:
                out['errors']['exec'] = 'SystemExit'
            except Exception as e:
                out['errors']['exec_exception'] = str(e)
    finally:
        # cancel alarm
        try:
            signal.alarm(0)
        except Exception:
            pass
        log_fh.close()

    # Collect covered functions from instrument module
    try:
        out['covered'] = sorted(list(covered_functions))
    except Exception:
        out['covered'] = []

    return out


def run_in_subprocess(seed: Path, apis_json: Path, timeout: int) -> Dict[str, Any]:
    """Run the seed in a separate Python subprocess using the bundled runner.

    The subprocess runner (`seed_instrument_runner.py`) prints a JSON mapping
    of called APIs to stdout. We capture that JSON, write combined stdout/stderr
    into the per-seed .log file, and return a result dict compatible with
    `run_in_process` output (keys: seed, covered, errors).
    """
    out: Dict[str, Any] = {'seed': str(seed), 'covered': [], 'errors': {}}

    # Build command
    runner_py = RUNNER
    cmd = [sys.executable, str(runner_py), '--seed', str(seed), '--apis-json', str(apis_json)]

    log_path = seed.with_suffix('.log')
    try:
        # Start subprocess in a new session so we can signal the whole group
        # (POSIX only). This ensures that C-level threads or child processes
        # spawned by the seed are also terminated.
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # First try polite termination of the whole process group
            try:
                pgid = p.pid
                import os, signal as _signal
                # send SIGTERM to the process group
                os.killpg(pgid, _signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
            # wait a short grace period
            try:
                stdout, stderr = p.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                # still alive -> force kill the process group
                try:
                    import os, signal as _signal
                    os.killpg(pgid, _signal.SIGKILL)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                stdout, stderr = p.communicate()
            # write logs
            try:
                with open(log_path, 'wb') as fh:
                    if stdout:
                        fh.write(stdout)
                    if stderr:
                        fh.write(b"\n---stderr---\n")
                        fh.write(stderr)
            except Exception:
                pass
            out['errors']['exec'] = 'timeout_subprocess'
            out['errors']['termination_signal'] = 'SIGTERM/SIGKILL'
            return out

        # Write logs (stdout + stderr) for debugging
        try:
            with open(log_path, 'wb') as fh:
                if stdout:
                    fh.write(stdout)
                if stderr:
                    fh.write(b"\n---stderr---\n")
                    fh.write(stderr)
        except Exception:
            pass

        # We intentionally do NOT rely on JSON output from the runner.
        # Instead, write the combined stdout/stderr to the per-seed .log and
        # extract printed fully-qualified API names (those printed by
        # `instrument_function_fcr`) from the log. This mirrors the
        # `python runner.py > a.log 2>&1` workflow and leaves the logs human
        # readable for inspection.
        try:
            # Read the log we just wrote and look for lines that resemble
            # fully-qualified function names (module.submodule.func).
            txt = ''
            try:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
            except Exception:
                # fallback to decoding captured stdout
                try:
                    txt = stdout.decode('utf-8') if isinstance(stdout, (bytes, bytearray)) else str(stdout)
                except Exception:
                    txt = ''

            covered_set = set()
            import re
            # match lines that are a dotted name (e.g. numpy.array)
            pattern = re.compile(r'^([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)$', re.MULTILINE)
            for m in pattern.finditer(txt):
                covered_set.add(m.group(1))

            out['covered'] = sorted(covered_set)

            # If subprocess exit code indicates termination by signal, record it
            try:
                if p.returncode is not None and p.returncode < 0:
                    import signal as _signal
                    sig = -p.returncode
                    try:
                        sig_name = _signal.Signals(sig).name
                    except Exception:
                        sig_name = str(sig)
                    out['errors']['terminated_by_signal'] = sig_name
            except Exception:
                pass

            # If there was no covered API and process failed, record exit code
            if p.returncode and not out['covered'] and not out['errors']:
                out['errors']['exit_code'] = p.returncode

        except Exception as e:
            out['errors']['log_parse'] = str(e)

    except Exception as e:
        out['errors']['subprocess_spawn'] = str(e)

    return out


def main() -> None:
    p = argparse.ArgumentParser()
    # Compute repository-root-relative defaults so the script works across machines.
    # File is at: <repo>/experiments/RQ1/coverage/check_instrument_calls_fixed.py
    # parents: 0=coverage,1=RQ1,2=experiments,3=<repo root>
    repo_root = Path(__file__).resolve().parents[3]
    default_base = str(repo_root / 'experiments' / 'RQ1' / 'generated_tests_bigrun')
    default_apis = str(repo_root / 'experiments' / 'RQ1' / 'api_list.json')
    default_out = str(repo_root / 'experiments' / 'RQ1' / 'instrument_check_report.json')

    p.add_argument('--base', '-b', default=default_base, help='Base directory containing .py seeds')
    p.add_argument('--apis-json', '-a', default=default_apis, help='API list JSON')
    p.add_argument('--out', '-o', default=default_out, help='Output report JSON')
    p.add_argument('--timeout', type=int, default=10, help='Per-seed execution timeout in seconds')
    p.add_argument('--workers', '-j', type=int, default=4, help='Parallel workers to run subprocesses')
    p.add_argument('--no-progress', action='store_true', help='Disable progress bar output')
    p.add_argument('--max-seeds', type=int, default=0, help='If >0, process at most this many seeds (for testing)')
    p.add_argument('--start-index', type=int, default=0, help='Start index into the compilable seed list (0-based)')
    p.add_argument('--end-index', type=int, default=0, help='End index (exclusive) into the compilable seed list (0-based). If 0, goes to end')
    p.add_argument('--skip-interactive', action='store_true', help='Skip seeds that appear interactive (contain Ctrl+C/wait for signal/input prompts)')
    p.add_argument('--libs', type=str, default='', help='Comma-separated list of top-level libs from the APIs JSON to instrument (default: all)')
    args = p.parse_args()

    # Lightweight guard: patch nltk.download to avoid repeated blocking downloads
    # Some generated seeds call nltk.download() which can block or repeatedly
    # print progress and slow the whole run. We patch it to attempt each named
    # resource at most once per process and to no-op on subsequent calls.
    try:
        import nltk
        _nltk_orig_download = getattr(nltk, 'download', None)
        _nltk_tried = set()

        def _patched_nltk_download(resource, *a, **kw):
            try:
                name = resource if isinstance(resource, str) else str(resource)
            except Exception:
                name = repr(resource)
            if name in _nltk_tried:
                # Already attempted once in this run — skip to avoid repeated work
                return True
            # mark attempted to avoid repeated future attempts
            _nltk_tried.add(name)
            # If resource already exists locally, skip actual download
            try:
                # nltk.data.find can raise LookupError if not present
                nltk.data.find(name)
                return True
            except Exception:
                # Try the original download once (if available). If it fails or
                # raises, swallow to avoid hanging the orchestrator.
                try:
                    if _nltk_orig_download:
                        return _nltk_orig_download(resource, *a, **kw)
                except Exception:
                    return True
                return True

        if hasattr(nltk, 'download'):
            nltk.download = _patched_nltk_download
    except Exception:
        # Ignore if nltk is not installed or patching fails
        pass

    base = Path(args.base)
    if not base.exists() or not base.is_dir():
        raise SystemExit(f'Base directory not found: {base}')

    apis_json = Path(args.apis_json)
    if not apis_json.exists():
        raise SystemExit(f'APIs JSON not found: {apis_json}')

    files = sorted(base.rglob('*.py'))

    # Optional: filter out seeds that look interactive to avoid long-running
    # or waiting-for-signal behavior (e.g. tests that print 'Ctrl+C' and wait).
    def looks_interactive(path: Path) -> bool:
        try:
            txt = path.read_text(encoding='utf-8', errors='ignore')[:8192]
        except Exception:
            return False
        s = txt.lower()
        keywords = [
            'ctrl+c',
            'press ctrl',
            'press ctrl+c',
            'wait for signal',
            'wait for ctrl',
            'disable_signal_handler',
            'signal.pause',
            'input(',
            'raw_input('
        ]
        for k in keywords:
            if k in s:
                return True
        return False

    if args.skip_interactive:
        old_count = len(files)
        files = [f for f in files if not looks_interactive(f)]
        print(f'Skipped {old_count - len(files)} interactive-looking seeds')

    compilable_all = [f for f in files if is_compilable(f)]

    # Apply slicing/resume options: start-index/end-index take precedence over --max-seeds
    start_idx = max(0, args.start_index or 0)
    end_idx = args.end_index if args.end_index and args.end_index > 0 else None
    if end_idx is not None:
        compilable = compilable_all[start_idx:end_idx]
    else:
        compilable = compilable_all[start_idx:]
    if args.max_seeds and args.max_seeds > 0:
        compilable = compilable[: args.max_seeds]

    results = {}
    total = len(compilable)
    completed = 0

    def _render_progress(comp: int, tot: int) -> str:
        try:
            pct = int(comp * 100 / tot)
        except Exception:
            pct = 0
        bar_width = 30
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "-" * (bar_width - filled)
        return f"Progress: [{bar}] {comp}/{tot} ({pct}%)"

    # Parse libs filter
    libs_to_instrument = []
    if args.libs:
        libs_to_instrument = [x.strip() for x in args.libs.split(',') if x.strip()]

    # Instrument all target APIs once, keep originals and restore at the end.
    import importlib
    from instrument import instrument_function_fcr, covered_functions

    originals: dict = {}
    instrumentation_errors: dict = {}

    # Load API list and filter to requested libraries
    try:
        with open(apis_json, 'r', encoding='utf-8') as fh:
            api_data = json.load(fh)
    except Exception as e:
        raise SystemExit(f'Failed to load APIs JSON: {e}')

    apis: list[str] = []
    for lib, val in api_data.items():
        if libs_to_instrument and lib not in libs_to_instrument:
            continue
        if isinstance(val, dict):
            apis_list = val.get('apis') or []
        elif isinstance(val, list):
            apis_list = val
        else:
            apis_list = []
        for a in apis_list:
            if isinstance(a, str) and '.' in a:
                apis.append(a)

    # Instrument each API once
    for api in apis:
        mods = api.split('.')
        try:
            m = importlib.import_module(mods[0])
            parent = m
            for name in mods[1:-1]:
                parent = getattr(parent, name, None)
                if parent is None:
                    break
            if parent is None:
                instrumentation_errors[api] = 'parent_not_found'
                continue
            cur = getattr(parent, mods[-1], None)
            if cur is None:
                instrumentation_errors[api] = 'attr_not_found'
                continue
            originals[api] = (parent, cur)
            new = instrument_function_fcr(cur)
            setattr(parent, mods[-1], new)
        except Exception as e:
            instrumentation_errors[api] = f'instrument_error: {e}'

    # Notify instrumentation summary
    print(f'Instrumented {len(originals)} APIs, {len(instrumentation_errors)} errors')

    # Run seeds sequentially in the same process, under the single instrumentation set.
    for seed in compilable:
        # Print the currently-running seed path (unbuffered callers will
        # capture this immediately). This helps locate which seed caused a
        # native crash when the orchestrator or process dies unexpectedly.
        try:
            print(f"RUNNING_SEED: {seed}", flush=True)
        except Exception:
            pass

        try:
            res = run_in_process(seed, apis_json, args.timeout, libs_to_instrument)
        except Exception as e:
            res = {'seed': str(seed), 'error': f'exception: {e}'}

        # If the in-process run timed out or hung, retry in a subprocess which
        # we can forcibly kill. This helps with seeds that block at C level or
        # otherwise cannot be reliably terminated from inside the interpreter.
        try:
            errs = res.get('errors') or {}
            if errs.get('exec') == 'timeout':
                print(f"Seed timed out in-process, retrying in subprocess: {seed}", flush=True)
                try:
                    print(f"RUNNING_SEED_SUBPROCESS: {seed}", flush=True)
                except Exception:
                    pass
                sub = run_in_subprocess(seed, apis_json, args.timeout)
                # Preserve the original timeout info for auditing
                sub.setdefault('errors', {})
                sub['errors']['retry_in_process_timeout'] = errs
                res = sub
        except Exception:
            # Don't let retry logic crash the whole orchestrator
            pass
        # attach instrumentation errors once to the first seed result for reference
        if instrumentation_errors and not results:
            res.setdefault('instrumentation_errors', instrumentation_errors)
        results[str(seed)] = res
        completed += 1
        if not args.no_progress:
            print(_render_progress(completed, total), end='\r', flush=True)

    # Restore originals after running all seeds
    for api, (parent, cur) in originals.items():
        try:
            setattr(parent, api.split('.')[-1], cur)
        except Exception:
            pass

    if not args.no_progress and total > 0:
        print(_render_progress(total, total))

    # Summarize: for each API count how many seeds called it (based on covered list)
    api_counts: Dict[str, int] = {}
    total_seeds = len(compilable)
    for seed, r in results.items():
        covered = r.get('covered') or []
        for api in covered:
            api_counts[api] = api_counts.get(api, 0) + 1

    report = {
        'base': str(base),
        'total_seeds_compilable': total_seeds,
        'per_seed_results': results,
        'api_called_counts': api_counts,
    }

    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(f'Wrote report to {args.out}')


if __name__ == '__main__':
    main()
