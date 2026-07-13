from __future__ import annotations

from typing import Any

from ..kenken import KenKenPuzzleManager, parse_puzzle, validate_structure
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class KenKenScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intent_map = {
            "next_best_move": "ask_for_next_deduction",
            "validity_check": "verify_value_or_cage",
            "rules_explanation": "learn_kenken_rules",
            "solve_puzzle": "request_solution_guidance",
            "cage_analysis": "analyze_cage_combinations",
            "hint": "ask_for_hint",
            "mistake_correction": "debug_constraint_mistake",
            "technique_discussion": "discuss_kenken_strategy",
            "beginner_question": "learn_kenken_basics",
            "advanced_question": "analyze_constraint_interaction",
            "general_chat": "general_kenken_help",
        }
        base = intent_map.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = [
            "Use the supplied KenKen cage layout exactly unless malformed input is explicitly required.",
            "Ground every claim in cage arithmetic and row/column uniqueness.",
        ]
        if task_category == "hint":
            constraints.append("Prefer a deduction or constrained cage over revealing the full solution.")
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's KenKen assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed puzzle representation before helping.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class KenKenValidator:
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
            else:
                for index, message in enumerate(messages):
                    if not str(message.get("user", "")).strip():
                        errors.append(f"message {index} is missing user text")
                    if not str(message.get("response", "")).strip():
                        errors.append(f"message {index} is missing response text")
        board = output.get("board", "")
        if scenario.edge_case == "malformed_input":
            if not board:
                errors.append("malformed_input scenario requires a malformed puzzle string")
        elif board != puzzle.rendered_board:
            errors.append("output board does not match selected puzzle")

        size, cages = parse_puzzle(puzzle.puzzle)
        violations = validate_structure(size, cages)
        truth = puzzle.ground_truth
        if scenario.edge_case == "none":
            if violations:
                errors.append("standard scenario has structurally invalid cages")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique KenKen puzzle")
        if truth.get("solution") != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_cages" and not violations:
            errors.append("invalid_cages scenario has no structural violation")
        if scenario.edge_case == "unsolvable_puzzle" and truth.get("solvability_status") != "unsolvable":
            errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if scenario.edge_case == "ambiguous_puzzle" and truth.get("solvability_status") != "ambiguous":
            errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class KenKenDomainAdapter:
    name = "kenken"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = KenKenScenarioGenerator(config)
        self.puzzle_manager = KenKenPuzzleManager(config)
        self.validator = KenKenValidator()

    def generate_scenario(
        self,
        *,
        sample_index: int,
        conversation_type: str,
        max_turns: int,
        distribution_stats: dict[str, Any],
    ) -> Scenario:
        return self.scenario_generator.generate(
            sample_index=sample_index,
            conversation_type=conversation_type,
            max_turns=max_turns,
            distribution_stats=distribution_stats,
        )

    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        return self.puzzle_manager.select_puzzle(scenario, sample_index)

    def mark_problem_used(self, puzzle: PuzzleRecord) -> None:
        self.puzzle_manager.mark_used(puzzle)

    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        tool_name = self._tool_name_for_scenario(scenario)
        if tool_name is None:
            return {"used": False, "tool_name": None, "tool_input": None, "tool_output": None, "reason": None}
        return {
            "used": True,
            "tool_name": tool_name,
            "tool_input": {
                "puzzle_id": puzzle.puzzle_id,
                "task_category": scenario.task_category,
                "edge_case": scenario.edge_case,
                "size": puzzle.metadata.get("size"),
                "cages": puzzle.metadata.get("cages", []),
            },
            "tool_output": self._run_tool(tool_name, puzzle),
            "reason": self._tool_reason(tool_name, scenario),
        }

    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain": self.name,
            "size": puzzle.metadata.get("size"),
            "cages": puzzle.metadata.get("cages", []),
            "ground_truth": puzzle.ground_truth,
            "tool_usage": tool_usage,
        }

    def generation_guidance(self) -> str:
        return (
            "Use the supplied KenKen size and cage layout exactly. Base all arithmetic on the given cage "
            "targets and operations, and never modify the representation unless malformed input is required."
        )

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {})
        row["domain"] = sample.get("domain", self.name)
        row["grid_size"] = metadata.get("size")
        row["cages"] = metadata.get("cages", [])
        row["tool_used"] = sample.get("tool_used", False)
        row["tool_name"] = sample.get("tool_usage_details", {}).get("tool_name")
        return row

    def _tool_name_for_scenario(self, scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_cages", "unsolvable_puzzle", "ambiguous_puzzle"}:
            return "kenken_constraint_validation"
        if scenario.tool_usage == "cage_candidate_scan" or scenario.task_category in {"hint", "next_best_move", "cage_analysis"}:
            return "kenken_cage_analysis"
        if scenario.tool_usage == "cage_constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}:
            return "kenken_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "kenken_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        if tool_name == "kenken_cage_analysis":
            return {
                "cage_candidate_tuples": truth.get("cage_candidate_tuples", {}),
                "forced_values": truth.get("forced_values", {}),
                "suggested_move": truth.get("suggested_move"),
            }
        if tool_name == "kenken_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "cage_evaluations": truth.get("cage_evaluations", []),
            }
        if tool_name == "kenken_solution_verification":
            return {
                "solution": truth.get("solution"),
                "solution_grid": truth.get("solution_grid"),
                "unique_solution_status": truth.get("unique_solution_status"),
            }
        return {}

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "kenken_cage_analysis": "The scenario needs cage combinations or a constrained next deduction.",
            "kenken_constraint_validation": "The scenario needs deterministic cage and Latin-square checks.",
            "kenken_solution_verification": "The scenario needs solver-backed solution verification.",
        }
        return reasons.get(tool_name, f"The {scenario.task_category} scenario requested KenKen tool support.")
