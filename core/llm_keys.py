"""
core/llm_keys.py — resolves LLM API keys with a clear precedence:
environment variable first, config/llm_config.yaml second, unset third.

Not called by anything in v0 yet (no LLM-invoking feature exists). This
exists so that when a real feature starts calling out, it has one place
to ask "what's my key and where did it come from" rather than reaching
into os.environ or the yaml file directly and duplicating the precedence
logic at every call site.
"""
import os

import yaml

_ENV_VAR_NAMES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
}


def resolve(key_name, llm_config_path):
    """
    key_name: one of "anthropic_api_key", "openai_api_key", "gemini_api_key"
    Returns (value, source) where source is "env", "file", or "unset".
    Env var always wins over the plaintext file — an exported shell
    variable is strictly more secure than a value sitting in a repo-local
    file, so it should never be silently overridden by the file.
    """
    env_var = _ENV_VAR_NAMES.get(key_name)
    if env_var:
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            return env_value, "env"

    try:
        with open(llm_config_path) as f:
            file_config = yaml.safe_load(f) or {}
        file_value = str(file_config.get(key_name, "")).strip()
        if file_value:
            return file_value, "file"
    except FileNotFoundError:
        pass

    return "", "unset"


def resolve_all(llm_config_path):
    """Returns {key_name: (value, source)} for every known key."""
    return {k: resolve(k, llm_config_path) for k in _ENV_VAR_NAMES}
