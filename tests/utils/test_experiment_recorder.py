"""MC3-1 的单元测试：保存每一次实验的日志与结果数据（json）。

覆盖：

- 启动一次运行 → 落盘 ``logs.jsonl`` / ``events.jsonl`` / ``result.json``；
- 日志以 JSON Lines 记录、事件与结果数据进入 ``result.json``；
- 配置快照脱敏（``api_key`` 绝不落盘）；
- 未启动录制时各 record_* 接口为 no-op；
- 异常结束标记 ``failed`` 并保留异常类型，收尾幂等；
- 多线程并发写事件不丢行、计数一致；
- ``llm_mutator`` 的无效 LLM 输出 ``reason`` / ``detail`` 落盘（MC2-7 记录中标注
  「未纳入本次」的那一项）。
"""

import json
import threading
from types import SimpleNamespace

import pytest
from loguru import logger

from respfuzzer.utils.experiment_recorder import (
    EVENT_FILENAME,
    LOG_FILENAME,
    RESULT_FILENAME,
    finish_experiment,
    get_recorder,
    is_recording,
    record_event,
    record_result,
    start_experiment,
)

VALID_CODE = "import math\nresult = math.ceil(1.5)"
NO_TARGET_CALL_CODE = "import math\nresult = math.floor(1.5)"


@pytest.fixture(autouse=True)
def _clean_recorder():
    """用例结束后确保没有残留的活动 recorder / 日志 sink。"""
    yield
    finish_experiment(status="aborted")
    assert get_recorder() is None


