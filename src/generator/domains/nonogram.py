from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..nonogram import NonogramPuzzleManager, line_patterns, parse_puzzle, solve_nonogram, validate_structure
from ..scenario import ScenarioGenerator


class NonogramScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {"next_best_move": "ask_for_next_line_deduction", "validity_check": "verify_nonogram_solution", "rules_explanation": "learn_nonogram_rules", "solve_puzzle": "request_solution_guidance", "clue_analysis": "analyze_line_clues", "hint": "ask_for_nonogram_hint", "mistake_correction": "debug_run_separation_mistake", "technique_discussion": "discuss_nonogram_strategy", "beginner_question": "learn_nonogram_basics", "advanced_question": "analyze_cross_line_constraints", "general_chat": "general_nonogram_help"}
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = ["Use the supplied Nonogram row and column clues exactly unless malformed input is explicitly required.", "Ground every claim in consecutive filled runs and row/column cross-references."]
        if task_category == "hint":
            constraints.append("Prefer one line deduction over revealing the completed picture.")
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Nonogram assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed clues before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class NonogramValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors: list[str] = []
        if output.get("conversation_type") != scenario.conversation_type:
            errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category:
            errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip(): errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip(): errors.append("single-turn response is empty")
        elif not isinstance(output.get("messages"), list) or not output["messages"]:
            errors.append("multi-turn messages list is empty")
        elif any(not str(message.get("user", "")).strip() or not str(message.get("response", "")).strip() for message in output["messages"]):
            errors.append("a multi-turn message is missing user text or response text")
        if scenario.edge_case == "malformed_input":
            if not output.get("board"): errors.append("malformed_input scenario requires a malformed puzzle string")
        elif output.get("board") != puzzle.rendered_board:
            errors.append("output board does not match selected puzzle")
        height, width, rows, columns = parse_puzzle(puzzle.puzzle)
        structural = validate_structure(height, width, rows, columns)
        truth = puzzle.ground_truth
        if scenario.edge_case == "none" and (structural or truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status):
            errors.append("standard scenario requires a structurally valid unique Nonogram")
        if truth.get("solution") != puzzle.solution: errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_clues" and not structural: errors.append("invalid_clues scenario has no structural violation")
        if scenario.edge_case == "unsolvable_puzzle" and truth.get("solvability_status") != "unsolvable": errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if scenario.edge_case == "ambiguous_puzzle" and truth.get("solvability_status") != "ambiguous": errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class NonogramDomainAdapter(VariedDomainSupport):
    name = "nonogram"
    tool_catalog = make_tool_catalog(name, {
        "nonogram_line_analysis": "Forced filled and empty cells across all lines.", "nonogram_constraint_validation": "Clue structure and solver status validation.",
        "nonogram_solution_verification": "Complete solver-backed grid verification.", "nonogram_rules_reference": "Canonical run and separation rules.",
        "nonogram_puzzle_summary": "Dimensions and clue counts.", "nonogram_row_pattern_analysis": "Legal pattern counts and examples for each row.",
        "nonogram_column_pattern_analysis": "Legal pattern counts and examples for each column.", "nonogram_overlap_deduction": "Cells shared by every legal pattern of an individual line.",
        "nonogram_cross_line_propagation": "Forced cells confirmed through row and column evidence.", "nonogram_solution_space_analysis": "Capped solution count and ambiguous witness differences.",
    })
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = NonogramScenarioGenerator(config), NonogramPuzzleManager(config), NonogramValidator()
        self._initialize_variety_support()
    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self._select_varied_problem(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)
    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "height": puzzle.metadata.get("height"), "width": puzzle.metadata.get("width")}, output_for=lambda name: self._run_tool(name, puzzle))
    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]: return {"puzzle_id": puzzle.puzzle_id, "height": puzzle.metadata.get("height"), "width": puzzle.metadata.get("width"), "difficulty": puzzle.difficulty, "required_strategies": puzzle.required_strategies, "transformation": puzzle.transformation}
    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {key: truth.get(key) for key in ("solution_grid", "validity_status", "solvability_status", "unique_solution_status", "solution_count", "suggested_move")} | {"structural_violations": truth.get("structural_violations", [])[:10], "forced_filled": truth.get("forced_filled", [])[:12], "forced_empty": truth.get("forced_empty", [])[:12]}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        del scenario
        return compact_tool_context(self.name, puzzle, tool_usage)
    def generation_guidance(self) -> str: return "Use the supplied Nonogram clues exactly. A clue gives consecutive filled runs, and separate runs must have at least one empty cell."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "grid_height": metadata.get("height"), "grid_width": metadata.get("width"), "row_clues": metadata.get("row_clues", []), "column_clues": metadata.get("column_clues", []), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); self._add_flat_tool_metadata(row, sample); return row
    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["nonogram_constraint_validation", "nonogram_puzzle_summary", "nonogram_rules_reference"]
        if scenario.edge_case in {"invalid_clues", "unsolvable_puzzle", "ambiguous_puzzle"}: return ["nonogram_constraint_validation", "nonogram_solution_space_analysis", "nonogram_overlap_deduction"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["nonogram_rules_reference", "nonogram_puzzle_summary", "nonogram_overlap_deduction"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}: return ["nonogram_row_pattern_analysis", "nonogram_column_pattern_analysis", "nonogram_overlap_deduction", "nonogram_cross_line_propagation"]
        primary = self._tool_name(scenario)
        if primary == "nonogram_solution_verification": return ["nonogram_line_analysis", "nonogram_cross_line_propagation", "nonogram_solution_space_analysis", primary]
        if primary == "nonogram_line_analysis": return [primary, "nonogram_overlap_deduction", "nonogram_cross_line_propagation"]
        if primary == "nonogram_constraint_validation": return [primary, "nonogram_solution_space_analysis", "nonogram_line_analysis"]
        return [primary or "nonogram_puzzle_summary", "nonogram_constraint_validation"]
    @staticmethod
    def _tool_name(scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_clues", "unsolvable_puzzle", "ambiguous_puzzle"} or scenario.tool_usage == "constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}: return "nonogram_constraint_validation"
        if scenario.tool_usage == "line_candidate_scan" or scenario.task_category in {"hint", "next_best_move", "clue_analysis"}: return "nonogram_line_analysis"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle": return "nonogram_solution_verification"
        return None

    def _run_tool(self, name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth; height, width, rows, columns = parse_puzzle(puzzle.puzzle)
        if name == "nonogram_line_analysis": return {"forced_filled": truth.get("forced_filled", []), "forced_empty": truth.get("forced_empty", []), "suggested_move": truth.get("suggested_move")}
        if name == "nonogram_constraint_validation": return {"validity_status": truth.get("validity_status"), "solvability_status": truth.get("solvability_status"), "structural_violations": truth.get("structural_violations", [])}
        if name == "nonogram_solution_verification": return {"solution": truth.get("solution"), "solution_grid": truth.get("solution_grid"), "unique_solution_status": truth.get("unique_solution_status")}
        if name == "nonogram_rules_reference": return {"rules": ["Clues describe consecutive filled runs in order.", "Separate runs require at least one empty cell."]}
        if name == "nonogram_puzzle_summary": return {"height": height, "width": width, "row_count": len(rows), "column_count": len(columns)}
        if name == "nonogram_row_pattern_analysis": return {"rows": self._pattern_summary(width, rows, "row")}
        if name == "nonogram_column_pattern_analysis": return {"columns": self._pattern_summary(height, columns, "column")}
        if name == "nonogram_overlap_deduction": return {"line_overlaps": self._overlaps(width, rows, "row") + self._overlaps(height, columns, "column")}
        if name == "nonogram_cross_line_propagation": return {"forced_filled": truth.get("forced_filled", []), "forced_empty": truth.get("forced_empty", []), "evidence": "solver_consensus_across_rows_and_columns"}
        if name == "nonogram_solution_space_analysis":
            solutions = solve_nonogram(height, width, rows, columns, limit=2) if not validate_structure(height, width, rows, columns) else []
            return self._solution_space(solutions, height, width, truth.get("solvability_status"))
        return {}

    @staticmethod
    def _pattern_summary(length: int, clues: list[list[int]], label: str) -> list[dict[str, Any]]:
        result = []
        for index, clue in enumerate(clues):
            patterns = line_patterns(length, clue)
            result.append({label: index + 1, "clue": clue, "pattern_count": len(patterns), "examples": patterns[:4]})
        return result

    @staticmethod
    def _overlaps(length: int, clues: list[list[int]], label: str) -> list[dict[str, Any]]:
        result = []
        for index, clue in enumerate(clues):
            patterns = line_patterns(length, clue)
            if patterns:
                filled = [offset + 1 for offset in range(length) if all(pattern[offset] for pattern in patterns)]
                empty = [offset + 1 for offset in range(length) if all(not pattern[offset] for pattern in patterns)]
                if filled or empty: result.append({label: index + 1, "forced_filled_positions": filled, "forced_empty_positions": empty})
        return result

    @staticmethod
    def _solution_space(solutions: list[list[list[int]]], height: int, width: int, status: str | None) -> dict[str, Any]:
        differences = []
        if len(solutions) > 1:
            differences = [f"r{r + 1}c{c + 1}" for r in range(height) for c in range(width) if solutions[0][r][c] != solutions[1][r][c]]
        return {"solution_count_capped": len(solutions), "cap": 2, "status": status, "witness_differences": differences}
