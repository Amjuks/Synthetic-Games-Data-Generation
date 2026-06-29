from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .exporters import append_csv_row


class DatasetStorage:
    def __init__(self, job_dir: Path, config: dict[str, Any]):
        storage_config = config.get("storage", {})
        self.job_dir = job_dir
        self.metadata_path = job_dir / storage_config.get("metadata_filename", "samples.jsonl")
        self.rejected_path = job_dir / storage_config.get("rejected_filename", "rejected_samples.jsonl")
        self.stats_path = job_dir / storage_config.get("stats_filename", "dataset_stats.json")
        self.single_turn_path = job_dir / "single_turn.csv"
        self.multi_turn_path = job_dir / "multi_turn.csv"
        self._history = self._load_jsonl(self.metadata_path)
        self._rejected_history = self._load_jsonl(self.rejected_path)
        self._stats = self._load_stats()

    def append_sample(self, sample: dict[str, Any]) -> None:
        self._append_jsonl(self.metadata_path, sample)
        output = sample.get("output", {})
        row = self._flatten_sample_row(sample)
        if output.get("conversation_type") == "multi_turn":
            append_csv_row(self.multi_turn_path, row)
        else:
            append_csv_row(self.single_turn_path, row)
        self._history.append(sample)
        self._update_stats(sample)
        self._save_stats()

    def append_rejected_sample(self, rejected_record: dict[str, Any]) -> None:
        self._append_jsonl(self.rejected_path, rejected_record)
        self._rejected_history.append(rejected_record)

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def get_rejected_count(self) -> int:
        return len(self._rejected_history)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    def _flatten_sample_row(self, sample: dict[str, Any]) -> dict[str, Any]:
        output = sample.get("output", {})
        row = {
            "sample_id": sample.get("sample_id"),
            "scenario_id": sample.get("scenario_id"),
            "puzzle_id": sample.get("puzzle_id"),
            "parent_puzzle_id": sample.get("parent_puzzle_id"),
            "task": sample.get("task"),
            "difficulty": sample.get("difficulty"),
            "turns": sample.get("turns"),
            "similarity_score": sample.get("similarity_score"),
            "generation_model": sample.get("generation_model"),
            "timestamp": sample.get("timestamp"),
            "validation_status": sample.get("validation_status"),
            "edge_case": sample.get("edge_case"),
            "user_expertise": sample.get("user_expertise"),
            "assistant_style": sample.get("assistant_style"),
            "tool_usage": sample.get("tool_usage"),
            "board": output.get("board", ""),
        }
        if output.get("conversation_type") == "multi_turn":
            row["conversation_type"] = "multi_turn"
            row["messages"] = output.get("messages", [])
        else:
            row["conversation_type"] = "single_turn"
            row["prompt"] = output.get("prompt", "")
            row["response"] = output.get("response", "")
        return row

    def _update_stats(self, sample: dict[str, Any]) -> None:
        scenario = sample.get("scenario", {})
        self._increment_nested("task_distribution", scenario.get("task_category", "unknown"))
        self._increment_nested("difficulty_distribution", scenario.get("difficulty", "unknown"))
        self._increment_nested("user_expertise_distribution", scenario.get("user_expertise", "unknown"))
        self._increment_nested("user_personality_distribution", scenario.get("user_personality", "unknown"))
        self._increment_nested("assistant_style_distribution", scenario.get("assistant_style", "unknown"))
        self._increment_nested("tone_distribution", scenario.get("tone", "unknown"))
        self._increment_nested("edge_case_distribution", scenario.get("edge_case", "unknown"))
        self._increment_nested("tool_usage_distribution", scenario.get("tool_usage", "unknown"))
        self._increment_nested("puzzle_reuse_distribution", sample.get("parent_puzzle_id") or sample.get("puzzle_id", "unknown"))
        self._increment_nested("conversation_type_distribution", sample.get("conversation_type", "unknown"))
        self._increment_nested("conversation_length_distribution", str(sample.get("turns", 0)))
        self._stats["accepted_samples"] = int(self._stats.get("accepted_samples", 0)) + 1

    def _increment_nested(self, key: str, nested_key: str) -> None:
        distribution = self._stats.setdefault(key, {})
        distribution[nested_key] = int(distribution.get(nested_key, 0)) + 1

    def _load_stats(self) -> dict[str, Any]:
        if not self.stats_path.exists():
            return {"accepted_samples": 0}
        return json.loads(self.stats_path.read_text(encoding="utf-8"))

    def _save_stats(self) -> None:
        self.stats_path.write_text(json.dumps(self._stats, indent=2, sort_keys=True), encoding="utf-8")

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