def _read_jsonl(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _result_payload(rec) -> dict:
    return json.loads(rec.result_path.read_text(encoding="utf-8"))


def test_start_writes_logs_events_and_result(tmp_path):
    rec = start_experiment("rq3 demo", results_dir=tmp_path, metadata={"seed": 7})
    assert rec.run_dir.parent == tmp_path
    assert "rq3-demo" in rec.run_id

    record_event("mutant_executed", func_name="math.ceil", cov_gain=1)
    record_result("final_coverage", 123)
    path = rec.finish()

    assert path == rec.result_path
    assert path.exists()
    assert (rec.run_dir / LOG_FILENAME).exists()
    assert (rec.run_dir / EVENT_FILENAME).exists()
    assert not rec.result_path.with_suffix(".json.tmp").exists(), "结果必须原子落盘"

    payload = _result_payload(rec)
    assert payload["schema_version"] == 1
    assert payload["name"] == "rq3 demo"
    assert payload["run_id"] == rec.run_id
    assert payload["status"] == "completed"
    assert payload["error"] is None
    assert payload["results"] == {"final_coverage": 123}
    assert payload["event_counts"] == {"mutant_executed": 1}
    assert payload["totals"]["events"] == 1
    assert payload["totals"]["log_records"] >= 1
    assert payload["metadata"] == {"seed": 7}
    assert payload["environment"]["python"]
    assert isinstance(payload["config"], dict)
    assert payload["files"] == {
        "logs": LOG_FILENAME,
        "events": EVENT_FILENAME,
        "result": RESULT_FILENAME,
    }
    assert payload["started_at"] <= payload["finished_at"]
    assert payload["duration_seconds"] >= 0

    events = _read_jsonl(rec.events_path)
    assert len(events) == 1
    assert events[0]["event"] == "mutant_executed"
    assert events[0]["func_name"] == "math.ceil"
    assert events[0]["cov_gain"] == 1
    assert "ts" in events[0]


def test_logs_are_json_lines_with_level_and_message(tmp_path):
    rec = start_experiment("log-run", results_dir=tmp_path)
    logger.info("hello {}", "world")
    logger.debug("dbg message")
    rec.finish()

    logs = _read_jsonl(rec.logs_path)
    messages = [entry["message"] for entry in logs]
    assert "hello world" in messages
    assert "dbg message" in messages
    assert "INFO" in {entry["level"] for entry in logs}
    assert all({"ts", "level", "message"} <= set(entry) for entry in logs)
    assert all(entry["process"] for entry in logs)

    payload = _result_payload(rec)
    assert payload["totals"]["log_records"] == len(logs)


def test_record_helpers_are_noop_without_active_run(tmp_path):
    assert get_recorder() is None
    assert is_recording() is False
    record_event("should_not_raise", foo=1)
    record_result("k", 1)
    assert finish_experiment() is None


def test_config_snapshot_masks_api_key(tmp_path):
    secret = "sk-SECRET-VALUE"
    rec = start_experiment(
        "masked",
        results_dir=tmp_path,
        config={"llm": {"api_key": secret, "model_name": "Qwen3.6-35B-A3B"}},
    )
    rec.finish()

    payload = _result_payload(rec)
    assert payload["config"]["llm"]["api_key"] == "***"
    assert payload["config"]["llm"]["model_name"] == "Qwen3.6-35B-A3B"
    for path in rec.run_dir.iterdir():
        assert secret not in path.read_text(encoding="utf-8"), f"{path} 泄露了 api_key"


def test_exception_marks_run_failed_and_keeps_results(tmp_path):
    rec = start_experiment("boom", results_dir=tmp_path)
    with pytest.raises(ValueError, match="boom"), rec:
        record_result("partial", 1)
        raise ValueError("boom")

    payload = _result_payload(rec)
    assert payload["status"] == "failed"
    assert payload["error"] == {"type": "ValueError", "message": "boom"}
    assert payload["results"] == {"partial": 1}
    assert get_recorder() is None, "异常收尾后不应留下活动 recorder"


def test_finish_is_idempotent_and_later_writes_are_ignored(tmp_path):
    rec = start_experiment("twice", results_dir=tmp_path)
    first = rec.finish()

    record_event("after_finish", x=1)
    record_result("after", 2)
    second = rec.finish()

    assert first == second
    payload = _result_payload(rec)
    assert payload["event_counts"] == {}
    assert payload["results"] == {}
    assert not (rec.run_dir / EVENT_FILENAME).exists()
    assert get_recorder() is None
    assert is_recording() is False


def test_start_experiment_supersedes_previous_run(tmp_path):
    first = start_experiment("first", results_dir=tmp_path)
    second = start_experiment("second", results_dir=tmp_path)

    assert first.closed
    assert get_recorder() is second
    assert _result_payload(first)["status"] == "superseded"
    second.finish()


def test_concurrent_events_are_all_written(tmp_path):
    rec = start_experiment("threads", results_dir=tmp_path)
    per_worker = 50

    def worker(index: int) -> None:
        for i in range(per_worker):
            record_event("worker_event", worker=index, i=i)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rec.finish()

    events = _read_jsonl(rec.events_path)
    assert len(events) == 8 * per_worker
    payload = _result_payload(rec)
    assert payload["totals"]["events"] == 8 * per_worker
    assert payload["event_counts"] == {"worker_event": 8 * per_worker}


def test_run_id_collision_gets_suffix(tmp_path):
    first = start_experiment("same", results_dir=tmp_path, run_id="rq1-run")
    first.finish()
    second = start_experiment("same", results_dir=tmp_path, run_id="rq1-run")
    second.finish()

    assert first.run_dir != second.run_dir
    assert second.run_dir.name == "rq1-run-2"
    assert (tmp_path / "rq1-run" / RESULT_FILENAME).exists()


def test_run_dir_stays_inside_results_dir(tmp_path):
    rec = start_experiment("../escape/实验名", results_dir=tmp_path)
    rec.finish()

    assert rec.run_dir.parent == tmp_path
    assert rec.run_id not in {".", "..", ""}


def test_invalid_llm_output_lands_in_events(tmp_path, monkeypatch):
    """无效 LLM 输出的 reason/detail 必须写进 events.jsonl（MC2-7 → MC3-1）。"""
    from respfuzzer.lib.fuzz import llm_mutator
    from respfuzzer.models import Seed

    def stub(chain, input_dict, config, timeout):
        return SimpleNamespace(code=NO_TARGET_CALL_CODE)

    monkeypatch.setattr(llm_mutator, "_invoke_with_timeout", stub)

    seed = Seed(
        func_id=1,
        library_name="math",
        func_name="math.ceil",
        args=[],
        function_call=VALID_CODE,
    )
    rec = start_experiment("mutator", results_dir=tmp_path)
    mutator = llm_mutator.LLMMutator(seed)
    mutator.select_mutation_type = lambda: 0
    assert mutator.random_llm_mutate() is None
    rec.finish()

    events = _read_jsonl(rec.events_path)
    invalid = [e for e in events if e["event"] == "invalid_llm_output"]
    assert len(invalid) == 1
    assert invalid[0]["reason"] == "no_target_call"
    assert invalid[0]["detail"]
    assert invalid[0]["target_called"] is False
    assert invalid[0]["code"] == NO_TARGET_CALL_CODE
    assert invalid[0]["seed_id"] == seed.id
    assert invalid[0]["mutation_type"] == 0
    assert _result_payload(rec)["event_counts"] == {"invalid_llm_output": 1}


def test_llm_failure_lands_in_events(tmp_path, monkeypatch):
    """LLM 重试耗尽无输出时同样要落盘一条事件。"""
    from respfuzzer.lib.fuzz import llm_mutator
    from respfuzzer.models import Seed

    def stub(chain, input_dict, config, timeout):
        raise TimeoutError("llm down")

    monkeypatch.setattr(llm_mutator, "_invoke_with_timeout", stub)

    seed = Seed(
        func_id=1,
        library_name="math",
        func_name="math.ceil",
        args=[],
        function_call=VALID_CODE,
    )
    rec = start_experiment("mutator-no-output", results_dir=tmp_path)
    mutator = llm_mutator.LLMMutator(seed)
    mutator.select_mutation_type = lambda: 0
    assert mutator.random_llm_mutate() is None
    rec.finish()

    events = _read_jsonl(rec.events_path)
    assert [e["event"] for e in events] == ["llm_no_output"]
    assert events[0]["func_name"] == "math.ceil"
    assert events[0]["seed_id"] == seed.id
