"""RQ1 F1：run driver 测试（runs 循环、run_id、落盘、合并）。

- ut-F1-1 run 循环 runs 次（产物目录数）。
- ut-F1-2 run_id 派生确定（r<run>_s<seed>）。
- ut-F1-3 结果路径 runs/RQ1/<mode>/<library>/r<run>_s<seed>/。
- ut-F1-4 合并器保留 run_id 列、无丢行。
- ut-F9-1 落盘行字段齐全（run_id/mode/library/func_name/seed_id/candidate_index/covered）。
- ut-F9-3 同参数重跑幂等（结果行一致、文件覆盖）。
- it-F1 manifest/decode_config/results schema 校验（fake LLM/Executor，零网络）。
"""

from __future__ import annotations

import json
import re

from experiments.RQ1.engine.driver import merge_results, run_experiment
from respfuzzer.models import Function


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []  # 记录每次收到的 user prompt（rag 注入断言用）

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        return self.responses.pop(0) if self.responses else "<code>x=1</code>"

class FakeExecutor:
    def __init__(self, covered_sequence):
        self.seq = list(covered_sequence)
        self.judged = []

    def covered(self, function, code):
        self.judged.append(code)
        return bool(self.seq.pop(0)) if self.seq else True


def _funcs() -> list[Function]:
    return [
        Function(
            func_name=f"lib.mod.f{i}",
            library_name="lib",
            id=i + 1,
            source=f"def f{i}(): pass",
            args=[],
        )
        for i in range(4)
    ]


def _run(
    tmp_path,
    *,
    runs=2,
    k=3,
    seed=1,
    mode="normal",
    limit=2,
    responses=None,
    covered=None,
):
    llm = FakeLLM(responses if responses is not None else ["<code>y=1</code>"] * 20)
    ex = FakeExecutor(covered if covered is not None else [True] * 20)
    out = run_experiment(
        mode,
        "lib",
        runs=runs,
        k=k,
        base_seed=seed,
        outdir=tmp_path,
        limit=limit,
        llm=llm,
        executor=ex,
        functions=_funcs(),
        now="2026-08-29T00:00:00",
        warmup=False,  # 测试注入零网络 fake，禁用 LLM 预热
    )
    return out, llm, ex


def test_f1_run_dirs_and_id(tmp_path):
    """ut-F1-1/2/3：runs 次 run 目录、run_id 派生确定、路径结构。"""
    out, _, _ = _run(tmp_path, runs=3, seed=9)
    run_dirs = sorted(p.name for p in out.glob("r*_s*"))
    assert len(run_dirs) == 3
    assert all("_s" in d and d.startswith("r") for d in run_dirs)
    man = json.loads((out / run_dirs[0] / "manifest.json").read_text())
    assert man["run_id"].startswith("normal_lib_r1_s")
    assert man["experiment"] == "RQ1"
    assert man["created_at"] == "2026-08-29T00:00:00"


def test_f1_result_schema_keys(tmp_path):
    """ut-F9-1：落盘行字段齐全。"""
    _run(tmp_path)
    rows = merge_results(tmp_path, "normal", "lib")
    assert rows, "至少一行候选"
    for key in (
        "run_id",
        "mode",
        "library",
        "func_name",
        "seed_id",
        "candidate_index",
        "covered",
    ):
        assert key in rows[0], f"缺键 {key}"
    assert rows[0]["mode"] == "normal"
    assert isinstance(rows[0]["covered"], bool)
    assert rows[0]["library"] == "lib"


def test_f1_merge_keeps_run_ids(tmp_path):
    """ut-F1-4：合并保留 run_id 列（2 次 run → 2 个不同 run_id）。"""
    _run(tmp_path, runs=2, seed=3)
    rows = merge_results(tmp_path, "normal", "lib")
    run_ids = {r["run_id"] for r in rows}
    assert len(run_ids) == 2


def test_f1_reproducible_content(tmp_path):
    """ut-F9-3：同参数重跑（覆盖同 run_id 文件）→ 合并结果等价（幂等）。"""
    _run(tmp_path, runs=2, seed=5)
    rows1 = merge_results(tmp_path, "normal", "lib")
    _run(tmp_path, runs=2, seed=5)  # 相同种子重跑 → 相同 run_id 目录被覆盖
    rows2 = merge_results(tmp_path, "normal", "lib")

    def _row_key(row):
        return (row["run_id"], row["func_name"], row["candidate_index"])

    assert sorted(rows1, key=_row_key) == sorted(rows2, key=_row_key)


