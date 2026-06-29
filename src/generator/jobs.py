from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_config


class JobManager:
    def __init__(self, job_name: str | None = None, config: dict[str, Any] | None = None):
        config = config or get_config()
        self.base_output_dir = Path(config["output_path"])
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.job_name = job_name or f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}"
        self.job_dir = self.base_output_dir / self.job_name
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.progress_path = self.job_dir / "progress.json"
        self.status = self._load_status()

    def _load_status(self) -> dict[str, Any]:
        if self.progress_path.exists():
            with self.progress_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "job_name": self.job_name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "initialized",
            "completed": 0,
            "total": 0,
            "current_stage": "idle",
            "single_turn_count": 0,
            "multi_turn_count": 0,
            "next_sample_index": 0,
            "last_error": None,
        }

    def save_status(self, **kwargs: Any) -> None:
        status = self._load_status()
        status.update(kwargs)
        status["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.progress_path.open("w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, sort_keys=True)
        self.status = status

    def get_status(self) -> dict[str, Any]:
        self.status = self._load_status()
        return self.status

    def get_resume_state(self) -> dict[str, Any]:
        status = self.get_status()
        return {
            "completed": int(status.get("completed", 0) or 0),
            "single_turn_count": int(status.get("single_turn_count", 0) or 0),
            "multi_turn_count": int(status.get("multi_turn_count", 0) or 0),
            "next_sample_index": int(status.get("next_sample_index", status.get("completed", 0)) or 0),
            "status": status.get("status", "initialized"),
            "total": int(status.get("total", 0) or 0),
        }
