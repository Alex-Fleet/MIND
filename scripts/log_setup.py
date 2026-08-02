#!/usr/bin/env python3
"""
MIND 统一日志：所有脚本/进程的日志统一落盘 data/logs/mind.log。

用法：在脚本 main() 里调用 setup_logger()（幂等，重复调用无害）。
之后该进程内 loguru 与标准库 logging 的输出都进 mind.log：
  带级别 + 时间戳，超 1MB 自动轮转，保留最近 5 份。

默认 file-only：不写 stderr——保护 hook 的 stdout/stderr 协议输出
（inject/summarize/digest 的 JSON、"MIND ready." 等）。

降级：loguru 未安装时 setup_logger() 静默跳过，logger 变空操作——
  绝不让"日志"拖垮 ingest/summarize 等核心脚本或 SessionStart/Stop hook。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths  # noqa: E402

try:
    from loguru import logger as _loguru_logger
    _HAS_LOGURU = True
except ImportError:  # loguru 未装：日志降级为静默，不影响任何脚本运行
    _loguru_logger = None
    _HAS_LOGURU = False

_configured = False


class _NullLogger:
    """loguru 缺失时的空操作 logger：logger.info(...) 永远安全。"""

    def __getattr__(self, name):
        return lambda *a, **k: None


logger = _loguru_logger if _HAS_LOGURU else _NullLogger()


class _InterceptHandler(logging.Handler):
    """把标准库 logging 的日志转接到 loguru（原样保留级别）。"""

    def emit(self, record: logging.LogRecord) -> None:
        if not _HAS_LOGURU:
            return
        try:
            level = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logger(also_stderr: bool = False) -> None:
    """配置统一日志。默认只写文件；also_stderr=True 时额外输出到 stderr。"""
    global _configured
    if _configured or not _HAS_LOGURU:
        return
    logs_dir = get_paths()["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)

    _loguru_logger.remove()  # 去掉 loguru 默认的 stderr sink
    _loguru_logger.add(
        str(logs_dir / "mind.log"),
        level="INFO",
        rotation="1 MB",
        retention=5,
        encoding="utf-8",
        enqueue=True,
    )
    if also_stderr:
        _loguru_logger.add(sys.stderr, level="WARNING")

    # 标准库 logging → loguru 转接：store/llm_utils 等仍用 logging 的调用自动落盘
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    _configured = True
