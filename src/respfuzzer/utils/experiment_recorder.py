"""把每一次实验的**日志**与**结果数据**落盘为 JSON（【MC3-1】）。

评审「主要意见 3」第 1 条要求「在开源仓库中存放每一次实验的日志和结果数据（增加
保存结果为 json 的功能）」。本模块是该能力的框架侧实现，供框架入口
（``respfuzzer.cmd.fuzz`` / ``fuzz_exp``）与 RQ1–RQ4 的实验驱动（环节②）共用。

一次 ``start_experiment(...)`` 对应一次独立运行（run），在
``run_data/experiments/<run_id>/`` 下落盘三个文件：

- ``logs.jsonl``   —— JSON Lines 格式的 loguru 日志，每行一条日志记录；
- ``events.jsonl`` —— JSON Lines 格式的结构化事件（变异执行、无效 LLM 输出……），
  是 run 级结果数据的明细来源；
- ``result.json``  —— 本次运行的汇总（状态、耗时、环境、配置快照、结果数据、
  事件计数），即对外公开的「结果数据」。

设计要点：

- **线程 + fork 安全**：模糊测试主循环是 ``max_workers`` 个 worker 线程外加 fork
  出来的执行子进程，日志与事件可能并发写入；写文件持全局锁，并通过
  ``os.register_at_fork(after_in_child=...)`` 在子进程里重置锁，避免子进程继承到
  父进程已被占用的锁而死锁。
- **未启动即 no-op**：``record_event`` / ``record_result`` 在没有活动 recorder 时
  直接返回，因此库代码（如 ``llm_mutator``）可以无条件调用，不必感知实验是否在录制。
- **配置快照脱敏**：``api_key`` 等敏感字段写入 json 前统一替换为 ``***``。
- **原子落盘**：``result.json`` 先写临时文件再 ``os.replace``，避免半截文件。
- **落盘失败不打断实验**：日志 sink 写入失败只计数，``__exit__`` 里结果写失败只报错。
"""

import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tomllib import TOMLDecodeError
from types import TracebackType
from typing import Any, Self

from loguru import logger

from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import PROJECT_DIR, RUNDATA_DIR

SCHEMA_VERSION = 1
DEFAULT_RESULTS_DIR = RUNDATA_DIR / "experiments"
LOG_FILENAME = "logs.jsonl"
EVENT_FILENAME = "events.jsonl"
RESULT_FILENAME = "result.json"

# 写进 json 前需要脱敏的字段名（大小写不敏感，按子串匹配）
SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "password",
    "secret",
    "authorization",
    "private_key",
)

# 全局状态锁（可重入）与文件写入锁。顺序恒为：全局锁 -> 写入锁。
_global_lock = threading.RLock()
_io_lock = threading.Lock()


def _reset_locks_after_fork() -> None:
    """fork 之后在子进程里换一把新锁。

    父进程的某个线程可能正持锁写文件，被 ``Process(target=...)`` fork 出来的执行
    子进程若继承了「已上锁」的锁对象，任何后续写入都会永久阻塞。
    """
    global _global_lock, _io_lock
    _global_lock = threading.RLock()
    _io_lock = threading.Lock()


os.register_at_fork(after_in_child=_reset_locks_after_fork)

_active: "ExperimentRecorder | None" = None


# ---------------------------------------------------------------------------
# json 工具
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """带时区的当前时间（本地时区），避免 json 里出现裸时间戳。"""
    return datetime.now(tz=UTC).astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="milliseconds")


def _json_safe(value: Any) -> Any:
    """把任意对象转换成可 json 序列化的形式（兜底用 ``repr``，绝不抛异常）。"""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(v) for v in value]
    if isinstance(value, BaseException):
        return repr(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))
    if isinstance(value, bytes):  # pragma: no cover - 兜底分支
        return value.decode("utf-8", "replace")
    return repr(value)


def _scrub_secrets(value: Any) -> Any:
    """把 ``api_key`` 之类的敏感字段替换为 ``***``（递归）。"""
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            if isinstance(key, str) and any(
                marker in key.lower() for marker in SECRET_KEY_MARKERS
            ):
                scrubbed[key] = "***"
            else:
                scrubbed[key] = _scrub_secrets(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub_secrets(v) for v in value]
    return value


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_safe)


