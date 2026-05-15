"""
LLM provider factory.

Usage:
    from providers import get_client, LLMClient

    client = get_client(cost_config)                        # top-level provider
    client = get_client(cost_config, stage="llm_evaluation") # per-stage override
"""

from __future__ import annotations

import os

from providers.base import LLMAPIError, LLMClient, LLMError, LLMRateLimitError

_PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _resolve_provider(config: dict, stage: str | None) -> str:
    """Return the provider name for this call, resolving stage overrides."""
    if stage:
        stage_provider = config.get(stage, {}).get("provider")
        if stage_provider:
            return stage_provider
    return config.get("provider", "openai")


def _build_client(provider: str) -> LLMClient:
    if provider == "openai":
        from providers.openai_client import OpenAIClient
        return OpenAIClient()
    if provider == "anthropic":
        from providers.anthropic_client import AnthropicClient
        return AnthropicClient()
    raise ValueError(
        f"Unknown provider {provider!r}. Valid options: openai, anthropic."
    )


def get_client(config: dict, *, stage: str | None = None) -> LLMClient:
    """
    Return an LLMClient for the given cost config.

    If stage is provided (e.g. "llm_evaluation"), the stage's `provider:`
    field takes precedence over the top-level `provider:` field.
    Falls back to 'openai' if neither is set.
    """
    provider = _resolve_provider(config, stage)
    return _build_client(provider)


def validate_provider(config: dict, *, stage: str | None = None) -> None:
    """
    Raise AuthError if the API key for the resolved provider is not set.

    Import and call this at the start of a command that will make LLM calls.
    """
    from commands.auth import AuthError

    provider = _resolve_provider(config, stage)
    env_var = _PROVIDER_ENV_VARS.get(provider)
    if env_var and not os.environ.get(env_var):
        raise AuthError(
            f"No API key found for provider '{provider}'. Set {env_var}."
        )


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMRateLimitError",
    "LLMAPIError",
    "get_client",
    "validate_provider",
]
