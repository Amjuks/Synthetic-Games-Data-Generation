from __future__ import annotations

from typing import Any

from ..models import PuzzleRecord, Scenario, ValidationResult
from ..puzzles import PuzzleManager
from ..scenario import ScenarioGenerator
from ..validation import SampleValidator


class SudokuDomainAdapter:
    name = "sudoku"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.scenario_generator = ScenarioGenerator(config)
        self.puzzle_manager = PuzzleManager(config)
        self.validator = SampleValidator()

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
            return {
                "used": False,
                "tool_name": None,
                "tool_input": None,
                "tool_output": None,
                "reason": None,
            }

        tool_input = {
            "puzzle_id": puzzle.puzzle_id,
            "task_category": scenario.task_category,
            "edge_case": scenario.edge_case,
            "board": puzzle.rendered_board,
        }
        return {
            "used": True,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": self._run_tool(tool_name, puzzle),
            "reason": self._tool_reason(tool_name, scenario),
        }

    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain": self.name,
            "ground_truth": puzzle.ground_truth,
            "tool_usage": tool_usage,
        }

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        row["domain"] = sample.get("domain", self.name)
        row["tool_used"] = sample.get("tool_used", False)
        row["tool_name"] = sample.get("tool_usage_details", {}).get("tool_name")
        return row

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
        return {}

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "sudoku_candidate_scan": "The scenario asks for candidate or next-move guidance.",
            "sudoku_board_validation": "The scenario needs deterministic board validity or conflict checks.",
            "sudoku_solution_verification": "The scenario needs solution-backed verification.",
        }
        return reasons.get(tool_name, f"The {scenario.task_category} scenario requested domain tool support.")
