"""LLM 服务模块 — 基于 LangChain ChatOpenAI"""

import os
from langchain_openai import ChatOpenAI
from .logger import logger

# 全局 LLM 实例
_llm_instance = None
_fallback_llm_instance = None


def _env_flag(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，兼容常见的 true/false 写法。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _create_llm(api_key: str, base_url: str, model: str, thinking_env: str) -> ChatOpenAI:
    """按统一策略创建主模型或备用模型。"""
    llm_options = {}
    if "api.deepseek.com" in base_url.lower():
        thinking_enabled = _env_flag(thinking_env, default=False)
        llm_options["extra_body"] = {
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"}
        }

    try:
        timeout = float(os.getenv("LLM_TIMEOUT", "120"))
    except ValueError:
        timeout = 120
    try:
        sdk_retries = max(0, int(os.getenv("LLM_SDK_MAX_RETRIES", "1")))
    except ValueError:
        sdk_retries = 1

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0.7,
        timeout=timeout,
        max_retries=sdk_retries,
        **llm_options,
    )


def get_llm() -> ChatOpenAI:
    """
    获取 LLM 实例（单例模式）。

    优先从环境变量读取配置：
      - LLM_API_KEY / OPENAI_API_KEY
      - LLM_BASE_URL / OPENAI_BASE_URL
      - LLM_MODEL_ID / OPENAI_MODEL

    Returns:
        ChatOpenAI 实例
    """
    global _llm_instance

    if _llm_instance is None:
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.getenv("LLM_MODEL_ID") or os.getenv("OPENAI_MODEL", "gpt-4")

        if not api_key:
            logger.warning("LLM_API_KEY / OPENAI_API_KEY 未设置，LLM 功能可能无法使用")

        thinking_mode = "provider-default"
        # DeepSeek V4 默认开启 thinking，会让本项目这种结构化 JSON 任务产生
        # 大量不必要的隐藏推理。行程可靠性由候选打分、时间约束和 Critic
        # 校验共同保证，因此默认关闭；需要对比质量时可通过环境变量重新开启。
        if "api.deepseek.com" in base_url.lower():
            thinking_enabled = _env_flag("LLM_THINKING_ENABLED", default=False)
            thinking_mode = "enabled" if thinking_enabled else "disabled"

        _llm_instance = _create_llm(
            api_key, base_url, model, "LLM_THINKING_ENABLED"
        )

        logger.info(
            f"LLM 初始化完成 | model={model} | base_url={base_url} "
            f"| thinking={thinking_mode}"
        )

    return _llm_instance


def get_fallback_llm() -> ChatOpenAI | None:
    """读取可选备用模型；未完整配置时保持禁用。"""
    global _fallback_llm_instance
    api_key = os.getenv("LLM_FALLBACK_API_KEY", "").strip()
    base_url = os.getenv("LLM_FALLBACK_BASE_URL", "").strip()
    model = os.getenv("LLM_FALLBACK_MODEL_ID", "").strip()
    if not (api_key and base_url and model):
        return None
    if _fallback_llm_instance is None:
        _fallback_llm_instance = _create_llm(
            api_key, base_url, model, "LLM_FALLBACK_THINKING_ENABLED"
        )
        logger.info(f"备用 LLM 初始化完成 | model={model} | base_url={base_url}")
    return _fallback_llm_instance


def reset_llm():
    """重置 LLM 实例（用于测试或重新配置）"""
    global _llm_instance, _fallback_llm_instance
    _llm_instance = None
    _fallback_llm_instance = None
    logger.info("LLM 实例已重置")
