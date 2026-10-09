"""每一次实验落两个文件：统一命名的 ``.log`` + 记录结束状态的 ``*_checkpoint.json``（【MC3-1】）。

评审「主要意见 3」第 1 条要求「在开源仓库中存放每一次实验的日志和结果数据（增加保存结果
为 json 的功能）」。本模块是该能力的框架侧实现，4 个 RQ 的所有实验驱动共用同一套命名与
同一套字段。

**产物契约**（一次运行 = 一个 ``stem``）::

    <rq>_<group>_<library>_<YYYYmmddTHHMMSS>[_mode-<mode>].log
    <rq>_<group>_<library>_<YYYYmmddTHHMMSS>[_mode-<mode>]_checkpoint.json

例如::

    rq3_respfuzzer_nltk_20261010T090000.log
    rq3_respfuzzer_nltk_20261010T090000_checkpoint.json
    rq4_respfuzzer_nltk_20261010T090000_mode-nl.log
    rq2_sce-only_nltk_20261010T100000_checkpoint.json

- ``.log`` 是**可读文本日志**：loguru 文本 sink（格式与 loguru 默认 stderr 格式一致，
  ``experiments/RQ*/report.py`` 依赖的行首时间戳与 ``Current coverage after fuzzing``
  语句因此原样保留）加上对 ``sys.stdout`` / ``sys.stderr`` 的 tee——后者让只用 ``print``
  的脚本（RQ1 生成脚本、第三方 DyFuzz）不改一行代码也能留下同样格式的日志。
- ``*_checkpoint.json`` 是**运行结束时的状态与元数据**：状态（含异常）、起止时间与耗时、
  环境（argv/python/platform/hostname/git_commit）、脱敏后的 ``config.toml`` 快照、
  运行元数据、结果数据（``results``）、事件计数（``event_counts``）、以及**内嵌的全部
  结构化事件** ``events[]``；``log`` 字段指向同 stem 的 ``.log``。

设计要点：

- **唯一文件集**：不再有 run 目录与 ``logs.jsonl`` / ``events.jsonl`` / ``result.json``
  三件套——日志在 ``.log`` 里，其余全在 ``checkpoint.json`` 里，仓库里只有两个文件。
- **checkpoint 带时间戳**：否则 ``batch_run.sh`` 重复 N 次会互相覆盖，违背「公开每一次
  独立运行的结果」；带时间戳也让 log 与 checkpoint 天然同 stem、可直接配对。
- **未启动即 no-op**：``record_event`` / ``record_result`` 在没有活动记录器时直接返回，
  因此库代码（如 ``llm_mutator``）可以无条件调用。
- **线程 + fork 安全**：写文件持锁，``os.register_at_fork(after_in_child=...)`` 在子进程
  里重置锁，避免子进程继承到父进程已持有的锁而死锁。
- **脱敏**：``api_key`` / ``password`` / ``secret`` 等字段写盘前递归替换为 ``***``。
- **原子落盘**：checkpoint 先写临时文件再 ``os.replace``；``finish()`` 幂等。
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
from typing import Any, Self, TextIO

from loguru import logger

from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import PROJECT_DIR, RUNDATA_DIR

SCHEMA_VERSION = 1
#: 未显式给 ``out_dir`` 时的落盘目录（临时/手工运行，不污染 experiments/）
DEFAULT_OUT_DIR = RUNDATA_DIR
#: 与 loguru 默认 stderr 格式一致；``report.py`` 按行首 ``%Y-%m-%d %H:%M:%S.%f`` 解析
LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
CHECKPOINT_SUFFIX = "_checkpoint.json"
LOG_SUFFIX = ".log"

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

_active: "RunRecorder | None" = None


# ---------------------------------------------------------------------------
# json / 时间工具
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
    """把任意名字转成安全的路径片段（保留 ``\\w`` / ``.`` / ``-``）。"""
    cleaned = re.sub(r"[^\w.-]+", "-", str(text), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    if not cleaned or cleaned in {".", ".."}:
        return "run"
    return cleaned


# ---------------------------------------------------------------------------
# 命名契约
# ---------------------------------------------------------------------------


def run_stem(
    rq: str,
    group: str,
    library: str | None = None,
    mode: str | None = None,
    timestamp: str | None = None,
) -> str:
    """构造一次运行的 ``stem``，log 与 checkpoint 共用它。

    ``rq3_respfuzzer_nltk_20261010T090000_mode-full`` 这种形式：
    ``rq`` / ``group`` / ``library`` 小写、``mode`` 追加在时间戳之后。
    """
    stamp = timestamp or _now().strftime("%Y%m%dT%H%M%S")
    parts = [_slug(rq).lower(), _slug(group).lower()]
    if library:
        parts.append(_slug(library).lower())
    parts.append(_slug(stamp))
    if mode and _slug(mode).lower() != "full":
        # Full 是默认消融组，不出现在文件名里（与约定的命名样例一致）
        parts.append(f"mode-{_slug(mode).lower()}")
    return "_".join(parts)


def log_filename(stem: str) -> str:
    return f"{stem}{LOG_SUFFIX}"


def checkpoint_filename(stem: str) -> str:
    return f"{stem}{CHECKPOINT_SUFFIX}"


def _unique_stem(out_dir: Path, stem: str) -> str:
    """同名（同一秒内重复启动）时加 ``-2`` 后缀，避免互相覆盖。"""
    candidate = stem
    suffix = 1
    while (out_dir / log_filename(candidate)).exists() or (
        out_dir / checkpoint_filename(candidate)
    ).exists():
        suffix += 1
        candidate = f"{stem}-{suffix}"
    return candidate


# ---------------------------------------------------------------------------
# stdout / stderr tee
# ---------------------------------------------------------------------------


class _TeeWriter:
    """把 ``print()`` 与 stderr 输出镜像到 ``.log``，同时保持控制台输出不变。

    对只用 ``print`` 的脚本（RQ1 生成脚本、第三方 DyFuzz）无需改代码即可留下日志。
    """

    def __init__(self, original: TextIO, path: Path, owner: "RunRecorder") -> None:
        self._original = original
        self._path = path
        self._owner = owner

    def write(self, text: str) -> int:
        try:
            self._original.write(text)
        except (OSError, ValueError):  # pragma: no cover - 控制台已关闭
            pass
        if text:
            self._owner.append_raw(text)
        return len(text) if isinstance(text, str) else 0

    def flush(self) -> None:
        try:
            self._original.flush()
        except (OSError, ValueError):  # pragma: no cover
            pass

    def isatty(self) -> bool:
        return bool(getattr(self._original, "isatty", lambda: False)())

    def fileno(self) -> int:
        return int(self._original.fileno())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


# ---------------------------------------------------------------------------
# 记录器
# ---------------------------------------------------------------------------


class RunRecorder:
    """一次独立运行的记录器：产出 ``.log`` 与 ``*_checkpoint.json``。

    通常不直接构造，而是通过 :func:`start_run` 启动；用 ``with`` 包住实验主体即可在
    正常结束时写 ``status: completed``，异常结束时写 ``failed``（含异常类型与消息）。
    """

    def __init__(
        self,
        out_dir: Path,
        rq: str,
        group: str,
        library: str | None,
        mode: str | None,
        stem: str,
        *,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        capture_stdout: bool = True,
    ) -> None:
        self.out_dir = out_dir
        self.rq = rq
        self.group = group
        self.library = library
        self.mode = mode
        self.stem = stem
        self.run_id = stem
        self.metadata = _scrub_secrets(_json_safe(dict(metadata or {})))
        self.config = (
            _scrub_secrets(_json_safe(config))
            if config is not None
            else _config_snapshot()
        )
        self.results: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.event_counts: dict[str, int] = {}
        self.log_lines = 0
        self.raw_lines = 0
        self.write_errors = 0
        self.started_at = _now()
        self.finished_at: datetime | None = None
        self._sink_id: int | None = None
        self._tee_streams: list[tuple[str, TextIO, _TeeWriter]] = []
        self._capture_stdout = capture_stdout
        self._closed = False
        self._finishing = False

        out_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = out_dir / log_filename(stem)
        self.checkpoint_path = out_dir / checkpoint_filename(stem)

    # -- 属性 ---------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    # -- 日志 ---------------------------------------------------------------

    def attach(self, level: str = "DEBUG") -> None:
        """挂上 loguru 文本 sink 与 stdout/stderr tee，开始写 ``.log``。"""
        if self._sink_id is not None or self._closed:
            return
        self._sink_id = logger.add(
            self._loguru_sink, level=level, format=LOG_FORMAT, catch=True
        )
        if self._capture_stdout:
            self._attach_tee("stdout")
            self._attach_tee("stderr")

    def _attach_tee(self, name: str) -> None:
        original = getattr(sys, name, None)
        if original is None or isinstance(original, _TeeWriter):
            return
        tee = _TeeWriter(original, self.log_path, self)
        setattr(sys, name, tee)
        self._tee_streams.append((name, original, tee))

    def detach(self) -> None:
        """摘除 sink 与 tee（``finish()`` 内部调用，也可手工调）。"""
        for name, original, tee in self._tee_streams:
            if getattr(sys, name, None) is tee:
                setattr(sys, name, original)
        self._tee_streams.clear()
        sink_id, self._sink_id = self._sink_id, None
        if sink_id is None:
            return
        try:
            logger.remove(sink_id)
        except ValueError:
            # sink 已被外部的 logger.remove() 清掉（实验入口会重设日志）
            logger.debug(f"Log sink {sink_id} of run {self.stem} already removed")

    def _loguru_sink(self, message: str) -> None:
        text = str(message)
        with _io_lock:
            if self._closed:
                return
            if self._append_text(text):
                self.log_lines += 1

    def append_raw(self, text: str) -> None:
        """tee 写入（stdout/stderr 片段），由 :class:`_TeeWriter` 调用。"""
        with _io_lock:
            if self._closed:
                return
            if self._append_text(text):
                self.raw_lines += 1

    def _append_text(self, text: str) -> bool:
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            # 写日志失败（磁盘满等）只计数，绝不让 sink 抛回 loguru
            self.write_errors += 1
            return False
        return True

    # -- 事件与结果 ---------------------------------------------------------

    def record_event(self, event_type: str, **fields: Any) -> None:
        """追加一条结构化事件（收尾后为 no-op）。"""
        if self._closed:
            return
        payload = _json_safe(dict(fields))
        payload.setdefault("ts", _now_iso())
        payload["event"] = str(event_type)
        with _io_lock:
            if self._closed:
                return
            self.events.append(payload)
            key = str(payload["event"])
            self.event_counts[key] = self.event_counts.get(key, 0) + 1

    def record_result(self, key: str, value: Any) -> None:
        """写入一条结果数据（最终进入 checkpoint 的 ``results`` 字段）。"""
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

    def build_checkpoint(self, status: str, error: Any = None) -> dict[str, Any]:
        finished = self.finished_at or _now()
        return {
            "schema_version": SCHEMA_VERSION,
            "rq": self.rq,
            "group": self.group,
            "library": self.library,
            "mode": self.mode,
            "run_id": self.stem,
            "log": log_filename(self.stem),
            "checkpoint": checkpoint_filename(self.stem),
            "status": status,
            "error": _error_payload(error),
            "started_at": self.started_at.isoformat(timespec="milliseconds"),
            "finished_at": finished.isoformat(timespec="milliseconds"),
            "duration_seconds": round((finished - self.started_at).total_seconds(), 3),
            "environment": _environment(),
            "config": self.config,
            "metadata": self.metadata,
            "results": self.results,
            "event_counts": dict(sorted(self.event_counts.items())),
            "totals": {
                "events": len(self.events),
                "log_lines": self.log_lines,
                "raw_lines": self.raw_lines,
                "write_errors": self.write_errors,
            },
            "events": self.events,
        }

    def finish(self, status: str = "completed", error: Any = None) -> Path:
        """收尾：摘 sink/tee，原子写出 ``*_checkpoint.json``。幂等，可重复调用。"""
        global _active
        if error is not None and status == "completed":
            status = "failed"
        with _global_lock:
            if self._closed or self._finishing:
                return self.checkpoint_path
            self._finishing = True
            if _active is self:
                _active = None
        logger.debug(
            f"Run {self.stem} finished with status={status}, "
            f"checkpoint -> {self.checkpoint_path}"
        )
        self.detach()
        self.finished_at = _now()
        payload = self.build_checkpoint(status, error)
        with _io_lock:
            self._closed = True
            tmp_path = self.checkpoint_path.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                handle.write(_dump(payload))
                handle.write("\n")
            os.replace(tmp_path, self.checkpoint_path)
        return self.checkpoint_path

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
            # checkpoint 落盘失败不能吞掉实验本身的异常
            logger.opt(exception=True).error(
                f"Failed to write checkpoint json for run {self.stem}"
            )
        return False


# ---------------------------------------------------------------------------
# 模块级 API
# ---------------------------------------------------------------------------


def start_run(
    rq: str,
    *,
    group: str,
    library: str | None = None,
    mode: str | None = None,
    out_dir: str | os.PathLike[str] | None = None,
    timestamp: str | None = None,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    log_level: str = "DEBUG",
    capture_stdout: bool = True,
) -> RunRecorder:
    """启动一次独立运行的录制，返回记录器（可直接用于 ``with``）。

    Args:
        rq: 实验编号，如 ``RQ1``..``RQ4``；决定文件名前缀与 checkpoint 的 ``rq`` 字段。
        group: 实验组，如 ``respfuzzer`` / ``fuzz4all`` / ``dyfuzz`` / ``sce-only``。
        library: 目标库（如 ``nltk``）；一次运行覆盖多库时可为 ``None``。
        mode: RQ4 的消融模式（``Full`` / ``NL`` / ``NSF`` ...），仅 RQ4 使用。
        out_dir: 落盘目录，通常是 ``experiments/RQ<x>/``；缺省为 ``run_data/``。
        timestamp: 覆盖自动时间戳（复现/测试用）。
        metadata: 运行元数据（数据集路径、随机种子、解码参数……），原样落盘。
        config: 配置快照；缺省时自动快照 ``config.toml``（脱敏后）。
        log_level: loguru sink 级别。
        capture_stdout: 是否把 stdout/stderr tee 进 ``.log``（只用 print 的脚本靠它）。
    """
    global _active
    with _global_lock:
        previous = _active
        _active = None
    if previous is not None and not previous.closed:
        # 同一进程里连续启动两次：先把上一次收尾，避免结果互相覆盖
        previous.finish(status="superseded")

    base = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    stem = _unique_stem(base, run_stem(rq, group, library, mode, timestamp))
    recorder = RunRecorder(
        base,
        rq,
        group,
        library,
        mode,
        stem,
        metadata=metadata,
        config=config,
        capture_stdout=capture_stdout,
    )
    with _global_lock:
        _active = recorder
    recorder.attach(level=log_level)
    logger.debug(f"Recording run {stem} into {base}")
    return recorder


def get_recorder() -> RunRecorder | None:
    """返回当前活动的记录器；没有则返回 ``None``。"""
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


def finish_run(status: str = "completed", error: Any = None) -> Path | None:
    """收尾当前运行并返回 checkpoint 路径；未启动录制时返回 ``None``。"""
    recorder = get_recorder()
    if recorder is None:
        return None
    return recorder.finish(status, error)


@contextmanager
def run_session(name: str, **kwargs: Any) -> Iterator[RunRecorder]:
    """``with run_session("RQ3", group="respfuzzer", ...) as rec:`` 的便捷封装。

    异常结束时以 ``failed`` + 异常信息收尾，异常继续向上抛出。
    """
    recorder = start_run(name, **kwargs)
    try:
        yield recorder
    except BaseException as exc:
        recorder.finish("failed", error=exc)
        raise
    else:
        recorder.finish("completed")
