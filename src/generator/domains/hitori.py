from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..hitori import HitoriPuzzleManager, parse_puzzle, solve_hitori, validate_structure
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


class HitoriDomainAdapter(VariedDomainSupport):
    name = "hitori"
    tool_catalog = make_tool_catalog(name, {
        "hitori_duplicate_analysis": "Forced shaded and unshaded cells from duplicate constraints.", "hitori_constraint_validation": "Grid, adjacency, connectivity, and solver validation.",
        "hitori_solution_verification": "Complete solver-backed shading verification.", "hitori_rules_reference": "Canonical Hitori rules.", "hitori_puzzle_summary": "Grid dimensions and difficulty.",
        "hitori_row_duplicate_groups": "Repeated-value groups in each row.", "hitori_column_duplicate_groups": "Repeated-value groups in each column.",
        "hitori_adjacency_risk_analysis": "Cells that must remain unshaded around forced shaded cells.", "hitori_connectivity_analysis": "Connectivity statistics for the unshaded solution graph.",
        "hitori_solution_space_analysis": "Capped solution count and ambiguous witness differences.",
    })
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = HitoriScenarioGenerator(config), HitoriPuzzleManager(config), HitoriValidator()
        self._initialize_variety_support()
    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self._select_varied_problem(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)
    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "size": puzzle.metadata.get("size")}, output_for=lambda name: self._run_tool(name, puzzle))
    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]: return {"puzzle_id": puzzle.puzzle_id, "size": puzzle.metadata.get("size"), "difficulty": puzzle.difficulty, "required_strategies": puzzle.required_strategies, "transformation": puzzle.transformation}
    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {key: truth.get(key) for key in ("solution_mask", "validity_status", "solvability_status", "unique_solution_status", "solution_count", "suggested_move")} | {"structural_violations": truth.get("structural_violations", [])[:10], "solution_violations": truth.get("solution_violations", [])[:10], "forced_shaded": truth.get("forced_shaded", [])[:12], "forced_unshaded": truth.get("forced_unshaded", [])[:12]}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        del scenario
        return compact_tool_context(self.name, puzzle, tool_usage)
    def generation_guidance(self) -> str: return "Use the supplied Hitori grid exactly. Unshaded values must be unique in each row and column, shaded cells cannot touch, and all unshaded cells must remain connected."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "grid_size": metadata.get("size"), "grid": metadata.get("grid", []), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); self._add_flat_tool_metadata(row, sample); return row
    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["hitori_constraint_validation", "hitori_puzzle_summary", "hitori_rules_reference"]
        if scenario.edge_case in {"invalid_grid", "unsolvable_puzzle", "ambiguous_puzzle"}: return ["hitori_constraint_validation", "hitori_solution_space_analysis", "hitori_connectivity_analysis"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["hitori_rules_reference", "hitori_puzzle_summary", "hitori_row_duplicate_groups"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}: return ["hitori_row_duplicate_groups", "hitori_column_duplicate_groups", "hitori_adjacency_risk_analysis", "hitori_connectivity_analysis"]
        primary = self._tool_name(scenario)
        if primary == "hitori_solution_verification": return ["hitori_duplicate_analysis", "hitori_connectivity_analysis", "hitori_solution_space_analysis", primary]
        if primary == "hitori_duplicate_analysis": return [primary, "hitori_row_duplicate_groups", "hitori_column_duplicate_groups", "hitori_adjacency_risk_analysis"]
        if primary == "hitori_constraint_validation": return [primary, "hitori_connectivity_analysis", "hitori_solution_space_analysis"]
        return [primary or "hitori_puzzle_summary", "hitori_constraint_validation"]
    @staticmethod
    def _tool_name(scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_grid", "unsolvable_puzzle", "ambiguous_puzzle"} or scenario.tool_usage == "constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}: return "hitori_constraint_validation"
        if scenario.tool_usage == "duplicate_scan" or scenario.task_category in {"hint", "next_best_move", "duplicate_analysis"}: return "hitori_duplicate_analysis"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle": return "hitori_solution_verification"
        return None

    def _run_tool(self, name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth; size, grid = parse_puzzle(puzzle.puzzle)
        if name == "hitori_duplicate_analysis": return {"forced_shaded": truth.get("forced_shaded", []), "forced_unshaded": truth.get("forced_unshaded", []), "suggested_move": truth.get("suggested_move")}
        if name == "hitori_constraint_validation": return {"validity_status": truth.get("validity_status"), "solvability_status": truth.get("solvability_status"), "structural_violations": truth.get("structural_violations", []), "solution_violations": truth.get("solution_violations", [])}
        if name == "hitori_solution_verification": return {"solution": truth.get("solution"), "solution_mask": truth.get("solution_mask"), "unique_solution_status": truth.get("unique_solution_status")}
        if name == "hitori_rules_reference": return {"rules": ["Unshaded values are unique in each row and column.", "Shaded cells do not touch orthogonally and unshaded cells stay connected."]}
        if name == "hitori_puzzle_summary": return {"size": size, "cell_count": puzzle.num_clues, "difficulty": puzzle.difficulty}
        if name == "hitori_row_duplicate_groups": return {"groups": self._duplicates(grid, by_row=True)}
        if name == "hitori_column_duplicate_groups": return {"groups": self._duplicates(grid, by_row=False)}
        if name == "hitori_adjacency_risk_analysis": return {"must_remain_unshaded": self._adjacent_cells(size, truth.get("forced_shaded", []))}
        if name == "hitori_connectivity_analysis": return self._connectivity(truth.get("solution_mask") or [], size)
        if name == "hitori_solution_space_analysis":
            solutions = solve_hitori(size, grid, limit=2) if not validate_structure(size, grid) else []
            differences = [f"r{r + 1}c{c + 1}" for r in range(size) for c in range(size) if len(solutions) > 1 and solutions[0][r][c] != solutions[1][r][c]]
            return {"solution_count_capped": len(solutions), "cap": 2, "status": truth.get("solvability_status"), "witness_differences": differences}
        return {}

    @staticmethod
    def _duplicates(grid: list[list[int]], *, by_row: bool) -> list[dict[str, Any]]:
        size = len(grid); groups = []
        for unit in range(size):
            values: dict[int, list[str]] = {}
            for offset in range(size):
                row, col = (unit, offset) if by_row else (offset, unit)
                values.setdefault(grid[row][col], []).append(f"r{row + 1}c{col + 1}")
            groups.extend({"value": value, "cells": cells, "unit": f"{'row' if by_row else 'column'}_{unit + 1}"} for value, cells in values.items() if len(cells) > 1)
        return groups

    @staticmethod
    def _adjacent_cells(size: int, shaded: list[str]) -> list[str]:
        cells = set()
        for cell in shaded:
            row, col = (int(part) - 1 for part in cell[1:].split("c"))
            for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if 0 <= nr < size and 0 <= nc < size: cells.add(f"r{nr + 1}c{nc + 1}")
        return sorted(cells - set(shaded))

    @staticmethod
    def _connectivity(mask: list[list[int]], size: int) -> dict[str, Any]:
        open_cells = {(r, c) for r in range(size) for c in range(size) if mask and not mask[r][c]}; components = []
        while open_cells:
            stack = [open_cells.pop()]; count = 0
            while stack:
                r, c = stack.pop(); count += 1
                for neighbor in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if neighbor in open_cells: open_cells.remove(neighbor); stack.append(neighbor)
            components.append(count)
        return {"component_count": len(components), "component_sizes": sorted(components, reverse=True), "connected": len(components) == 1}
