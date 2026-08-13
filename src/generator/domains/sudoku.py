from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..puzzles import PuzzleManager
from ..scenario import ScenarioGenerator
from ..validation import SampleValidator


class SudokuDomainAdapter(VariedDomainSupport):
    name = "sudoku"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = ScenarioGenerator(config)
        self.puzzle_manager = PuzzleManager(config)
        self.validator = SampleValidator()
        self._initialize_variety_support()

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
        return self._select_varied_problem(scenario, sample_index)

    def mark_problem_used(self, puzzle: PuzzleRecord) -> None:
        self.puzzle_manager.mark_used(puzzle)

    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        return build_tool_usage(
            scenario=scenario,
            puzzle=puzzle,
            tool_names=self._tool_bundle(scenario),
            input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "board": puzzle.rendered_board},
            output_for=lambda name: self._run_tool(name, puzzle),
        )

    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return compact_tool_context(self.name, puzzle, tool_usage)

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        return {
            "puzzle_id": puzzle.puzzle_id,
            "puzzle": puzzle.puzzle,
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "metadata": puzzle.metadata,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        return puzzle.ground_truth

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Sudoku puzzle exactly and never invent or modify the board unless the "
            "scenario explicitly requires malformed input."
        )

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        row["domain"] = sample.get("domain", self.name)
        row["tool_used"] = sample.get("tool_used", False)
        row["tool_name"] = sample.get("tool_usage_details", {}).get("tool_name")
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}:
            return ["sudoku_rules_reference", "sudoku_board_summary"]
        primary = self._tool_name_for_scenario(scenario)
        if primary == "sudoku_solution_verification":
            return ["sudoku_candidate_scan", primary]
        if primary == "sudoku_candidate_scan": return [primary, "sudoku_board_validation"]
        if primary == "sudoku_board_validation": return [primary, "sudoku_candidate_scan"]
        return [primary or "sudoku_candidate_scan", "sudoku_board_validation"]

    def _tool_name_for_scenario(self, scenario: Scenario) -> str | None:
        if scenario.tool_usage == "candidate_scan" or scenario.task_category in {"hint", "next_best_move"}:
            return "sudoku_candidate_scan"
        if scenario.tool_usage == "row_column_box_check" or scenario.task_category in {"validity_check", "solve_row_column_box"}:
            return "sudoku_board_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category in {"solve_puzzle", "mistake_correction"}:
            return "sudoku_solution_verification"
        if scenario.edge_case in {"invalid_board", "unsolvable_board", "ambiguous_board"}:
            return "sudoku_board_validation"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        ground_truth = puzzle.ground_truth
        if tool_name == "sudoku_candidate_scan":
            return {
                "candidates": ground_truth.get("candidates", {}),
                "suggested_move": ground_truth.get("suggested_move"),
            }
        if tool_name == "sudoku_board_validation":
            return {
                "validity_status": ground_truth.get("validity_status"),
                "solvability_status": ground_truth.get("solvability_status"),
                "conflicts": ground_truth.get("conflicts", []),
            }
        if tool_name == "sudoku_solution_verification":
            return {
                "solution": ground_truth.get("solution"),
                "solved_board": ground_truth.get("solved_board"),
                "unique_solution_status": ground_truth.get("unique_solution_status"),
            }
        if tool_name == "sudoku_rules_reference":
            return {"rules": ["Place 1–9 once in every row, column, and 3x3 box.", "Given cells cannot be changed."]}
        if tool_name == "sudoku_board_summary":
            return {"given_count": puzzle.num_clues, "empty_count": 81 - puzzle.num_clues, "difficulty": puzzle.difficulty, "strategies": puzzle.required_strategies}
        return {}

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "sudoku_candidate_scan": "The scenario asks for candidate or next-move guidance.",
            "sudoku_board_validation": "The scenario needs deterministic board validity or conflict checks.",
            "sudoku_solution_verification": "The scenario needs solution-backed verification.",
        }
        return reasons.get(tool_name, f"The {scenario.task_category} scenario requested domain tool support.")
