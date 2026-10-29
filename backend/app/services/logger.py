"""结构化日志模块 — 基于 loguru"""

import sys
import io
import time
from pathlib import Path
from loguru import logger

# ---- Windows 控制台编码修复 ----
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 移除默认 handler
logger.remove()

# 控制台输出 — 彩色、结构化
logger.add(
    sys.stderr,
    format=(
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
    level="DEBUG",
    colorize=True,
)

# 文件日志 — 按天轮转
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger.add(
    LOG_DIR / "trip_agent_{time:YYYY-MM-DD}.log",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    ),
    level="DEBUG",
    rotation="00:00",      # 每天午夜轮转
    retention="30 days",   # 保留 30 天
    encoding="utf-8",
    enqueue=True,           # 多进程安全
)

# 性能计时器
_timers: dict[str, float] = {}


def log_step_start(step_name: str) -> str:
    """记录步骤开始，返回 step_id 用于 log_step_end"""
    step_id = f"{step_name}_{time.time()}"
    _timers[step_id] = time.time()
    logger.info(f"▶ STEP START | {step_name}")
    return step_id


def log_step_end(step_id: str, extra: str = ""):
    """记录步骤结束，输出耗时"""
    start = _timers.pop(step_id, None)
    if start:
        elapsed = time.time() - start
        step_name = step_id.rsplit("_", 1)[0]
        suffix = f" | {extra}" if extra else ""
        logger.info(f"✓ STEP END   | {step_name} | ⏱ {elapsed:.2f}s{suffix}")
    else:
        logger.warning(f"⚠ STEP END   | step_id not found: {step_id}")


def log_agent_call(agent_name: str, query_preview: str = ""):
    """记录 Agent LLM 调用"""
    preview = query_preview[:80] + "..." if len(query_preview) > 80 else query_preview
    logger.debug(f"🤖 AGENT CALL | {agent_name} | {preview}")


def log_agent_result(agent_name: str, elapsed: float, result_len: int):
    """记录 Agent 返回结果"""
    logger.debug(f"✅ AGENT DONE | {agent_name} | ⏱ {elapsed:.2f}s | {result_len} chars")


def log_tool_call(tool_name: str, args: dict):
    """记录工具调用"""
    logger.debug(f"🔧 TOOL CALL | {tool_name} | {args}")


__all__ = [
    "logger",
    "log_step_start",
    "log_step_end",
    "log_agent_call",
    "log_agent_result",
    "log_tool_call",
]
