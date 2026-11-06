"""MC3-1 的单元测试：每一次实验落 ``.log`` + ``*_checkpoint.json``。

覆盖：

- 命名契约（rq/group/library/mode/timestamp → stem，log 与 checkpoint 同 stem）；
- 只产出两个文件，没有 run 目录、没有 jsonl 三件套；
- ``.log`` 保持 ``report.py`` 依赖的行首时间戳格式，且 ``print()`` 也被 tee 进去；
- checkpoint 字段齐全（状态、环境、脱敏 config、results、event_counts、内嵌 events）；
- 未启动录制时各 record_* 接口为 no-op；
- 异常结束标记 ``failed``、收尾幂等、覆盖上一次运行；
- 多线程并发写事件不丢行、计数一致；
- ``llm_mutator`` 的无效 LLM 输出 ``reason`` / ``detail`` 落盘（MC2-7 → MC3-1）。
"""

import json
import re
import threading
from types import SimpleNamespace

import pytest
from loguru import logger

from respfuzzer.utils.experiment_recorder import (
    checkpoint_filename,
    finish_run,
    get_recorder,
    is_recording,
    record_event,
    record_result,
    run_stem,
    start_run,
)

VALID_CODE = "import math\nresult = math.ceil(1.5)"
NO_TARGET_CALL_CODE = "import math\nresult = math.floor(1.5)"
TIMESTAMP_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")


@pytest.fixture(autouse=True)
def _clean_recorder():
    """用例结束后确保没有残留的记录器，且 stdout/stderr 已还原。"""
    yield
    finish_run(status="aborted")
    assert get_recorder() is None


