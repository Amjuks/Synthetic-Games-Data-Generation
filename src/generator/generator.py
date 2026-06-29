from __future__ import annotations

import json
import random
import traceback
from pathlib import Path
from typing import Any

from .config import get_config
from .exporters import append_csv_row
from .jobs import JobManager
from .model_client import ModelClient


class ConversationGenerator:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or get_config()
        self.prompts = self.config.get("prompts", {})
        self.model_client = ModelClient(self.config.get("model", {}))

    def generate_single_turn(self, sample_index: int) -> dict[str, Any]:
        category = self._choose_category()
        board = self._get_board_for_category(category, sample_index)
        prompt = self._build_single_turn_prompt(sample_index, category, board)
        raw_output = self.model_client.generate(prompt)
        return self._parse_output(raw_output, sample_index, "single_turn", category, board)

    def generate_multi_turn(self, sample_index: int) -> dict[str, Any]:
        category = self._choose_category()
        board = self._get_board_for_category(category, sample_index)
        prompt = self._build_multi_turn_prompt(sample_index, category, board)
        raw_output = self.model_client.generate(prompt)
        return self._parse_output(raw_output, sample_index, "multi_turn", category, board)

    def _build_single_turn_prompt(self, sample_index: int, category: str, board: str) -> str:
        board_section = self._build_board_section(board)
        return (
            f"{self.prompts.get('system_prompt', '')}\n\n"
            f"{self.prompts.get('single_turn_prompt', '')}\n"
            f"Category: {category}\n"
            f"Sample index: {sample_index}\n"
            f"{board_section}"
            "Return minified JSON with keys: prompt, response, conversation_type, category, board.\n"
            "Use conversation_type='single_turn'.\n"
            "If a board is provided, preserve it exactly in the board field and ground the prompt and response in that board."
        )

    def _build_multi_turn_prompt(self, sample_index: int, category: str, board: str) -> str:
        max_turns = self.config.get("max_turns", 6)
        board_section = self._build_board_section(board)
        return (
            f"{self.prompts.get('system_prompt', '')}\n\n"
            f"{self.prompts.get('multi_turn_prompt', '').format(max_turns=max_turns)}\n"
            f"Category: {category}\n"
            f"Sample index: {sample_index}\n"
            f"{board_section}"
            "Return minified JSON with keys: messages, conversation_type, category, board.\n"
            "Use conversation_type='multi_turn'.\n"
            "messages must be a list of objects, each with keys: user, response.\n"
            "If a board is provided, preserve it exactly in the board field and make the exchange refer to that board."
        )

    def _parse_output(
        self,
        raw_output: str,
        sample_index: int,
        conversation_type: str,
        category: str,
        board: str,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError:
            payload = {
                "conversation_type": conversation_type,
                "category": category,
                "prompt": raw_output,
                "response": raw_output,
                "messages": [{"user": raw_output, "response": raw_output}],
                "board": board,
            }

        if conversation_type == "multi_turn":
            payload = {
                "conversation_type": "multi_turn",
                "category": payload.get("category", category),
                "messages": self._normalize_messages(payload),
                "board": payload.get("board", board),
                "sample_index": sample_index,
            }
        else:
            payload = {
                "conversation_type": "single_turn",
                "category": payload.get("category", category),
                "prompt": payload.get("prompt", payload.get("user", "")),
                "response": payload.get("response", payload.get("assistant", "")),
                "board": payload.get("board", board),
                "sample_index": sample_index,
            }
        return payload

    def _choose_category(self) -> str:
        categories = self.prompts.get("conversation_categories", [])
        return random.choice(categories) if categories else "general_chat"

    def _get_board_for_category(self, category: str, sample_index: int) -> str:
        if not self._category_needs_board(category):
            return ""
        template = BOARD_TEMPLATES[sample_index % len(BOARD_TEMPLATES)]
        return self._format_board(template)

    def _category_needs_board(self, category: str) -> bool:
        return category in {
            "next_best_move",
            "validity_check",
            "solve_puzzle",
            "solve_row_column_box",
            "hint",
            "mistake_correction",
            "advanced_question",
        }

    def _build_board_section(self, board: str) -> str:
        if not board:
            return ""
        return f"Sudoku board:\n{board}\n"

    def _normalize_messages(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        messages = payload.get("messages")
        if isinstance(messages, list):
            normalized = [self._normalize_message_pair(item) for item in messages]
            normalized = [item for item in normalized if item["user"] or item["response"]]
            if normalized:
                return normalized

        turns = payload.get("turns")
        if isinstance(turns, list):
            normalized_turns: list[dict[str, str]] = []
            pending_user = ""
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                role = str(turn.get("role", "")).lower()
                content = str(turn.get("content", ""))
                if role == "user":
                    if pending_user:
                        normalized_turns.append({"user": pending_user, "response": ""})
                    pending_user = content
                elif role in {"assistant", "response"}:
                    normalized_turns.append({"user": pending_user, "response": content})
                    pending_user = ""
            if pending_user:
                normalized_turns.append({"user": pending_user, "response": ""})
            if normalized_turns:
                return normalized_turns

        prompt = payload.get("prompt", payload.get("user", ""))
        response = payload.get("response", payload.get("assistant", ""))
        return [{"user": str(prompt), "response": str(response)}]

    def _normalize_message_pair(self, item: Any) -> dict[str, str]:
        if not isinstance(item, dict):
            return {"user": str(item), "response": ""}
        if "user" in item or "response" in item:
            return {
                "user": str(item.get("user", "")),
                "response": str(item.get("response", "")),
            }
        return {
            "user": str(item.get("prompt", item.get("assistant", "" if "role" in item else ""))),
            "response": str(item.get("response", item.get("content", "" if "role" in item else ""))),
        }

    def _format_board(self, rows: list[str]) -> str:
        formatted_rows: list[str] = []
        for row_index, row in enumerate(rows):
            values = [value if value != "0" else "." for value in row]
            formatted_rows.append(
                f"{' '.join(values[0:3])} | {' '.join(values[3:6])} | {' '.join(values[6:9])}"
            )
            if row_index in {2, 5}:
                formatted_rows.append("------+-------+------")
        return "\n".join(formatted_rows)

    def run(self, samples: int, conversation_type: str = "both", max_turns: int = 6, job_name: str | None = None) -> dict[str, Any]:
        job_manager = JobManager(job_name=job_name, config=self.config)
        status = job_manager.get_status()
        target_total = self._resolve_target_total(status, samples)
        self._validate_resume_config(status, conversation_type, max_turns)

        resume_state = job_manager.get_resume_state()
        completed = resume_state["completed"]
        single_turn_count = resume_state["single_turn_count"]
        multi_turn_count = resume_state["multi_turn_count"]
        start_index = resume_state["next_sample_index"]
        single_turn_path = job_manager.job_dir / "single_turn.csv"
        multi_turn_path = job_manager.job_dir / "multi_turn.csv"

        job_manager.save_status(
            status="running",
            total=target_total,
            completed=completed,
            current_stage="generating",
            conversation_type=conversation_type,
            max_turns=max_turns,
            next_sample_index=start_index,
            single_turn_count=single_turn_count,
            multi_turn_count=multi_turn_count,
            single_turn_path=str(single_turn_path),
            multi_turn_path=str(multi_turn_path),
            last_error=None,
        )

        try:
            for sample_index in range(start_index, target_total):
                if conversation_type in ("both", "single_turn"):
                    row = self.generate_single_turn(sample_index)
                    append_csv_row(single_turn_path, row)
                    single_turn_count += 1
                if conversation_type in ("both", "multi_turn"):
                    row = self.generate_multi_turn(sample_index)
                    append_csv_row(multi_turn_path, row)
                    multi_turn_count += 1

                completed = sample_index + 1
                job_manager.save_status(
                    completed=completed,
                    next_sample_index=completed,
                    single_turn_count=single_turn_count,
                    multi_turn_count=multi_turn_count,
                    status="running",
                    current_stage="generating",
                )
        except KeyboardInterrupt:
            job_manager.save_status(
                status="stopped",
                current_stage="interrupted",
                completed=completed,
                next_sample_index=completed,
                single_turn_count=single_turn_count,
                multi_turn_count=multi_turn_count,
                last_error="Generation stopped by user.",
            )
            raise
        except Exception as exc:
            job_manager.save_status(
                status="failed",
                current_stage="error",
                completed=completed,
                next_sample_index=completed,
                single_turn_count=single_turn_count,
                multi_turn_count=multi_turn_count,
                last_error=f"{type(exc).__name__}: {exc}",
                last_error_traceback=traceback.format_exc(),
            )
            raise

        job_manager.save_status(
            status="completed",
            total=target_total,
            completed=completed,
            next_sample_index=completed,
            current_stage="finished",
            single_turn_count=single_turn_count,
            multi_turn_count=multi_turn_count,
            last_error=None,
        )
        return {
            "job_name": job_manager.job_name,
            "job_dir": str(job_manager.job_dir),
            "completed": completed,
            "total": target_total,
            "single_turn_count": single_turn_count,
            "multi_turn_count": multi_turn_count,
        }

    def _resolve_target_total(self, status: dict[str, Any], requested_samples: int) -> int:
        existing_total = int(status.get("total", 0) or 0)
        if existing_total > requested_samples:
            return existing_total
        return requested_samples

    def _validate_resume_config(self, status: dict[str, Any], conversation_type: str, max_turns: int) -> None:
        existing_conversation_type = status.get("conversation_type")
        if existing_conversation_type and existing_conversation_type != conversation_type:
            raise ValueError(
                f"Job '{status.get('job_name')}' was started with conversation_type="
                f"'{existing_conversation_type}', not '{conversation_type}'."
            )

        existing_max_turns = status.get("max_turns")
        if existing_max_turns is not None and int(existing_max_turns) != int(max_turns):
            raise ValueError(
                f"Job '{status.get('job_name')}' was started with max_turns={existing_max_turns}, not {max_turns}."
            )


BOARD_TEMPLATES = [
    [
        "530070000",
        "600195000",
        "098000060",
        "800060003",
        "400803001",
        "700020006",
        "060000280",
        "000419005",
        "000080079",
    ],
    [
        "003020600",
        "900305001",
        "001806400",
        "008102900",
        "700000008",
        "006708200",
        "002609500",
        "800203009",
        "005010300",
    ],
    [
        "200080300",
        "060070084",
        "030500209",
        "000105408",
        "000000000",
        "402706000",
        "301007040",
        "720040060",
        "004010003",
    ],
]