def test_f1_artifact_files_exist(tmp_path):
    """it-F1：每 run 目录含 manifest/decode_config/results 三件套。"""
    out, _, _ = _run(tmp_path, runs=1)
    run_dir = next(out.glob("r*_s*"))
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "decode_config.json").exists()
    assert (run_dir / "results.jsonl").exists()
    dec = json.loads((run_dir / "decode_config.json").read_text())
    assert dec["temperature"] == 0.7  # F4 交叉
    man = json.loads((run_dir / "manifest.json").read_text())
    assert "usage" in man  # F5 交叉：每 run 资源计数落盘



def test_f1_limit_fixed_batch_across_runs(tmp_path):
    """ED-4 固定批采样：limit 批跨 run 保持同一函数集（配对口径，
    非每 run 重新采样——回归：此前按 run_seed 洗牌截断导致 5 run 子集漂移）。"""
    out, _, _ = _run(tmp_path, runs=3, limit=3, seed=11)
    per_run = []
    for d in sorted(out.glob("r*_s*")):
        funcs = {
            json.loads(l)["func_name"]
            for l in (d / "results.jsonl").read_text().splitlines()
        }
        per_run.append(funcs)
    assert [len(s) for s in per_run] == [3, 3, 3]
    assert all(s == per_run[0] for s in per_run)  # 跨 run 同一函数集

def test_f1_rag_requires_library(tmp_path):
    """rag 模式未指定 library → 显式报错（每库一索引）。"""
    import pytest

    with pytest.raises(RuntimeError, match="rag 需要 --library|rag 模式"):
        run_experiment(
            "rag",
            None,
            runs=1,
            k=3,
            base_seed=1,
            outdir=tmp_path,
            functions=_funcs(),
            allow_fake_embedding=True,
        )



def test_target_importable_shortcut():
    """ours 短路探测：运行时不可导入目标（pyi 私有路径）→ False，
    占位 covered=False 与对照 CALLFAIL 对齐（paddle.amp.* 类省巨额无效 solve）。"""
    from experiments.RQ1.engine.driver import _target_module_importable

    assert _target_module_importable("numpy.linalg.norm") is True
    assert _target_module_importable("nonexistent_mod.foo") is False
    assert _target_module_importable("paddle.amp.core.libpaddle.pir.ops.repeat_interleave_grad") is False


def test_rag_retrieval_full_pool_and_excludes_self(tmp_path):
    """MC6-2：RAG 检索语料 = 采样前整库池（--limit 不塌缩），且默认剔除目标自身。

    两个回归点：
      - 语料若随 limit 塌缩成评测子集，剔除自身后只剩 ≤1 个 chunk 可检索；
      - 若不剔除自身，RAG 会「检索到目标自己的源码」，与 Extended 的源码直拼混淆。
    """
    llm = FakeLLM(["<code>y=1</code>"] * 40)
    ex = FakeExecutor([True] * 40)
    run_experiment(
        "rag",
        "lib",
        runs=1,
        k=1,
        base_seed=1,
        outdir=tmp_path,
        limit=2,  # 评测子集 2 个函数，但检索语料仍应是 4 个
        llm=llm,
        executor=ex,
        functions=_funcs(),
        now="2026-08-29T00:00:00",
        warmup=False,
    )
    assert llm.prompts, "rag 模式应至少产生一次 LLM 调用"
    for prompt in llm.prompts:
        target = re.search(r"为(\S+)生成", prompt)
        assert target, "prompt 缺目标函数名"
        block = re.search(r"<retrieval>\n(.*?)\n</retrieval>", prompt, flags=re.DOTALL)
        assert block, "rag prompt 未注入 <retrieval>"
        text = block.group(1)
        own_idx = target.group(1).rsplit("f", 1)[1]
        assert f"def f{own_idx}(): pass" not in text, "检索结果不应含目标函数自身"
        others = set(re.findall(r"def f(\d)\(\): pass", text))
        assert len(others) >= 2, f"检索语料应为整库池而非 limit 子集：{others}"
