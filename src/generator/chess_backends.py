from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import chess
import chess.engine
import requests


OFFICIAL_STOCKFISH_RELEASE_API = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"


class ChessBackendManager:
    """Discovers or provisions a verified official Stockfish UCI executable."""

    def __init__(self, config: dict[str, Any]):
        chess_config = config.get("chess", config)
        output_path = Path(config.get("output_path", "outputs"))
        configured_cache = chess_config.get("engine_cache_dir")
        self.cache_dir = Path(configured_cache) if configured_cache else output_path / ".chess_backends" / "stockfish"
        self.configured_path = os.getenv("CHESS_ENGINE_PATH") or chess_config.get("engine_path")
        self.auto_install = bool(chess_config.get("engine_auto_install", True))
        self.release_api = str(chess_config.get("engine_release_api", OFFICIAL_STOCKFISH_RELEASE_API))
        self.timeout = float(chess_config.get("tool_timeout", 30))
        self.user_agent = "synthetic-chess-dataset/0.1"
        self._resolved: dict[str, Any] | None = None

    def ensure_engine(self) -> dict[str, Any]:
        if self._resolved and self._resolved.get("available"):
            return dict(self._resolved)
        discovered = self._discover_engine()
        if discovered:
            self._resolved = self._verify_engine(discovered, source="configured_or_cached")
            if self._resolved.get("available"):
                return dict(self._resolved)
        if not self.auto_install:
            self._resolved = {"available": False, "verified": False, "reason": "No working UCI engine was found and automatic installation is disabled."}
            return dict(self._resolved)
        try:
            self._resolved = self._install_official_stockfish()
        except (OSError, ValueError, requests.RequestException, zipfile.BadZipFile, tarfile.TarError) as exc:
            self._resolved = {"available": False, "verified": False, "reason": f"Stockfish provisioning failed: {exc}"}
        return dict(self._resolved)

    def healthcheck(self) -> dict[str, Any]:
        resolved = self.ensure_engine()
        if not resolved.get("available"):
            return resolved
        path = resolved["engine_path"]
        try:
            with chess.engine.SimpleEngine.popen_uci(path, timeout=self.timeout) as engine:
                result = engine.play(chess.Board(), chess.engine.Limit(depth=1))
                return {**resolved, "verified": result.move in chess.Board().legal_moves, "probe_move_uci": result.move.uci()}
        except (OSError, TimeoutError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
            return {**resolved, "available": False, "verified": False, "reason": f"UCI healthcheck failed: {exc}"}

    def _discover_engine(self) -> Path | None:
        if self.configured_path and str(self.configured_path).lower() != "auto":
            path = Path(self.configured_path).expanduser()
            if path.is_file():
                return path.resolve()
        on_path = shutil.which("stockfish")
        if on_path:
            return Path(on_path).resolve()
        if self.cache_dir.is_dir():
            suffix = ".exe" if os.name == "nt" else ""
            candidates = sorted(
                path for path in self.cache_dir.glob(f"stockfish*{suffix}")
                if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))
            )
            if candidates:
                return candidates[-1].resolve()
        return None

    def _install_official_stockfish(self) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        release_response = requests.get(self.release_api, headers={"User-Agent": self.user_agent}, timeout=self.timeout)
        release_response.raise_for_status()
        release = release_response.json()
        asset_name = self._asset_name()
        asset = next((item for item in release.get("assets", []) if item.get("name") == asset_name), None)
        if not asset:
            raise ValueError(f"Official release {release.get('tag_name')} has no supported asset named {asset_name}.")
        expected_digest = str(asset.get("digest") or "")
        if not expected_digest.startswith("sha256:"):
            raise ValueError("Official release metadata did not provide a SHA-256 digest; refusing an unverified engine download.")
        expected_sha256 = expected_digest.split(":", 1)[1].lower()
        download_url = asset.get("browser_download_url")
        if not download_url:
            raise ValueError("Official release asset has no download URL.")

        with tempfile.TemporaryDirectory(prefix="stockfish-install-", dir=self.cache_dir) as temp_name:
            temp_dir = Path(temp_name)
            archive_path = temp_dir / asset_name
            digest = hashlib.sha256()
            with requests.get(download_url, headers={"User-Agent": self.user_agent}, timeout=self.timeout, stream=True) as response:
                response.raise_for_status()
                with archive_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            digest.update(chunk)
                            handle.write(chunk)
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError(f"Stockfish SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}.")
            extracted = temp_dir / "extracted"
            extracted.mkdir()
            self._safe_extract(archive_path, extracted)
            executable = self._find_executable(extracted)
            tag = str(release.get("tag_name") or "latest").replace("/", "-")
            destination = self.cache_dir / f"stockfish-{tag}{'.exe' if os.name == 'nt' else ''}"
            shutil.copy2(executable, destination)
            if os.name != "nt":
                destination.chmod(destination.stat().st_mode | 0o111)

        verification = self._verify_engine(destination, source="official_stockfish_release")
        if not verification.get("available"):
            destination.unlink(missing_ok=True)
            return verification
        manifest = {
            "release": release.get("tag_name"),
            "asset": asset_name,
            "download_url": download_url,
            "sha256": expected_sha256,
            "executable_sha256": self._sha256_file(destination),
            "engine_path": str(destination.resolve()),
            "engine_name": verification.get("engine_name"),
        }
        (self.cache_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return {**verification, "release": release.get("tag_name"), "sha256": expected_sha256, "manifest_path": str(self.cache_dir / "manifest.json")}

    def _verify_engine(self, path: Path, *, source: str) -> dict[str, Any]:
        integrity = self._verify_cached_integrity(path)
        if integrity is not None:
            return integrity
        try:
            with chess.engine.SimpleEngine.popen_uci(str(path), timeout=self.timeout) as engine:
                engine_name = str(engine.id.get("name", ""))
                if "stockfish" not in engine_name.lower():
                    return {"available": False, "verified": False, "reason": f"UCI executable identified itself as {engine_name!r}, not Stockfish."}
                result = engine.play(chess.Board(), chess.engine.Limit(depth=1))
                if result.move not in chess.Board().legal_moves:
                    return {"available": False, "verified": False, "reason": "UCI engine returned an illegal healthcheck move."}
        except (OSError, TimeoutError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
            return {"available": False, "verified": False, "reason": f"UCI verification failed: {exc}"}
        return {"available": True, "verified": True, "engine_path": str(path.resolve()), "engine_name": engine_name, "source": source}

    def _verify_cached_integrity(self, path: Path) -> dict[str, Any] | None:
        try:
            is_cached = path.resolve().parent == self.cache_dir.resolve()
        except OSError:
            is_cached = False
        manifest_path = self.cache_dir / "manifest.json"
        if not is_cached or not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"available": False, "verified": False, "reason": f"Stockfish cache manifest is unreadable: {exc}"}
        expected = manifest.get("executable_sha256")
        if not expected:
            recorded_path = manifest.get("engine_path")
            if recorded_path and Path(recorded_path).resolve() == path.resolve() and manifest.get("sha256"):
                manifest["executable_sha256"] = self._sha256_file(path)
                manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            return None
        actual = self._sha256_file(path)
        if actual != expected:
            return {"available": False, "verified": False, "reason": f"Cached Stockfish executable SHA-256 mismatch: expected {expected}, got {actual}."}
        return None

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _asset_name() -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()
        is_arm = machine in {"arm64", "aarch64"}
        if system == "windows":
            return "stockfish-windows-armv8.zip" if is_arm else "stockfish-windows-x86-64.zip"
        if system == "linux":
            if is_arm:
                return "stockfish-ubuntu-armv8.tar"
            return "stockfish-ubuntu-x86-64.tar"
        if system == "darwin":
            return "stockfish-macos-m1-apple-silicon.tar" if is_arm else "stockfish-macos-x86-64.tar"
        raise ValueError(f"Automatic Stockfish installation is not supported on {platform.system()} {platform.machine()}.")

    @staticmethod
    def _safe_extract(archive_path: Path, destination: Path) -> None:
        destination_resolved = destination.resolve()
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    target = (destination / member.filename).resolve()
                    if destination_resolved not in target.parents and target != destination_resolved:
                        raise ValueError(f"Unsafe archive member: {member.filename}")
                archive.extractall(destination)
            return
        with tarfile.open(archive_path) as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise ValueError(f"Refusing archive link member: {member.name}")
                target = (destination / member.name).resolve()
                if destination_resolved not in target.parents and target != destination_resolved:
                    raise ValueError(f"Unsafe archive member: {member.name}")
            archive.extractall(destination)

    @staticmethod
    def _find_executable(directory: Path) -> Path:
        candidates = [path for path in directory.rglob("stockfish*") if path.is_file()]
        if os.name == "nt":
            candidates = [path for path in candidates if path.suffix.lower() == ".exe"]
        else:
            candidates = [path for path in candidates if "." not in path.name or os.access(path, os.X_OK)]
        if not candidates:
            raise ValueError("Downloaded Stockfish archive did not contain a usable executable.")
        return sorted(candidates, key=lambda path: (len(path.parts), path.name))[0]
