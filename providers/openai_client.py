"""OpenAI chat completion provider."""

from __future__ import annotations
from typing import Any

from providers.base import LLMAPIError, LLMClient, LLMRateLimitError


def _error_code(exc) -> str | None:
    """Extract the string error code from an openai RateLimitError if present."""
    try:
        body = exc.body or {}
        return (body.get("error") or {}).get("code")
    except Exception:
        return None


class OpenAIClient(LLMClient):
    """LLMClient backed by the OpenAI API."""

    @property
    def provider_name(self) -> str:
        return "openai"

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        from openai import APIError, OpenAI, RateLimitError

        client = OpenAI()
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                **kwargs,
            )
        except RateLimitError as exc:
            raise LLMRateLimitError(str(exc), code=_error_code(exc)) from exc
        except APIError as exc:
            raise LLMAPIError(str(exc)) from exc

        return response.choices[0].message.content or ""
