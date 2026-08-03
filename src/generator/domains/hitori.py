from __future__ import annotations

from typing import Any

from ..hitori import HitoriPuzzleManager, parse_puzzle, validate_structure
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class HitoriScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {"next_best_move": "ask_for_next_elimination", "validity_check": "verify_hitori_eliminations", "rules_explanation": "learn_hitori_rules", "solve_puzzle": "request_solution_guidance", "duplicate_analysis": "analyze_duplicate_values", "hint": "ask_for_hitori_hint", "mistake_correction": "debug_shading_mistake", "technique_discussion": "discuss_hitori_strategy", "beginner_question": "learn_hitori_basics", "advanced_question": "analyze_connectivity_constraints", "general_chat": "general_hitori_help"}
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = ["Use the supplied Hitori number grid exactly unless malformed input is explicitly required.", "Ground every claim in duplicate elimination, non-adjacent shaded cells, and unshaded connectivity."]
        if task_category == "hint": constraints.append("Prefer one forced shade or keep decision over revealing the complete elimination mask.")
        if edge_case == "incorrect_assumption": constraints.append("Correct the user's Hitori assumption politely.")
        if edge_case == "malformed_input": constraints.append("Acknowledge the malformed grid before attempting to help.")
        if tool_usage != "none": constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class HitoriValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors: list[str] = []
        if output.get("conversation_type") != scenario.conversation_type: errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category: errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip(): errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip(): errors.append("single-turn response is empty")
        elif not isinstance(output.get("messages"), list) or not output["messages"]:
            errors.append("multi-turn messages list is empty")
        elif any(not str(item.get("user", "")).strip() or not str(item.get("response", "")).strip() for item in output["messages"]):
            errors.append("a multi-turn message is missing user text or response text")
        if scenario.edge_case == "malformed_input":
            if not output.get("board"): errors.append("malformed_input scenario requires a malformed puzzle string")
        elif output.get("board") != puzzle.rendered_board:
            errors.append("output board does not match selected puzzle")
        size, grid = parse_puzzle(puzzle.puzzle)
        structural, truth = validate_structure(size, grid), puzzle.ground_truth
        if scenario.edge_case == "none" and (structural or truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status): errors.append("standard scenario requires a structurally valid unique Hitori")
        if truth.get("solution") != puzzle.solution: errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_grid" and not structural: errors.append("invalid_grid scenario has no structural violation")
        if scenario.edge_case == "unsolvable_puzzle" and truth.get("solvability_status") != "unsolvable": errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if scenario.edge_case == "ambiguous_puzzle" and truth.get("solvability_status") != "ambiguous": errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class HitoriDomainAdapter:
    name = "hitori"
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = HitoriScenarioGenerator(config), HitoriPuzzleManager(config), HitoriValidator()
    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self.puzzle_manager.select_puzzle(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)
    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        name = self._tool_name(scenario)
        if not name: return {"used": False, "tool_name": None, "tool_input": None, "tool_output": None, "reason": None}
        truth = puzzle.ground_truth
        outputs = {"hitori_duplicate_analysis": {"forced_shaded": truth.get("forced_shaded", []), "forced_unshaded": truth.get("forced_unshaded", []), "suggested_move": truth.get("suggested_move")}, "hitori_constraint_validation": {"validity_status": truth.get("validity_status"), "solvability_status": truth.get("solvability_status"), "structural_violations": truth.get("structural_violations", []), "solution_violations": truth.get("solution_violations", [])}, "hitori_solution_verification": {"solution": truth.get("solution"), "solution_mask": truth.get("solution_mask"), "unique_solution_status": truth.get("unique_solution_status")}}
        return {"used": True, "tool_name": name, "tool_input": {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "size": puzzle.metadata.get("size")}, "tool_output": outputs[name], "reason": "The scenario needs deterministic Hitori duplicate and connectivity evidence."}
    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]: return {"puzzle_id": puzzle.puzzle_id, "size": puzzle.metadata.get("size"), "difficulty": puzzle.difficulty, "required_strategies": puzzle.required_strategies, "transformation": puzzle.transformation}
    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {key: truth.get(key) for key in ("solution_mask", "validity_status", "solvability_status", "unique_solution_status", "solution_count", "suggested_move")} | {"structural_violations": truth.get("structural_violations", [])[:10], "solution_violations": truth.get("solution_violations", [])[:10], "forced_shaded": truth.get("forced_shaded", [])[:12], "forced_unshaded": truth.get("forced_unshaded", [])[:12]}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        del scenario, puzzle
        output = dict(tool_usage.get("tool_output") or {}); output["forced_shaded"] = output.get("forced_shaded", [])[:12]; output["forced_unshaded"] = output.get("forced_unshaded", [])[:12]
        if tool_usage.get("tool_name") == "hitori_solution_verification": output.pop("solution", None); output.pop("solution_mask", None)
        return {"domain": self.name, "tool_usage": {**tool_usage, "tool_output": output}}
    def generation_guidance(self) -> str: return "Use the supplied Hitori grid exactly. Unshaded values must be unique in each row and column, shaded cells cannot touch, and all unshaded cells must remain connected."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "grid_size": metadata.get("size"), "grid": metadata.get("grid", []), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); return row
    @staticmethod
    def _tool_name(scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_grid", "unsolvable_puzzle", "ambiguous_puzzle"} or scenario.tool_usage == "constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}: return "hitori_constraint_validation"
        if scenario.tool_usage == "duplicate_scan" or scenario.task_category in {"hint", "next_best_move", "duplicate_analysis"}: return "hitori_duplicate_analysis"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle": return "hitori_solution_verification"
        return None
