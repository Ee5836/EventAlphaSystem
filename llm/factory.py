"""LLM provider factory."""
from llm.base import AbstractLLMProvider


def get_llm(config: dict = None) -> AbstractLLMProvider:
    """Get LLM provider instance based on configuration.

    Args:
        config: Application config dict. If None, loads from Flask current_app.

    Returns:
        Configured LLM provider instance.
    """
    if config is None:
        try:
            from flask import current_app
            config = current_app.config
        except RuntimeError:
            import os
            config = {
                "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "deepseek"),
                "LLM_API_KEY": os.environ.get("LLM_API_KEY", ""),
                "LLM_API_BASE": os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1"),
                "LLM_MODEL": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
                "LLM_TEMPERATURE": float(os.environ.get("LLM_TEMPERATURE", "0.1")),
                "LLM_MAX_TOKENS": int(os.environ.get("LLM_MAX_TOKENS", "4096")),
                "LLM_TIMEOUT": float(os.environ.get("LLM_TIMEOUT", "30.0")),
                "LLM_RETRY_COUNT": int(os.environ.get("LLM_RETRY_COUNT", "3")),
            }

    provider_name = config.get("LLM_PROVIDER", "deepseek").lower()

    if provider_name == "deepseek":
        from llm.deepseek import DeepSeekProvider
        return DeepSeekProvider(config)
    elif provider_name == "ollama":
        from llm.ollama import OllamaProvider
        return OllamaProvider(config)
    elif provider_name == "qwen":
        from llm.qwen import QwenProvider
        return QwenProvider(config)
    elif provider_name == "zhipu":
        from llm.zhipu import ZhipuProvider
        return ZhipuProvider(config)
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}. "
                         f"Supported: deepseek, ollama, qwen, zhipu")
