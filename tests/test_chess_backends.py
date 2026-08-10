import hashlib
import json

from src.generator.chess_backends import ChessBackendManager
from src.generator.config import get_config, resolve_domain_config


def test_default_chess_config_auto_provisions_and_requires_verification():
    config = resolve_domain_config(get_config(), "chess")

    assert config["chess"]["engine_path"] == "auto"
    assert config["chess"]["engine_auto_install"] is True
    assert config["chess"]["tablebase_path"] == "auto"
    assert config["chess"]["tablebase_online"] is True
    assert config["chess"]["verification_backends_required"] is True


def test_cached_engine_integrity_rejects_tampering(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    executable = cache / "stockfish-test.exe"
    executable.write_bytes(b"not really stockfish")
    manifest = {
        "engine_path": str(executable.resolve()),
        "sha256": "archive-digest",
        "executable_sha256": hashlib.sha256(b"original contents").hexdigest(),
    }
    (cache / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager = ChessBackendManager({
        "output_path": str(tmp_path),
        "chess": {"engine_cache_dir": str(cache), "engine_auto_install": False},
    })

    result = manager._verify_engine(executable, source="configured_or_cached")

    assert result["available"] is False
    assert result["verified"] is False
    assert "SHA-256 mismatch" in result["reason"]
