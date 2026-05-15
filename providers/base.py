"""Abstract LLM client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """Base class for provider errors."""


class LLMRateLimitError(LLMError):
    """Raised when the provider returns a rate limit or quota error."""

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code  # provider-specific error code, e.g. "insufficient_quota"


class LLMAPIError(LLMError):
    """Raised for general API errors (auth failures, server errors, etc.)."""


class LLMClient(ABC):
    """
    Thin interface over an LLM provider's chat completion endpoint.

    All pipeline stages call chat() — one method, provider-agnostic.
    Concrete implementations translate provider SDK types and exceptions
    into the common interface.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier, e.g. 'openai' or 'anthropic'."""
        ...

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        """
        Send a chat request and return the assistant's text response.

        messages: standard OpenAI-style list of {"role": ..., "content": ...} dicts.
        json_mode: hint to the provider that the response should be valid JSON.
                   Implementations use the best available mechanism per provider.
        """
        ...
