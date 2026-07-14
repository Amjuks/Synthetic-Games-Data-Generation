from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .models import utc_now_iso


class GenerationEventLogger:
    """Append-only structured and readable logs for one generation job."""

    def __init__(self, job_dir: Path, config: dict[str, Any]):
        storage = config.get("storage", {})
        self.events_path = job_dir / storage.get("events_filename", "generation_events.jsonl")
        self.text_path = job_dir / storage.get("log_filename", "generation.log")

    def log(self, event: str, *, level: str = "INFO", **details: Any) -> dict[str, Any]:
        record = {
            "event_id": uuid.uuid4().hex[:16],
            "timestamp": utc_now_iso(),
            "level": level,
            "event": event,
            **details,
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        with self.text_path.open("a", encoding="utf-8") as handle:
            handle.write(self._format_text(record))
        return record

    def _format_text(self, record: dict[str, Any]) -> str:
        header = f"[{record['timestamp']}] {record['level']} {record['event']} ({record['event_id']})"
        lines = [header]
        for key, value in record.items():
            if key in {"timestamp", "level", "event", "event_id"}:
                continue
            if key == "traceback":
                lines.extend(["traceback:", str(value).rstrip()])
            elif isinstance(value, (dict, list)):
                lines.extend([f"{key}:", json.dumps(value, ensure_ascii=False, indent=2, default=str)])
            else:
                lines.append(f"{key}: {value}")
        lines.append("")
        return "\n".join(lines) + "\n"
