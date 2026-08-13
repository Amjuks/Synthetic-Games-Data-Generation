from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..nonogram import NonogramPuzzleManager, parse_puzzle, validate_structure
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
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = NonogramScenarioGenerator(config), NonogramPuzzleManager(config), NonogramValidator()
        self._initialize_variety_support()
    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self._select_varied_problem(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)
    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        outputs = {"nonogram_line_analysis": {"forced_filled": truth.get("forced_filled", []), "forced_empty": truth.get("forced_empty", []), "suggested_move": truth.get("suggested_move")}, "nonogram_constraint_validation": {"validity_status": truth.get("validity_status"), "solvability_status": truth.get("solvability_status"), "structural_violations": truth.get("structural_violations", [])}, "nonogram_solution_verification": {"solution": truth.get("solution"), "solution_grid": truth.get("solution_grid"), "unique_solution_status": truth.get("unique_solution_status")}, "nonogram_rules_reference": {"rules": ["Clues describe consecutive filled runs in order.", "Separate runs require at least one empty cell."]}, "nonogram_puzzle_summary": {"height": puzzle.metadata.get("height"), "width": puzzle.metadata.get("width"), "row_count": len(puzzle.metadata.get("row_clues", [])), "column_count": len(puzzle.metadata.get("column_clues", []))}}
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "height": puzzle.metadata.get("height"), "width": puzzle.metadata.get("width")}, output_for=lambda name: outputs[name])
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
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["nonogram_rules_reference", "nonogram_puzzle_summary"]
        primary = self._tool_name(scenario)
        if primary == "nonogram_solution_verification": return ["nonogram_line_analysis", primary]
        if primary == "nonogram_line_analysis": return [primary, "nonogram_constraint_validation"]
        if primary == "nonogram_constraint_validation": return [primary, "nonogram_line_analysis"]
        return [primary or "nonogram_puzzle_summary", "nonogram_constraint_validation"]
    @staticmethod
    def _tool_name(scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_clues", "unsolvable_puzzle", "ambiguous_puzzle"} or scenario.tool_usage == "constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}: return "nonogram_constraint_validation"
        if scenario.tool_usage == "line_candidate_scan" or scenario.task_category in {"hint", "next_best_move", "clue_analysis"}: return "nonogram_line_analysis"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle": return "nonogram_solution_verification"
        return None
