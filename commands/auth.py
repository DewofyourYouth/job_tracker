"""OpenAI API key lookup helpers."""
import os


ENV_VAR = "OPENAI_API_KEY"


class AuthError(RuntimeError):
    """Raised when an API key cannot be retrieved."""


def get_openai_api_key() -> str:
    """Return the OpenAI API key from the environment."""

    key = os.getenv(ENV_VAR)
    if key:
        return key

    raise AuthError(f"No OpenAI API key found. Set {ENV_VAR}.")


def has_openai_api_key() -> bool:
    """Return whether an API key is available from the environment."""

    return bool(os.getenv(ENV_VAR))
