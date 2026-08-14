from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..puzzles import PuzzleManager
from ..scenario import ScenarioGenerator
from ..validation import SampleValidator


class SudokuDomainAdapter(VariedDomainSupport):
    name = "sudoku"
    tool_catalog = make_tool_catalog(name, {
        "sudoku_candidate_scan": "Candidate digits for every empty cell and a grounded next move.",
        "sudoku_board_validation": "Row, column, box, solvability, and conflict validation.",
        "sudoku_solution_verification": "Solver-backed complete solution verification.",
        "sudoku_rules_reference": "Canonical Sudoku rules and immutable-given guidance.",
        "sudoku_board_summary": "Board dimensions, clue density, difficulty, and strategies.",
        "sudoku_unit_analysis": "Missing digits and candidate coverage for every row, column, and box.",
        "sudoku_naked_single_scan": "Empty cells having exactly one legal candidate.",
        "sudoku_hidden_single_scan": "Digits occurring in only one candidate cell within a unit.",
        "sudoku_locked_candidate_scan": "Pointing and claiming candidates locked to a row or column.",
        "sudoku_move_impact_analysis": "Peer candidate eliminations caused by the suggested move.",
    })

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
        if scenario.edge_case == "malformed_input":
            return ["sudoku_board_validation", "sudoku_board_summary", "sudoku_rules_reference"]
        if scenario.edge_case in {"invalid_board", "unsolvable_board", "ambiguous_board"}:
            return ["sudoku_board_validation", "sudoku_unit_analysis", "sudoku_candidate_scan"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}:
            return ["sudoku_rules_reference", "sudoku_board_summary", "sudoku_unit_analysis"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}:
            return ["sudoku_candidate_scan", "sudoku_hidden_single_scan", "sudoku_locked_candidate_scan", "sudoku_unit_analysis"]
        primary = self._tool_name_for_scenario(scenario)
        if primary == "sudoku_solution_verification":
            return ["sudoku_candidate_scan", "sudoku_naked_single_scan", "sudoku_hidden_single_scan", primary]
        if primary == "sudoku_candidate_scan": return [primary, "sudoku_naked_single_scan", "sudoku_hidden_single_scan", "sudoku_move_impact_analysis"]
        if primary == "sudoku_board_validation": return [primary, "sudoku_unit_analysis", "sudoku_move_impact_analysis"]
        return [primary or "sudoku_candidate_scan", "sudoku_board_validation", "sudoku_unit_analysis"]

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
        candidates = ground_truth.get("candidates", {})
        if tool_name == "sudoku_unit_analysis":
            return {"units": self._unit_analysis(puzzle.puzzle, candidates)}
        if tool_name == "sudoku_naked_single_scan":
            return {"naked_singles": [{"cell": cell, "digit": values[0]} for cell, values in sorted(candidates.items()) if len(values) == 1]}
        if tool_name == "sudoku_hidden_single_scan":
            return {"hidden_singles": self._hidden_singles(candidates)}
        if tool_name == "sudoku_locked_candidate_scan":
            return {"locked_candidates": self._locked_candidates(candidates)}
        if tool_name == "sudoku_move_impact_analysis":
            move = ground_truth.get("suggested_move") or {}
            return {"suggested_move": move, "peer_eliminations": self._move_impact(move, candidates)}
        return {}

    @staticmethod
    def _cell(row: int, col: int) -> str: return f"r{row + 1}c{col + 1}"

    @classmethod
    def _units(cls) -> list[tuple[str, list[str]]]:
        rows = [(f"row_{r + 1}", [cls._cell(r, c) for c in range(9)]) for r in range(9)]
        columns = [(f"column_{c + 1}", [cls._cell(r, c) for r in range(9)]) for c in range(9)]
        boxes = [(f"box_{br + 1}_{bc + 1}", [cls._cell(r, c) for r in range(br * 3, br * 3 + 3) for c in range(bc * 3, bc * 3 + 3)]) for br in range(3) for bc in range(3)]
        return rows + columns + boxes

    @classmethod
    def _unit_analysis(cls, board: str, candidates: dict[str, list[str]]) -> list[dict[str, Any]]:
        values = {cls._cell(i // 9, i % 9): value for i, value in enumerate(board[:81]) if value in "123456789"}
        return [{"unit": name, "missing_digits": sorted(set("123456789") - {values[cell] for cell in cells if cell in values}), "empty_cells": [cell for cell in cells if cell in candidates]} for name, cells in cls._units()]

    @classmethod
    def _hidden_singles(cls, candidates: dict[str, list[str]]) -> list[dict[str, str]]:
        found: dict[tuple[str, str], dict[str, str]] = {}
        for unit, cells in cls._units():
            for digit in "123456789":
                matches = [cell for cell in cells if digit in candidates.get(cell, [])]
                if len(matches) == 1:
                    found[(matches[0], digit)] = {"cell": matches[0], "digit": digit, "unit": unit}
        return list(found.values())

    @classmethod
    def _locked_candidates(cls, candidates: dict[str, list[str]]) -> list[dict[str, Any]]:
        results = []
        for br in range(3):
            for bc in range(3):
                cells = [cls._cell(r, c) for r in range(br * 3, br * 3 + 3) for c in range(bc * 3, bc * 3 + 3)]
                for digit in "123456789":
                    matches = [cell for cell in cells if digit in candidates.get(cell, [])]
                    rows = {cell.split("c")[0] for cell in matches}; columns = {cell.split("c")[1] for cell in matches}
                    if len(matches) > 1 and (len(rows) == 1 or len(columns) == 1):
                        results.append({"box": f"box_{br + 1}_{bc + 1}", "digit": digit, "cells": matches, "locked_to": f"row_{next(iter(rows))[1:]}" if len(rows) == 1 else f"column_{next(iter(columns))}"})
        return results

    @staticmethod
    def _move_impact(move: dict[str, Any], candidates: dict[str, list[str]]) -> list[dict[str, str]]:
        cell, digit = move.get("cell"), str(move.get("value") or move.get("digit") or "")
        if not cell or not digit or "c" not in cell: return []
        row, col = (int(part) - 1 for part in cell[1:].split("c")); impacted = []
        for peer, values in sorted(candidates.items()):
            pr, pc = (int(part) - 1 for part in peer[1:].split("c"))
            if peer != cell and digit in values and (pr == row or pc == col or (pr // 3, pc // 3) == (row // 3, col // 3)):
                impacted.append({"cell": peer, "removed_candidate": digit})
        return impacted

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "sudoku_candidate_scan": "The scenario asks for candidate or next-move guidance.",
            "sudoku_board_validation": "The scenario needs deterministic board validity or conflict checks.",
            "sudoku_solution_verification": "The scenario needs solution-backed verification.",
        }
        return reasons.get(tool_name, f"The {scenario.task_category} scenario requested domain tool support.")
