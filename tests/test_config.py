from pathlib import Path

from src.generator import config as config_module
from src.generator.config import get_config


def test_config_loads_defaults():
    config = get_config()
    assert "prompts" in config
    assert config["samples"] == 10
    assert config["conversation_type"] == "both"
    assert config["output_dir"] == "outputs"


def test_config_loads_openai_settings_from_dotenv(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "defaults.yaml").write_text(
        (
            "defaults:\n"
            "  samples: 2\n"
            "  conversation_type: both\n"
            "  max_turns: 4\n"
            "  output_dir: outputs\n"
            "  model:\n"
            "    provider: openai\n"
            "    model_name: gpt-4o-mini\n"
            "    temperature: 0.8\n"
            "    max_tokens: 800\n"
        ),
        encoding="utf-8",
    )
    (config_dir / "prompts.yaml").write_text("system_prompt: test\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=test-key\nOPENAI_MODEL=test-model\nOPENAI_TEMPERATURE=0.3\nOPENAI_MAX_TOKENS=250\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_MAX_TOKENS", raising=False)
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "DEFAULTS_FILE", config_dir / "defaults.yaml")
    monkeypatch.setattr(config_module, "PROMPTS_FILE", config_dir / "prompts.yaml")

    config = config_module.get_config()

    assert config["model"]["api_key"] == "test-key"
    assert config["model"]["model_name"] == "test-model"
    assert config["model"]["temperature"] == 0.3
    assert config["model"]["max_tokens"] == 250
