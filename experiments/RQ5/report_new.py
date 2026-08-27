"""RQ5 Comparison Report: generate two tables for reviewer feedback.

Table 1: Total unique CWEs per tool (CodeQL vs Bandit)
Table 2/3: Unique vulnerability detection — tools that found it exclusively
          (CWEs found by some but not all tools are listed;
           common findings are excluded)
"""

import json
import pathlib
import re
import sys

import fire


# ── data paths ──────────────────────────────────────────────────────────────

BASE = pathlib.Path(__file__).parent / "SecurityEval" / "Result"

BANDIT_PATHS = {
    "InCoder":     BASE / "testcases_incoder.json",
    "Copilot":    BASE / "testcases_copilot.json",
    "RespFuzzer": BASE / "testcases_respfuzzer.json",
}

CODEQL_PATHS = {
    "InCoder":     BASE / "testcases_incoder",
    "Copilot":    BASE / "testcases_copilot",
    "RespFuzzer": BASE / "testcases_respfuzzer",
}

TOOLS = ["InCoder", "Copilot", "RespFuzzer"]


# ── readers ────────────────────────────────────────────────────────────────

def read_bandit(path: pathlib.Path) -> set[str]:
    """Parse bandit JSON, return CWE IDs as zero-padded 3-digit strings."""
    with open(path) as f:
        data = json.load(f)
    cwes = set()
    for item in data.get("results", []):
        cwe = item.get("issue_cwe", {}).get("id")
        if cwe is not None:
            cwes.add(f"{int(cwe):03d}")
    return cwes


def read_codeql(path: pathlib.Path) -> set[str]:
    """Walk CSV files under path, extract CWE IDs via regex."""
    cwes = set()
    re_cwe = re.compile(r"/CWE-(\d+)/")
    for csv_file in path.rglob("*.csv"):
        text = csv_file.read_text()
        for m in re_cwe.finditer(text):
            cwes.add(f"{int(m.group(1)):03d}")
    return cwes


# ── table builders ──────────────────────────────────────────────────────────

def fmt(found: bool) -> str:
    """✓ if present, ✗ otherwise."""
    return "✓" if found else "✗"


def cwe_str(s: str) -> str:
    """Normalise CWE string to CWE-NNN form."""
    return f"CWE-{int(s):03d}"


def build_table1(
    bandit_sets: dict[str, set[str]],
    codeql_sets: dict[str, set[str]],
) -> str:
    lines = [
        "| Code Generation Approach | CodeQL (Total) | Bandit (Total) |",
        "|--------------------------|----------------|---------------|",
    ]
    for tool in TOOLS:
        b = len(bandit_sets.get(tool, set()))
        c = len(codeql_sets.get(tool, set()))
        lines.append(f"| {tool:<26} | {c:>14} | {b:>12} |")
    return "\n".join(lines)


def build_unique_table(
    cwes: list[str],
    tool_sets: dict[str, set[str]],
    label: str,
) -> str:
    """Show CWEs where discovery is not identical across all three tools."""
    lines = [
        f"### {label}\n",
        "| CWE-ID | InCoder | Copilot | RespFuzzer |",
        "|--------|---------|---------|------------|",
    ]
    for cwe in sorted(cwes, key=lambda x: int(x)):
        row = [
            cwe_str(cwe),
            fmt(cwe in tool_sets["InCoder"]),
            fmt(cwe in tool_sets["Copilot"]),
            fmt(cwe in tool_sets["RespFuzzer"]),
        ]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# ── main ────────────────────────────────────────────────────────────────────

def _compute() -> tuple[
    dict[str, set[str]],  # bandit_sets
    dict[str, set[str]],  # codeql_sets
    set[str],             # all_bandit_cwes
    set[str],             # all_codeql_cwes
]:
    bandit_sets = {}
    for tool, p in BANDIT_PATHS.items():
        bandit_sets[tool] = read_bandit(p)

    codeql_sets = {}
    for tool, p in CODEQL_PATHS.items():
        codeql_sets[tool] = read_codeql(p)

    all_bandit_cwes = set()
    all_codeql_cwes = set()
    for s in bandit_sets.values():
        all_bandit_cwes |= s
    for s in codeql_sets.values():
        all_codeql_cwes |= s

    return bandit_sets, codeql_sets, all_bandit_cwes, all_codeql_cwes


def report_bandit():
    b, c, all_b, all_c = _compute()

    # Table 1: only Bandit column
    lines = [
        "# RQ5 Comparison Report\n",
        "## Table 1: Total Unique CWEs Detected\n",
        "| Code Generation Approach | Bandit (Total) |",
        "|--------------------------|---------------|",
    ]
    for tool in TOOLS:
        lines.append(f"| {tool:<26} | {len(b[tool]):>12} |")

    # Unique Bandit CWEs (found by some, not all)
    partial_bandit = [
        cw for cw in all_b
        if len([t for t in TOOLS if cw in b[t]]) not in (0, 3)
    ]

    lines.extend(["", build_unique_table(partial_bandit, b, "## Table 2: Unique Vulnerability Detection (Bandit)")])

    print("\n".join(lines))


def report_codeql():
    b, c, all_b, all_c = _compute()

    lines = [
        "# RQ5 Comparison Report\n",
        "## Table 1: Total Unique CWEs Detected\n",
        "| Code Generation Approach | CodeQL (Total) |",
        "|--------------------------|----------------|",
    ]
    for tool in TOOLS:
        lines.append(f"| {tool:<26} | {len(c[tool]):>14} |")

    # Unique CodeQL CWEs
    partial_codeql = [
        cw for cw in all_c
        if len([t for t in TOOLS if cw in c[t]]) not in (0, 3)
    ]

    lines.extend(["", build_unique_table(partial_codeql, c, "## Table 2: Unique Vulnerability Detection (CodeQL)")])

    print("\n".join(lines))


def report_both():
    b, c, all_b, all_c = _compute()

    lines = [
        "# RQ5 Comparison Report\n",
        "## Table 1: Total Unique CWEs Detected\n",
        "| Code Generation Approach | CodeQL (Total) | Bandit (Total) |",
        "|--------------------------|----------------|---------------|",
    ]
    for tool in TOOLS:
        lines.append(f"| {tool:<26} | {len(c[tool]):>14} | {len(b[tool]):>12} |")

    # Unique Bandit CWEs
    partial_bandit = sorted([
        cw for cw in all_b
        if len([t for t in TOOLS if cw in b[t]]) not in (0, 3)
    ], key=lambda x: int(x))

    # Unique CodeQL CWEs
    partial_codeql = sorted([
        cw for cw in all_c
        if len([t for t in TOOLS if cw in c[t]]) not in (0, 3)
    ], key=lambda x: int(x))

    lines.extend([
        "",
        build_unique_table(partial_bandit, b, "## Table 2: Unique Vulnerability Detection (Bandit)"),
        "",
        build_unique_table(partial_codeql, c, "## Table 3: Unique Vulnerability Detection (CodeQL)"),
    ])

    print("\n".join(lines))


if __name__ == "__main__":
    fire.Fire({"bandit": report_bandit, "codeql": report_codeql, "both": report_both})
