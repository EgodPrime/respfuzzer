"""框架统一日志系统（loguru）——唯一 setup 入口。

取代 ``experiment_recorder`` 的日志职责（实验入口各自 ``logger.remove()/add()``
散落各处、互不知晓的问题）。本模块只做两件事：

1. **统一 setup**：入口脚本（CLI、实验 bash 驱动的 python 入口）调用一次
   :func:`setup_logging`，重设 loguru sink（控制台 + 可选日志文件）；
2. **自定义日志名**：模块或调用方用 :func:`get_logger` 取 logger 并可指定
   记录名（显示于日志格式的 ``{name}`` 字段），例如 ``get_logger("RQ1")``。

用法::

    # 入口（每次运行一次）
    from respfuzzer.utils.logging import setup_logging
    setup_logging(level="INFO", log_file="experiments/RQ1/rq1_normal_numpy_20261021T.log")

    # 模块内
    from respfuzzer.utils.logging import get_logger
    logger = get_logger(__name__)       # 默认：模块全名
    logger = get_logger("RQ1")          # 调用方自定义名

约定：

- **幂等**：``setup_logging`` 先 ``logger.remove()`` 清掉所有既有 sink（含 loguru
  默认 stderr sink）再挂载，重复调用即重设为同一套 sink；
- **文件名由调用方决定**：本模块不发明命名契约，``log_file`` 路径与文件名由
  实验入口/脚本自己拼（时间戳、实验组等信息入口自己最清楚）；
- 线程安全由 loguru sink 自身保证；子进程各进程独立调用 setup 即可。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger as _logger

#: 统一日志格式：行首 ``%Y-%m-%d %H:%M:%S.%f`` 时间戳，供报表脚本按行首解析
LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
)


def setup_logging(
    *,
    level: str = "INFO",
    log_file: str | Path | None = None,
    file_level: str = "DEBUG",
    **sink_kwargs: Any,
) -> None:
    """统一日志设置：清空既有 sink，挂控制台 sink；给 ``log_file`` 时追加文件 sink。

    Args:
        level: 控制台 sink 级别（默认 ``INFO``）。
        log_file: 日志文件路径（父目录自动创建）；``None`` 只输出控制台。
        file_level: 文件 sink 级别（默认 ``DEBUG``——文件留全量细节，控制台只看主干）。
        **sink_kwargs: 透传给 ``logger.add`` 的其余参数（如 ``backtrace``、``enqueue``）。
    """
    _logger.remove()
    _logger.add(sys.stderr, level=level.upper(), format=LOG_FORMAT, **sink_kwargs)
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        _logger.add(path, level=file_level.upper(), format=LOG_FORMAT, **sink_kwargs)


def get_logger(name: str | None = None):
    """取全局 loguru logger；给 ``name`` 时把记录名替换为它（显示于 ``{name}``）。

    loguru 是进程级单例，无法像 stdlib logging 一样按名分实例；这里用
    ``logger.patch`` 在记录上替换 ``name`` 字段，达到「自定义日志名」的效果，
    sink、级别等仍由 :func:`setup_logging` 统一管理。
    """
    if not name:
        return _logger
    return _logger.patch(lambda record: record.update(name=name))