def _error_payload(error: Any) -> Any:
    """把异常转成可读且可 json 的结构（字符串原样保留）。"""
    if error is None:
        return None
    if isinstance(error, str):
        return error
    return {"type": type(error).__name__, "message": str(error)}


def _config_snapshot() -> dict[str, Any]:
    """读取 config.toml 全量快照（脱敏），供复现时核对解码参数等设置。"""
    try:
        raw: Any = get_config()
    except (OSError, TOMLDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return _scrub_secrets(_json_safe(raw))


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else None


def _environment() -> dict[str, Any]:
    return {
        "argv": [str(a) for a in sys.argv],
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "git_commit": _git_commit(),
    }


def _slug(text: str) -> str:
    """把任意名字转成安全的目录名（保留 ``\\w`` / ``.`` / ``-``）。"""
    cleaned = re.sub(r"[^\w.-]+", "-", str(text), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    if not cleaned or cleaned in {".", ".."}:
        return "run"
    return cleaned


def _configured_results_dir() -> Path:
    """落盘根目录：默认 ``run_data/experiments``，可用 ``[experiment] results_dir`` 覆盖。"""
    try:
        section = get_config("experiment")
    except (OSError, TOMLDecodeError, ValueError):
        section = None
    raw = section.get("results_dir") if isinstance(section, dict) else None
    if not raw:
        return DEFAULT_RESULTS_DIR
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else PROJECT_DIR / path


def _make_run_dir(base: Path, name: str, run_id: str | None) -> Path:
    """在锁内挑选并创建本次运行的目录，避免并发运行撞名。"""
    with _io_lock:
        if run_id:
            stem = _slug(run_id)
            run_dir = base / stem
            suffix = 1
            while run_dir.exists():
                suffix += 1
                run_dir = base / f"{stem}-{suffix}"
        else:
            stamp = _now().strftime("%Y%m%dT%H%M%S")
            run_dir = base / f"{_slug(name)}-{stamp}-{os.getpid()}"
            suffix = 1
            while run_dir.exists():
                suffix += 1
                run_dir = base / f"{_slug(name)}-{stamp}-{os.getpid()}-{suffix}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir


# ---------------------------------------------------------------------------
# 记录器
# ---------------------------------------------------------------------------


class ExperimentRecorder:
    """一次独立运行的日志与结果数据收集器。

    通常不直接构造，而是通过 :func:`start_experiment` 启动；用 ``with`` 包住实验
    主体即可在正常结束时写 ``result.json``（成功）、异常结束时写 ``failed``
    （含异常信息）。
    """

    def __init__(
        self,
        run_dir: Path,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.name = name
        self.metadata = _scrub_secrets(_json_safe(dict(metadata or {})))
        self.config = (
            _scrub_secrets(_json_safe(config))
            if config is not None
            else _config_snapshot()
        )
        self.results: dict[str, Any] = {}
        self.event_counts: dict[str, int] = {}
        self.log_records = 0
        self.events_written = 0
        self.write_errors = 0
        self.started_at = _now()
        self.finished_at: datetime | None = None
        self._sink_id: int | None = None
        self._closed = False

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logs_path = self.run_dir / LOG_FILENAME
        self.events_path = self.run_dir / EVENT_FILENAME
        self.result_path = self.run_dir / RESULT_FILENAME

    # -- 属性 ---------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    # -- 日志 ---------------------------------------------------------------

    def attach_log_sink(self, level: str = "DEBUG") -> None:
        """把本 recorder 注册为 loguru sink，日志以 JSON Lines 落盘。

        只**追加** sink，不动其它 sink，因此不会影响实验已有的 stderr 日志格式
        （``report.py`` 仍按文本日志解析覆盖率）。
        """
        if self._sink_id is not None or self._closed:
            return
        self._sink_id = logger.add(self._loguru_sink, level=level, catch=True)

    def detach_log_sink(self) -> None:
        sink_id, self._sink_id = self._sink_id, None
        if sink_id is None:
            return
        try:
            logger.remove(sink_id)
        except ValueError:
            # sink 已被外部的 logger.remove() 清掉（实验入口会重设日志）
            logger.debug(f"Log sink {sink_id} of run {self.run_id} already removed")

    def _loguru_sink(self, message: str) -> None:
        record = getattr(message, "record", None)
        payload = self._log_payload(record, str(message))
        with _io_lock:
            if self._closed:
                return
            try:
                self._append(self.logs_path, payload)
            except OSError:
                # 文件写入失败（磁盘满等）只计数，绝不让 sink 抛回 loguru
                self.write_errors += 1
                return
            self.log_records += 1

    @staticmethod
    def _log_payload(record: Any, fallback: str) -> dict[str, Any]:
        if not isinstance(record, dict):
            return {"ts": _now_iso(), "level": "UNKNOWN", "message": fallback}
        payload: dict[str, Any] = {
            "ts": record["time"].isoformat(timespec="milliseconds"),
            "level": record["level"].name,
            "logger": record["name"],
            "function": record["function"],
            "line": record["line"],
            "thread": record["thread"].name,
            "process": record["process"].id,
            "message": record["message"],
        }
        exception = record.get("exception")
        if exception:
            payload["exception"] = {
                "type": getattr(getattr(exception, "type", None), "__name__", None),
                "value": str(getattr(exception, "value", "")),
                "traceback": str(getattr(exception, "traceback", "")),
            }
        return payload

    # -- 事件与结果 ---------------------------------------------------------

    def record_event(self, event_type: str, **fields: Any) -> None:
        """追加一条结构化事件到 ``events.jsonl``（无活动 recorder 或已收尾时为 no-op）。"""
        if self._closed:
            return
        payload = _json_safe(dict(fields))
        payload.setdefault("ts", _now_iso())
        payload["event"] = str(event_type)
        with _io_lock:
            if self._closed:
                return
            self._append(self.events_path, payload)
            self.events_written += 1
            key = str(payload["event"])
            self.event_counts[key] = self.event_counts.get(key, 0) + 1

    def record_result(self, key: str, value: Any) -> None:
        """写入一条汇总结果数据（最终进入 ``result.json`` 的 ``results`` 字段）。"""
        if self._closed:
            return
        with _io_lock:
            if self._closed:
                return
            self.results[str(key)] = _json_safe(value)

    def record_results(self, mapping: dict[str, Any]) -> None:
        for key, value in mapping.items():
            self.record_result(key, value)

    # -- 收尾 ---------------------------------------------------------------

    def build_result(self, status: str, error: Any = None) -> dict[str, Any]:
        finished = self.finished_at or _now()
        duration = (finished - self.started_at).total_seconds()
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "name": self.name,
            "status": status,
            "error": error,
            "started_at": self.started_at.isoformat(timespec="milliseconds"),
            "finished_at": finished.isoformat(timespec="milliseconds"),
            "duration_seconds": round(duration, 3),
            "environment": _environment(),
            "config": self.config,
            "metadata": self.metadata,
            "results": self.results,
            "event_counts": dict(sorted(self.event_counts.items())),
            "totals": {
                "log_records": self.log_records,
                "events": self.events_written,
                "write_errors": self.write_errors,
            },
            "files": {
                "logs": LOG_FILENAME,
                "events": EVENT_FILENAME,
                "result": RESULT_FILENAME,
            },
        }

    def finish(self, status: str = "completed", error: Any = None) -> Path:
        """收尾：摘除日志 sink，原子写出 ``result.json``。幂等，可重复调用。"""
        global _active
        error_payload = _error_payload(error)
        if error is not None and status == "completed":
            status = "failed"
        with _global_lock:
            if self._closed:
                return self.result_path
            self._closed = True
            if _active is self:
                _active = None
        self.detach_log_sink()
        self.finished_at = _now()
        payload = self.build_result(status, error_payload)
        with _io_lock:
            tmp_path = self.result_path.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                handle.write(_dump(payload))
                handle.write("\n")
            os.replace(tmp_path, self.result_path)
        logger.debug(f"Experiment result saved to {self.result_path}")
        return self.result_path

    # -- 上下文管理器 -------------------------------------------------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        try:
            if exc is None:
                self.finish("completed")
            else:
                self.finish("failed", error=exc)
        except OSError:
            # 结果落盘失败不能吞掉实验本身的异常
            logger.opt(exception=True).error(
                f"Failed to write experiment result json for run {self.run_id}"
            )
        return False

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _append(path: Path, payload: dict[str, Any]) -> None:
        """追加一行 JSON。每次写都重新 open（O_APPEND），fork 出来的子进程也能安全写。"""
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_dump(payload))
            handle.write("\n")


# ---------------------------------------------------------------------------
# 模块级 API
# ---------------------------------------------------------------------------


def start_experiment(
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    run_id: str | None = None,
    results_dir: str | os.PathLike[str] | None = None,
    log_level: str | None = "DEBUG",
    capture_logs: bool = True,
) -> ExperimentRecorder:
    """启动一次独立运行的录制，返回 recorder（可直接用于 ``with``）。

    Args:
        name: 运行名，用于生成目录名与 ``result.json`` 的 ``name`` 字段。
        metadata: 运行级元数据（数据集路径、随机种子、实验组名……），原样落盘。
        config: 配置快照；缺省时自动快照 ``config.toml``（脱敏后）。
        run_id: 显式指定目录名（同一实验多次运行需保持可读命名时用）。
        results_dir: 覆盖落盘根目录；缺省走 ``[experiment] results_dir`` 或
            ``run_data/experiments``。
        log_level: 日志 sink 级别；``None`` 表示不挂日志 sink。
        capture_logs: 是否把 loguru 日志写入 ``logs.jsonl``。
    """
    global _active
    with _global_lock:
        previous = _active
        _active = None
    if previous is not None and not previous.closed:
        # 同一进程里连续启动两次：先把上一次收尾，避免结果互相覆盖
        previous.finish(status="superseded")

    base = Path(results_dir) if results_dir is not None else _configured_results_dir()
    run_dir = _make_run_dir(base, name, run_id)
    recorder = ExperimentRecorder(run_dir, name, metadata=metadata, config=config)
    with _global_lock:
        _active = recorder
    if capture_logs:
        recorder.attach_log_sink(level=log_level or "DEBUG")
    logger.debug(f"Recording experiment run {recorder.run_id} into {run_dir}")
    return recorder


def get_recorder() -> ExperimentRecorder | None:
    """返回当前活动的 recorder；没有则返回 ``None``。"""
    with _global_lock:
        return _active


def is_recording() -> bool:
    return get_recorder() is not None


def record_event(event_type: str, **fields: Any) -> None:
    """向当前运行追加一条结构化事件；**未启动录制时是 no-op**。"""
    recorder = get_recorder()
    if recorder is not None:
        recorder.record_event(event_type, **fields)


def record_result(key: str, value: Any) -> None:
    """向当前运行写入一条结果数据；**未启动录制时是 no-op**。"""
    recorder = get_recorder()
    if recorder is not None:
        recorder.record_result(key, value)


def record_results(mapping: dict[str, Any]) -> None:
    recorder = get_recorder()
    if recorder is not None:
        recorder.record_results(mapping)


def finish_experiment(status: str = "completed", error: Any = None) -> Path | None:
    """收尾当前运行并返回 ``result.json`` 路径；未启动录制时返回 ``None``。"""
    recorder = get_recorder()
    if recorder is None:
        return None
    return recorder.finish(status, error)


@contextmanager
def experiment_run(name: str, **kwargs: Any) -> Iterator[ExperimentRecorder]:
    """``with experiment_run("rq3-respfuzzer") as rec:`` 的便捷封装。

    异常结束时以 ``failed`` + 异常信息收尾，异常继续向上抛出。
    """
    recorder = start_experiment(name, **kwargs)
    try:
        yield recorder
    except BaseException as exc:
        recorder.finish("failed", error=exc)
        raise
    else:
        recorder.finish("completed")