def _checkpoint(rec) -> dict:
    return json.loads(rec.checkpoint_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 命名契约
# ---------------------------------------------------------------------------


def test_run_stem_follows_the_naming_contract():
    assert run_stem("RQ3", "respfuzzer", "nltk", timestamp="20261010T090000") == (
        "rq3_respfuzzer_nltk_20261010T090000"
    )
    assert run_stem("RQ3", "respfuzzer", "Nltk", "NL", "20261010T090000") == (
        "rq3_respfuzzer_nltk_20261010T090000_mode-nl"
    )
    # Full 是默认组：文件名里不带 mode 段
    assert run_stem("RQ3", "respfuzzer", "nltk", "Full", "20261010T090000") == (
        "rq3_respfuzzer_nltk_20261010T090000"
    )
    # 一次运行覆盖多库（RQ1 生成脚本）时省略 library
    assert run_stem("RQ1", "respfuzzer", timestamp="20261010T090000") == (
        "rq1_respfuzzer_20261010T090000"
    )
    # 组名里的连字符保留（RQ2 的 sce-only）
    assert run_stem("RQ2", "SCE-only", "nltk", timestamp="20261010T090000") == (
        "rq2_sce-only_nltk_20261010T090000"
    )


def test_log_and_checkpoint_share_the_stem(tmp_path):
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    rec.finish()

    assert rec.log_path.name == "rq3_respfuzzer_nltk_20261010T090000.log"
    assert rec.checkpoint_path.name == (
        "rq3_respfuzzer_nltk_20261010T090000_checkpoint.json"
    )
    assert rec.stem + "_checkpoint.json" == rec.checkpoint_path.name
    assert rec.stem + ".log" == rec.log_path.name
    assert checkpoint_filename(rec.stem) == rec.checkpoint_path.name


def test_only_two_files_are_produced(tmp_path):
    """没有 run 目录，也没有 logs/events jsonl 三件套。"""
    rec = start_run(
        "RQ3",
        group="fuzz4all",
        library="dask",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    record_event("seed_fuzzed", func_name="dask.delayed", coverage=10)
    record_result("final_coverage", 10)
    rec.finish()

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "rq3_fuzz4all_dask_20261010T090000.log",
        "rq3_fuzz4all_dask_20261010T090000_checkpoint.json",
    ]
    assert all(p.is_file() for p in tmp_path.iterdir())


def test_same_second_restart_gets_suffix(tmp_path):
    first = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    first.finish()
    second = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    second.finish()

    assert first.log_path.name == "rq3_respfuzzer_nltk_20261010T090000.log"
    assert second.log_path.name == "rq3_respfuzzer_nltk_20261010T090000-2.log"
    assert second.checkpoint_path.name == (
        "rq3_respfuzzer_nltk_20261010T090000-2_checkpoint.json"
    )


# ---------------------------------------------------------------------------
# .log
# ---------------------------------------------------------------------------


def test_log_keeps_the_report_py_line_format(tmp_path):
    """report.py 按行首 ``%Y-%m-%d %H:%M:%S.%f`` 与消息文本解析，格式不能变。"""
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    logger.info("Current coverage after fuzzing nltk.tokenize: 123 bits")
    rec.finish()

    lines = [ln for ln in rec.log_path.read_text(encoding="utf-8").splitlines() if ln]
    assert lines, ".log 不应为空"
    assert TIMESTAMP_LINE.match(lines[0]), "首行必须带时间戳（report.py 取它算 t0）"
    assert any(
        "Current coverage after fuzzing nltk.tokenize: 123 bits" in ln for ln in lines
    )
    assert any("INFO" in ln for ln in lines)


def test_print_is_teed_into_the_log_and_streams_are_restored(tmp_path):
    import sys

    original_out, original_err = sys.stdout, sys.stderr
    rec = start_run(
        "RQ1", group="respfuzzer", out_dir=tmp_path, timestamp="20261010T090000"
    )
    print("generated 20 seed files")
    rec.finish()

    assert sys.stdout is original_out, "收尾后必须还原 stdout"
    assert sys.stderr is original_err, "收尾后必须还原 stderr"
    assert "generated 20 seed files" in rec.log_path.read_text(encoding="utf-8")
    assert rec.checkpoint_path.exists()


# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_schema_and_embedded_events(tmp_path):
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        mode="NL",
        out_dir=tmp_path,
        timestamp="20261010T090000",
        metadata={"seed": 42},
    )
    logger.info("Starting SGM Fuzzing for seed 1: nltk.tokenize")
    record_event(
        "mutant_executed",
        seed_id=1,
        mutant_id=9,
        func_name="nltk.tokenize",
        slot=4,
        mutation_type=0,
        cov_before=10,
        cov_after=11,
        cov_gain=1,
        has_runtime_error=False,
        outcome="ok",
    )
    record_result("seed_count", 565)
    record_result("final_coverage", 27432)
    path = rec.finish()

    assert path == rec.checkpoint_path
    payload = _checkpoint(rec)
    assert payload["schema_version"] == 1
    assert payload["rq"] == "RQ3"
    assert payload["group"] == "respfuzzer"
    assert payload["library"] == "nltk"
    assert payload["mode"] == "NL"
    assert payload["run_id"] == rec.stem
    assert payload["log"] == rec.log_path.name
    assert payload["checkpoint"] == rec.checkpoint_path.name
    assert payload["status"] == "completed"
    assert payload["error"] is None
    assert payload["metadata"] == {"seed": 42}
    assert payload["results"] == {"seed_count": 565, "final_coverage": 27432}
    assert payload["event_counts"] == {"mutant_executed": 1}
    assert payload["totals"]["events"] == 1
    assert payload["totals"]["log_lines"] >= 1
    assert payload["totals"]["write_errors"] == 0
    assert payload["environment"]["python"]
    assert isinstance(payload["config"], dict)
    assert payload["started_at"] <= payload["finished_at"]
    assert payload["duration_seconds"] >= 0

    # 结构化明细内嵌在同一个文件里
    events = payload["events"]
    assert len(events) == 1
    assert events[0]["event"] == "mutant_executed"
    assert events[0]["cov_gain"] == 1
    assert "ts" in events[0]


def test_checkpoint_is_valid_json_with_every_event(tmp_path):
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    for i in range(5):
        record_event("seed_fuzzed", func_name=f"nltk.f{i}", coverage=i)
    rec.finish()

    payload = _checkpoint(rec)
    assert len(payload["events"]) == 5
    assert payload["event_counts"] == {"seed_fuzzed": 5}
    # 原子落盘：没有半截临时文件
    assert not rec.checkpoint_path.with_suffix(".json.tmp").exists()


