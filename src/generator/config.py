from __future__ import annotations

import os
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
    config = defaults.get("defaults", {}).copy()
    model_config = config.get("model", {}).copy()
    model_config["provider"] = get_env_or_default("MODEL_PROVIDER", model_config.get("provider", "openai"))
    model_config["api_key"] = get_env_or_default("OPENAI_API_KEY", model_config.get("api_key"))
    model_config["base_url"] = get_env_or_default("OPENAI_BASE_URL", model_config.get("base_url"))
    model_config["model_name"] = get_env_or_default("OPENAI_MODEL", model_config.get("model_name", "gpt-4o-mini"))
    model_config["temperature"] = _get_float_env("OPENAI_TEMPERATURE", model_config.get("temperature", 0.8))
    model_config["max_tokens"] = _get_int_env("OPENAI_MAX_TOKENS", model_config.get("max_tokens", 800))
    config["model"] = model_config
    config["prompts"] = prompts
    config["root_dir"] = str(ROOT)
    config["output_dir"] = config.get("output_dir", "outputs")
    config["output_path"] = str(ROOT / config["output_dir"])
    return config


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
