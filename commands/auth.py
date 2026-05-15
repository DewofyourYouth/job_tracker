"""API key lookup helpers."""
import os


class AuthError(RuntimeError):
    """Raised when a required API key cannot be found in the environment."""


_PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def get_api_key(provider: str = "openai") -> str:
    """Return the API key for the given provider, or raise AuthError."""
    env_var = _PROVIDER_ENV_VARS.get(provider)
    if not env_var:
        raise AuthError(f"Unknown provider '{provider}'.")
    key = os.getenv(env_var)
    if key:
        return key
    raise AuthError(f"No API key found for provider '{provider}'. Set {env_var}.")


def has_api_key(provider: str = "openai") -> bool:
    """Return whether an API key is available for the given provider."""
    env_var = _PROVIDER_ENV_VARS.get(provider)
    return bool(env_var and os.environ.get(env_var))


# Backwards-compatible aliases used by existing callers.
def get_openai_api_key() -> str:
    return get_api_key("openai")


def has_openai_api_key() -> bool:
    return has_api_key("openai")