def test_record_helpers_are_noop_without_active_run():
    assert get_recorder() is None
    assert is_recording() is False
    record_event("should_not_raise", foo=1)
    record_result("k", 1)
    assert finish_run() is None


def test_config_and_metadata_mask_api_key(tmp_path):
    secret = "sk-SECRET-VALUE"
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
        config={"llm": {"api_key": secret, "model_name": "Qwen3.6-35B-A3B"}},
        metadata={"api_key": secret},
    )
    rec.finish()

    payload = _checkpoint(rec)
    assert payload["config"]["llm"]["api_key"] == "***"
    assert payload["config"]["llm"]["model_name"] == "Qwen3.6-35B-A3B"
    assert payload["metadata"]["api_key"] == "***"
    for path in tmp_path.iterdir():
        assert secret not in path.read_text(encoding="utf-8"), f"{path} 泄露了 api_key"


def test_exception_marks_run_failed_and_keeps_results(tmp_path):
    import sys

    original_out = sys.stdout
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    with pytest.raises(ValueError, match="boom"), rec:
        record_result("partial", 1)
        raise ValueError("boom")

    payload = _checkpoint(rec)
    assert payload["status"] == "failed"
    assert payload["error"] == {"type": "ValueError", "message": "boom"}
    assert payload["results"] == {"partial": 1}
    assert sys.stdout is original_out, "异常收尾也要还原 stdout"
    assert get_recorder() is None, "异常收尾后不应留下活动记录器"


def test_finish_is_idempotent_and_later_writes_are_ignored(tmp_path):
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    first = rec.finish()

    record_event("after_finish", x=1)
    record_result("after", 2)
    second = rec.finish()

    assert first == second
    payload = _checkpoint(rec)
    assert payload["event_counts"] == {}
    assert payload["results"] == {}
    assert get_recorder() is None
    assert is_recording() is False


def test_start_run_supersedes_previous_run(tmp_path):
    first = start_run("RQ3", group="respfuzzer", library="nltk", out_dir=tmp_path)
    second = start_run("RQ3", group="respfuzzer", library="dask", out_dir=tmp_path)

    assert first.closed
    assert get_recorder() is second
    assert _checkpoint(first)["status"] == "superseded"
    second.finish()


def test_concurrent_events_are_all_embedded(tmp_path):
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20261010T090000",
    )
    per_worker = 50

    def worker(index: int) -> None:
        for i in range(per_worker):
            record_event("worker_event", worker=index, i=i)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rec.finish()

    payload = _checkpoint(rec)
    assert len(payload["events"]) == 8 * per_worker
    assert payload["totals"]["events"] == 8 * per_worker
    assert payload["event_counts"] == {"worker_event": 8 * per_worker}


# ---------------------------------------------------------------------------
# 框架入口的库名推导 + llm_mutator 事件
# ---------------------------------------------------------------------------


def test_library_is_inferred_from_dataset_filename():
    from respfuzzer.lib.fuzz.fuzz_exp import _library_from_dataset

    assert _library_from_dataset("rq2_111_data/nltk_seeds_sampled.json") == "nltk"
    assert _library_from_dataset("data/numpy_functions.json") == "numpy"
    assert _library_from_dataset("x/paddle_seeds_dyfuzz.json") == "paddle"
    assert _library_from_dataset("bare.json") == "bare"


def test_invalid_llm_output_lands_in_checkpoint(tmp_path, monkeypatch):
    """无效 LLM 输出的 reason/detail 必须写进 checkpoint（MC2-7 → MC3-1）。"""
    from respfuzzer.lib.fuzz import llm_mutator
    from respfuzzer.models import Seed

    def stub(chain, input_dict, config, timeout):
        return SimpleNamespace(code=NO_TARGET_CALL_CODE)

    monkeypatch.setattr(llm_mutator, "_invoke_with_timeout", stub)

    seed = Seed(
        func_id=1,
        library_name="math",
        func_path="math.ceil",
        args=[],
        function_call=VALID_CODE,
    )
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="math",
        out_dir=tmp_path,
        timestamp="20261010T090000",
        capture_stdout=False,
    )
    mutator = llm_mutator.LLMMutator(seed)
    mutator.select_mutation_type = lambda: 0
    assert mutator.random_llm_mutate() is None
    rec.finish()

    payload = _checkpoint(rec)
    invalid = [e for e in payload["events"] if e["event"] == "invalid_llm_output"]
    assert len(invalid) == 1
    assert invalid[0]["reason"] == "no_target_call"
    assert invalid[0]["detail"]
    assert invalid[0]["target_called"] is False
    assert invalid[0]["code"] == NO_TARGET_CALL_CODE
    assert invalid[0]["seed_id"] == seed.id
    assert invalid[0]["mutation_type"] == 0
    assert payload["event_counts"] == {"invalid_llm_output": 1}


