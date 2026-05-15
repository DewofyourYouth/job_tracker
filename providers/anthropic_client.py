"""Anthropic (Claude) chat completion provider."""

from __future__ import annotations

from providers.base import LLMAPIError, LLMClient, LLMRateLimitError


class AnthropicClient(LLMClient):
    """
    LLMClient backed by the Anthropic Messages API.

    System messages are extracted from the messages list and passed as the
    top-level `system` parameter (required by the Anthropic SDK).

    json_mode uses the assistant-turn prefill trick: an initial `{` is
    prepended to the assistant turn, and the provider's response is rejoined
    so callers receive a complete JSON string.
    """

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is not installed. Run: pip install anthropic"
            ) from exc

        system_parts: list[str] = []
        user_messages: list[dict[str, str]] = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                user_messages.append(m)

        system = "\n\n".join(system_parts)

        if json_mode:
            # Prefill assistant turn with `{` to constrain output to JSON.
            user_messages = [*user_messages, {"role": "assistant", "content": "{"}]

        create_kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": user_messages}
        if system:
            create_kwargs["system"] = system

        client = anthropic.Anthropic()
        try:
            response = client.messages.create(**create_kwargs)
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except anthropic.APIError as exc:
            raise LLMAPIError(str(exc)) from exc

        content = response.content[0].text if response.content else ""
        if json_mode:
            content = "{" + content  # restore the prefilled character
        return content
