#!/usr/bin/env python3
"""
Measure runtime code coverage (LOC executed) when running seeds.

Follows the Process+Pipe worker pattern. One persistent child process
accumulates coverage via coverage.Coverage API. Parent sends seeds one-by-one
via Pipe: ("execute", seed) -> worker execs -> sends "done" back.
On ("exit", None): worker stops coverage, saves, sends path, exits.

Usage:
    python scripts/count_seeds_running_loc.py -f <seeds.json> -s PKG1,PKG2
"""

import argparse
import io
import json
import os
import signal
import subprocess
import sys
import time
from multiprocessing import Process, Pipe

import coverage
import psutil

EXECUTION_TIMEOUT = 10  # seconds per seed


def kill_worker(p: Process) -> None:
    """Kill a worker process and all its children using SIGKILL."""
    try:
        parent = psutil.Process(p.pid)
    except psutil.NoSuchProcess:
        return
    children = parent.children(recursive=True)
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass


def _spawn_worker(source_pkgs, cov_data_path):
    """Create a new worker process and return (parent_conn, process)."""
    parent_conn, child_conn = Pipe()
    p = Process(
        target=coverage_worker,
        args=(child_conn, source_pkgs, cov_data_path),
    )
    p.start()
    return parent_conn, p


def coverage_worker(conn, source_pkgs: list, cov_data_file: str) -> None:
    """
    Persistent child process. Mirrors fuzz_exp.py continue_safe_execute:
      - Redirects stdout/stderr to suppress library noise
      - Accumulates coverage across all seeds via Pipe
      - On "execute": execs seed.function_call
      - On "exit": stops coverage, saves, sends path, exits
    """
    # Detach into own session so kill_worker only kills this process tree,
    # not the parent. os.setsid() here runs in the child after fork/exec
    # in spawn mode (preexec_fn cannot be used with spawn).
    try:
        os.setsid()
    except OSError:
        pass

    # Suppress stdout/stderr (mirrors fuzz_exp.py)
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    # Start programmatic coverage with explicit data file path
    cov = coverage.Coverage(source_pkgs=source_pkgs, data_file=cov_data_file)
    cov.start()

    while True:
        try:
            command, seed = conn.recv()
        except EOFError:
            break

        match command:
            case "execute":
                try:
                    exec(seed["function_call"])
                except Exception:
                    pass
                conn.send("done")
            case "exit":
                cov.stop()
                cov.save()
                conn.send(cov_data_file)
                return


def parse_args():
    parser = argparse.ArgumentParser(
        description="Measure runtime code coverage (LOC executed) for seeds."
    )
    parser.add_argument(
        "-f", "--function_file", required=True, help="Path to the seeds JSON file."
    )
    parser.add_argument(
        "-s",
        "--source_pkgs",
        type=str,
        default="",
        help="Comma-separated library names to measure coverage for (e.g. yaml,numpy).",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    input_path = args.function_file
    if not os.path.isfile(input_path):
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        seeds = json.load(f)

    print(f"Loaded {len(seeds)} seeds from {input_path}")

    source_pkgs = [p.strip() for p in args.source_pkgs.split(",") if p.strip()]
    if not source_pkgs:
        print("WARNING: --source_pkgs not set, coverage will track all executed code.")

    input_dir = os.path.dirname(os.path.abspath(input_path))
    input_basename = os.path.splitext(os.path.basename(input_path))[0]
    cov_json_path = os.path.join(input_dir, f"{input_basename}_cov.json")
    cov_data_path = os.path.join(input_dir, f"{input_basename}_cov.coverage")

    # Spawn initial worker
    parent_conn, p = _spawn_worker(source_pkgs, cov_data_path)

    completed = 0

    for i, seed in enumerate(seeds):
        parent_conn.send(("execute", seed))
        start = time.monotonic()
        seed_ok = False
        while True:
            if parent_conn.poll(timeout=1):
                try:
                    parent_conn.recv()
                    seed_ok = True
                except EOFError:
                    print(f"\n  seed {i}: worker crashed (PID={p.pid}), skipping", file=sys.stderr)
                    kill_worker(p)
                    p.join()
                    parent_conn, p = _spawn_worker(source_pkgs, cov_data_path)
                break

            if not p.is_alive():
                # Worker died silently
                print(f"\n  seed {i}: worker died (PID={p.pid}), skipping", file=sys.stderr)
                kill_worker(p)
                p.join()
                parent_conn, p = _spawn_worker(source_pkgs, cov_data_path)
                break

            elapsed = time.monotonic() - start
            if elapsed >= EXECUTION_TIMEOUT:
                print(f"\n  seed {i}: timeout after {EXECUTION_TIMEOUT}s, skipping", file=sys.stderr)
                kill_worker(p)
                p.join()
                parent_conn, p = _spawn_worker(source_pkgs, cov_data_path)
                break

        # Update progress bar only if seed completed successfully
        if seed_ok:
            completed += 1
            bar_len = 40
            filled = int(bar_len * completed / len(seeds))
            bar = "=" * filled + "-" * (bar_len - filled)
            pct = int(100 * completed / len(seeds))
            sys.stderr.write(f"\r[{bar}] {pct}% ({completed}/{len(seeds)})  ")
            sys.stderr.flush()

    sys.stderr.write("\n")

    parent_conn.send(("exit", None))
    try:
        cov_path = parent_conn.recv()
    except EOFError:
        print("ERROR: worker did not return coverage path", file=sys.stderr)
        kill_worker(p)
        sys.exit(1)

    p.join(timeout=10)
    if p.is_alive():
        kill_worker(p)
        p.join()

    if not os.path.exists(cov_data_path):
        print(f"ERROR: coverage file not found: {cov_data_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Coverage file saved: {cov_data_path}")

    # Generate JSON report from the single .coverage file
    json_cmd = [
        sys.executable, "-m", "coverage", "json",
        "--data-file", cov_data_path,
        "-o", cov_json_path,
    ]
    result = subprocess.run(json_cmd, capture_output=True, text=True, cwd=input_dir)
    if result.returncode != 0 or not os.path.exists(cov_json_path):
        print(f"ERROR: coverage json failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Parse and report results
    data = json.load(open(cov_json_path))
    files = data.get("files", {})

    total_covered = 0
    total_statements = 0

    print(f"\n=== Coverage Results ===")
    print(f"Files measured: {len(files)}")

    for file_path, info in files.items():
        executed = info.get("executed_lines", [])
        statements = info.get("summary", {}).get("num_statements", 0)
        covered = len(executed)
        total_covered += covered
        total_statements += statements
        pct = info.get("summary", {}).get("percent_covered", 0)
        basename = os.path.basename(file_path)
        print(f"  {basename}: {covered}/{statements} LOC ({pct:.1f}%)")

    pct_total = (total_covered / total_statements * 100) if total_statements > 0 else 0
    print(f"\nTotal LOC covered: {total_covered} / {total_statements} ({pct_total:.1f}%) across {len(files)} files")
    print(f"Coverage JSON saved to: {cov_json_path}")


if __name__ == "__main__":
    main()