def test_llm_failure_lands_in_checkpoint(tmp_path, monkeypatch):
    """LLM 重试耗尽无输出时同样要落盘一条事件。"""
    from respfuzzer.lib.fuzz import llm_mutator
    from respfuzzer.models import Seed

    def stub(chain, input_dict, config, timeout):
        raise TimeoutError("llm down")

    monkeypatch.setattr(llm_mutator, "_invoke_with_timeout", stub)

    seed = Seed(
        func_id=1,
        library_name="math",
        func_path="math.ceil",
        args=[],
        function_call=VALID_CODE,
    )
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="math",
        out_dir=tmp_path,
        timestamp="20261010T090000",
        capture_stdout=False,
    )
    mutator = llm_mutator.LLMMutator(seed)
    mutator.select_mutation_type = lambda: 0
    assert mutator.random_llm_mutate() is None
    rec.finish()

    payload = _checkpoint(rec)
    assert [e["event"] for e in payload["events"]] == ["llm_no_output"]
    assert payload["events"][0]["func_name"] == "math.ceil"
    assert payload["events"][0]["seed_id"] == seed.id


# ---------------------------------------------------------------------------
# MC3-5: decode_config 固化测试
# ---------------------------------------------------------------------------


def test_build_decode_config_defaults(monkeypatch, tmp_path):
    """decode_config 使用 config.toml 的值或回退代码默认。"""
    from respfuzzer.utils.experiment_recorder import build_decode_config
    import os

    # 使用默认 config.toml（[llm] 有 model_name/temperature，无 top_p/max_tokens）
    cfg = build_decode_config()
    assert isinstance(cfg, dict)
    assert "llm" in cfg
    assert "llm_mutator" in cfg
    llm = cfg["llm"]
    assert "model_name" in llm
    assert "temperature" in llm
    assert "top_p" in llm
    assert "max_tokens" in llm
    assert "base_url" in llm
    assert "chat_template_kwargs" in llm
    # chat_template_kwargs 固定
    assert llm["chat_template_kwargs"]["enable_thinking"] is False


def test_checkpoint_contains_decode_config(tmp_path):
    """checkpoint 中自动包含 decode_config 字段。"""
    rec = start_run(
        "RQ3",
        group="respfuzzer",
        library="nltk",
        out_dir=tmp_path,
        timestamp="20260101T120000",
        capture_stdout=False,
    )
    rec.finish()
    payload = _checkpoint(rec)
    assert "decode_config" in payload
    dc = payload["decode_config"]
    assert "llm" in dc
    assert "llm_mutator" in dc
    assert "model_name" in dc["llm"]
    assert "temperature" in dc["llm"]
    assert "top_p" in dc["llm"]
    assert "max_tokens" in dc["llm"]
    assert "chat_template_kwargs" in dc["llm"]
    assert isinstance(dc["llm"]["model_name"], str)
    assert isinstance(dc["llm"]["temperature"], float)
    assert isinstance(dc["llm"]["top_p"], float)
    assert isinstance(dc["llm"]["max_tokens"], int)


def test_decode_config_no_secrets(monkeypatch, tmp_path):
    """decode_config 不含敏感信息（api_key 等不应出现）。"""
    from respfuzzer.utils.experiment_recorder import build_decode_config
    cfg = build_decode_config()
    raw = json.dumps(cfg)
    assert "api_key" not in raw.lower()
    assert "sk-" not in raw
    assert "secret" not in raw.lower()
    # base_url 可能包含 host 信息，但不包含 key
