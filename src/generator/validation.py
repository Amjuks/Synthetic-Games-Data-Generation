from __future__ import annotations

from typing import Any

from .models import PuzzleRecord, Scenario, ValidationResult


class SampleValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors: list[str] = []

        if output.get("conversation_type") != scenario.conversation_type:
            errors.append("conversation_type does not match scenario")

        if output.get("category") != scenario.task_category:
            errors.append("category does not match scenario task_category")

        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip():
                errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip():
                errors.append("single-turn response is empty")
        else:
            messages = output.get("messages", [])
            if not isinstance(messages, list) or not messages:
                errors.append("multi-turn messages list is empty")
            for index, message in enumerate(messages):
                if not str(message.get("user", "")).strip():
                    errors.append(f"message {index} is missing user text")
                if not str(message.get("response", "")).strip():
                    errors.append(f"message {index} is missing response text")

        board = output.get("board", "")
        if scenario.edge_case == "malformed_input":
            if not board:
                errors.append("malformed_input scenario requires a malformed board string")
        else:
            if board != puzzle.rendered_board:
                errors.append("output board does not match selected puzzle")

        if scenario.edge_case == "none" and not puzzle.unique_solution_status:
            errors.append("standard scenario received non-unique puzzle")

        return ValidationResult(is_valid=not errors, errors=errors)
