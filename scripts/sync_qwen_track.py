#!/usr/bin/env python3
"""由 campaign_progress_<model>.tsv 同步 rq1_<model>_track.md。

幂等：重复运行只刷新以下内容
  * §1 逐 run 表格：已完成 run 对应格子改 [x]
  * §2 进度统计块：完成数 / 失败数 / seeds / calls / 最新 run
勾选依据 = tsv 中 rc==0 的行（run 落表即产物已写出）。
"""
import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="模型 tag，如 Qwen3.6-35B-A3B")
    args = ap.parse_args()
    model = args.model

    tsv = ROOT / "experiments" / "function_solutions" / f"campaign_progress_{model}.tsv"
    track = ROOT / f"rq1_{model}_track.md"
    if not tsv.is_file():
        raise SystemExit(f"missing progress tsv: {tsv}")

    rows = []
    for line in tsv.read_text().splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 8 and f[1] and f[2] and f[3]:
            rows.append({"ts": f[0], "lib": f[1], "rs": int(f[2]), "mode": f[3],
                         "seeds": int(f[4]), "calls": int(f[5]),
                         "secs": int(f[6]), "rc": f[7]})
    ok = [r for r in rows if r["rc"] == "0"]

    text = track.read_text()

    # §1 勾选：逐模式段落内，按 |<lib>| 行 × rep N 列改格子
    done = {(r["mode"], r["lib"], r["rs"]) for r in ok}
    mode = None
    out = []
    for line in text.splitlines():
        m = re.match(r"^### (respfuzzer[a-z-]*)", line)
        if m:
            mode = m.group(1)
        elif mode and re.match(r"^\|(nltk|numpy|pandas|scipy|sklearn|spacy)\|", line):
            lib = line.split("|")[1]
            cells = [" [x] " if (mode, lib, i) in done else " [ ] " for i in range(1, 6)]
            line = f"|{lib}|" + "|".join(cells) + "|"
        out.append(line)

    # §2 统计块重写（定位到 "## 3." 前，整体替换 bullet 列表）
    n, fails = len(ok), len(rows) - len(ok)
    seeds = sum(r["seeds"] for r in ok)
    calls = sum(r["calls"] for r in ok)
    per_mode = {}
    for r in ok:
        per_mode[r["mode"]] = per_mode.get(r["mode"], 0) + 1
    pos = " / ".join(f"{m} {c}/30" for m, c in per_mode.items()) or "无"
    last = rows[-1] if rows else None
    latest = (f"{last['mode']} {last['lib']}×rs{last['rs']} rc={last['rc']}"
              f"（{last['seeds']} seeds / {last['calls']} calls / {last['secs']}s）"
              if last else "无")

    text2 = "\n".join(out)
    block = (
        f"- 完成 run：**{n} / 150**（{pos}）\n"
        f"- 失败 run（rc!=0）：{fails if fails else '无'}\n"
        f"- 累计 seeds：{seeds:,}" + (f"（均值 {seeds / n:.0f}/run）" if n else "") + "\n"
        f"- 累计 llm_calls：{calls:,}" + (f"（均值 {calls / n:.0f}/run）" if n else "") + "\n"
        f"- 最新落表 run：{latest}"
    )
    m2 = re.search(r"(## 2\. 进度统计[^\n]*\n\n)(- [^\n]*(?:\n- [^\n]*)*)", text2)
    if not m2:
        raise SystemExit("track §2 block not found")
    text2 = text2[: m2.start(2)] + block + text2[m2.end(2):]

    # §0 启动/完成时间：启动=首行时间戳，完成=末行（150 全完成时）
    if rows:
        t0 = dt.datetime.strptime(rows[0]["ts"], "%Y-%m-%d %H:%M:%S")
        text2 = re.sub(r"\| 启动时间 \| [^\n]*\|",
                       f"| 启动时间 | {t0:%F %T} |", text2)
        if n == 150:
            t1 = dt.datetime.strptime(rows[-1]["ts"], "%Y-%m-%d %H:%M:%S")
            text2 = re.sub(r"\| 完成时间 \| [^\n]*\|",
                           f"| 完成时间 | {t1:%F %T} |", text2)

    track.write_text(text2)
    print(f"[track-sync] {model}: {n}/150 done, fails={fails}, seeds={seeds}, calls={calls}")


if __name__ == "__main__":
    main()
