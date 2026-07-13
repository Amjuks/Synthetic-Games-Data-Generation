from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DEFAULTS_FILE = CONFIG_DIR / "defaults.yaml"
PROMPTS_FILE = CONFIG_DIR / "prompts.yaml"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_config() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    defaults = load_yaml(DEFAULTS_FILE)
    prompts = load_yaml(PROMPTS_FILE)
    config = deepcopy(defaults.get("defaults", {}))
    prompt_profiles = prompts.get("domains", {})
    shared_prompts = {key: value for key, value in prompts.items() if key != "domains"}
    domain_profiles = deepcopy(config.get("domains", {}))
    domain_profiles.setdefault("sudoku", {})["prompts"] = deepcopy(
        prompt_profiles.get("sudoku", shared_prompts)
    )
    for domain, domain_prompts in prompt_profiles.items():
        domain_profiles.setdefault(domain, {})["prompts"] = deepcopy(domain_prompts)
    config["domains"] = domain_profiles
    model_config = config.get("model", {}).copy()
    provider = get_env_or_default("MODEL_PROVIDER", model_config.get("provider", "openai"))
    model_config["provider"] = provider

    if provider == "custom_chat":
        model_config["api_key"] = get_env_or_default("CUSTOM_API_KEY", model_config.get("api_key"))
        model_config["endpoint"] = get_env_or_default("CUSTOM_API_URL", model_config.get("endpoint"))
        model_config["model_name"] = get_env_or_default("CUSTOM_MODEL", model_config.get("model_name", "gpt-oss-120b"))
        model_config["temperature"] = _get_float_env("CUSTOM_TEMPERATURE", model_config.get("temperature", 0.1))
        model_config["max_tokens"] = _get_int_env("CUSTOM_MAX_TOKENS", model_config.get("max_tokens", 800))
        model_config["timeout"] = _get_float_env("CUSTOM_TIMEOUT", model_config.get("timeout", 60))
        model_config["reasoning"] = get_env_or_default("CUSTOM_REASONING", model_config.get("reasoning", "Low"))
        model_config["enable_thinking"] = _get_bool_env(
            "CUSTOM_ENABLE_THINKING",
            model_config.get("enable_thinking", False),
        )
    elif provider == "tensorstudio":
        model_config["api_key"] = get_env_or_default("TENSORSTUDIO_API_KEY", model_config.get("api_key"))
        model_config["endpoint"] = get_env_or_default(
            "TENSORSTUDIO_API_URL",
            model_config.get("endpoint", "https://api.tensorstudio.ai/v1/chat/completions"),
        )
        model_config["model_name"] = get_env_or_default(
            "TENSORSTUDIO_MODEL",
            model_config.get("model_name", "glm-5.2-fp8"),
        )
        model_config["temperature"] = _get_float_env(
            "TENSORSTUDIO_TEMPERATURE",
            model_config.get("temperature", 0.8),
        )
        model_config["max_tokens"] = _get_int_env("TENSORSTUDIO_MAX_TOKENS", model_config.get("max_tokens", 800))
        model_config["timeout"] = _get_float_env("TENSORSTUDIO_TIMEOUT", model_config.get("timeout", 300))
        model_config["session_id"] = get_env_or_default("TENSORSTUDIO_SESSION_ID", model_config.get("session_id"))
    else:
        model_config["api_key"] = get_env_or_default("OPENAI_API_KEY", model_config.get("api_key"))
        model_config["base_url"] = get_env_or_default("OPENAI_BASE_URL", model_config.get("base_url"))
        model_config["model_name"] = get_env_or_default("OPENAI_MODEL", model_config.get("model_name", "gpt-4o-mini"))
        model_config["temperature"] = _get_float_env("OPENAI_TEMPERATURE", model_config.get("temperature", 0.8))
        model_config["max_tokens"] = _get_int_env("OPENAI_MAX_TOKENS", model_config.get("max_tokens", 800))
        model_config["timeout"] = _get_float_env("OPENAI_TIMEOUT", model_config.get("timeout", 60))
    config["model"] = model_config
    config["prompts"] = shared_prompts
    config["root_dir"] = str(ROOT)
    config["output_dir"] = config.get("output_dir", "outputs")
    config["output_path"] = str(ROOT / config["output_dir"])
    return config


def resolve_domain_config(config: dict[str, Any], domain: str) -> dict[str, Any]:
    """Return shared configuration overlaid with the selected domain profile."""
    resolved = deepcopy(config)
    profile = resolved.get("domains", {}).get(domain, {})
    return _deep_merge(resolved, profile)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def get_env_or_default(name: str, default: Any) -> Any:
    return os.getenv(name, default)


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
