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


def test_config_loads_custom_chat_settings_from_dotenv(tmp_path, monkeypatch):
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
        (
            "MODEL_PROVIDER=custom_chat\n"
            "CUSTOM_API_URL=http://172.17.99.11:30000/v1/chat/completions\n"
            "CUSTOM_API_KEY=test-custom-key\n"
            "CUSTOM_MODEL=gpt-oss-120b\n"
            "CUSTOM_TEMPERATURE=0.1\n"
            "CUSTOM_MAX_TOKENS=512\n"
            "CUSTOM_REASONING=Low\n"
            "CUSTOM_ENABLE_THINKING=false\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("CUSTOM_API_URL", raising=False)
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.delenv("CUSTOM_MODEL", raising=False)
    monkeypatch.delenv("CUSTOM_TEMPERATURE", raising=False)
    monkeypatch.delenv("CUSTOM_MAX_TOKENS", raising=False)
    monkeypatch.delenv("CUSTOM_REASONING", raising=False)
    monkeypatch.delenv("CUSTOM_ENABLE_THINKING", raising=False)
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "DEFAULTS_FILE", config_dir / "defaults.yaml")
    monkeypatch.setattr(config_module, "PROMPTS_FILE", config_dir / "prompts.yaml")

    config = config_module.get_config()

    assert config["model"]["provider"] == "custom_chat"
    assert config["model"]["endpoint"] == "http://172.17.99.11:30000/v1/chat/completions"
    assert config["model"]["api_key"] == "test-custom-key"
    assert config["model"]["model_name"] == "gpt-oss-120b"
    assert config["model"]["temperature"] == 0.1
    assert config["model"]["max_tokens"] == 512
    assert config["model"]["reasoning"] == "Low"
    assert config["model"]["enable_thinking"] is False
