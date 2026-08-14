from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..nurikabe import NurikabePuzzleManager, parse_puzzle, solve_nurikabe, validate_structure
from ..scenario import ScenarioGenerator


class NurikabeScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {"next_best_move": "ask_for_next_nurikabe_move", "validity_check": "verify_nurikabe_state", "rules_explanation": "learn_nurikabe_rules", "solve_puzzle": "request_nurikabe_solution_guidance", "island_analysis": "analyze_island_growth", "hint": "ask_for_nurikabe_hint", "mistake_correction": "debug_nurikabe_mistake", "technique_discussion": "discuss_nurikabe_strategy", "beginner_question": "learn_nurikabe_basics", "advanced_question": "analyze_sea_connectivity", "general_chat": "general_nurikabe_help"}
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = ["Use the supplied Nurikabe clue grid exactly unless malformed input is explicitly required.", "Ground claims in island size, island separation, connected sea, and the no-2x2-sea rule."]
        if task_category == "hint": constraints.append("Give one deductive step rather than revealing the full sea map.")
        if edge_case == "incorrect_assumption": constraints.append("Correct the user's Nurikabe assumption politely.")
        if edge_case == "malformed_input": constraints.append("Acknowledge malformed input before attempting to solve it.")
        if tool_usage != "none": constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class NurikabeValidator:
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
        elif output.get("board") != puzzle.rendered_board: errors.append("output board does not match selected puzzle")
        size, clues = parse_puzzle(puzzle.puzzle); truth = puzzle.ground_truth
        if scenario.edge_case == "none" and (validate_structure(size, clues) or truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status): errors.append("standard scenario requires a structurally valid unique Nurikabe")
        if truth.get("solution") != puzzle.solution: errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_grid" and not validate_structure(size, clues): errors.append("invalid_grid scenario has no structural violation")
        if scenario.edge_case == "unsolvable_puzzle" and truth.get("solvability_status") != "unsolvable": errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if scenario.edge_case == "ambiguous_puzzle" and truth.get("solvability_status") != "ambiguous": errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class NurikabeDomainAdapter(VariedDomainSupport):
    name = "nurikabe"
    tool_catalog = make_tool_catalog(name, {
        "nurikabe_deduction_scan": "Forced sea and island cells with a next move.", "nurikabe_constraint_validation": "Island, sea, structure, and solver validation.",
        "nurikabe_solution_verification": "Complete solver-backed sea mask verification.", "nurikabe_rules_reference": "Canonical island and sea rules.", "nurikabe_puzzle_summary": "Grid size, clues, and difficulty.",
        "nurikabe_island_capacity_analysis": "Maximum geometric reach and required size for each clue island.", "nurikabe_island_separation_scan": "Cells forced to sea between nearby clue islands.",
        "nurikabe_sea_connectivity_analysis": "Connected-component statistics for the verified sea.", "nurikabe_two_by_two_risk_scan": "Potential 2x2 sea violations and forced island cells.",
        "nurikabe_solution_space_analysis": "Capped solution count and ambiguous witness differences.",
    })
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = NurikabeScenarioGenerator(config), NurikabePuzzleManager(config), NurikabeValidator()
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
        return {key: truth.get(key) for key in ("validity_status", "solvability_status", "unique_solution_status", "solution_count", "suggested_move")} | {"structural_violations": truth.get("structural_violations", [])[:10], "solution_violations": truth.get("solution_violations", [])[:10], "forced_sea": truth.get("forced_sea", [])[:12], "forced_island": truth.get("forced_island", [])[:12]}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        del scenario
        return compact_tool_context(self.name, puzzle, tool_usage)
    def generation_guidance(self) -> str: return "Use the supplied Nurikabe grid exactly. Each island has one clue and its stated size, islands cannot touch orthogonally, and the sea is connected without 2x2 sea blocks."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "grid_size": metadata.get("size"), "clues": metadata.get("clues", []), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); self._add_flat_tool_metadata(row, sample); return row
    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["nurikabe_constraint_validation", "nurikabe_puzzle_summary", "nurikabe_rules_reference"]
        if scenario.edge_case in {"invalid_grid", "unsolvable_puzzle", "ambiguous_puzzle"}: return ["nurikabe_constraint_validation", "nurikabe_solution_space_analysis", "nurikabe_sea_connectivity_analysis"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["nurikabe_rules_reference", "nurikabe_puzzle_summary", "nurikabe_island_capacity_analysis"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}: return ["nurikabe_island_capacity_analysis", "nurikabe_island_separation_scan", "nurikabe_sea_connectivity_analysis", "nurikabe_two_by_two_risk_scan"]
        primary = self._tool_name(scenario)
        if primary == "nurikabe_solution_verification": return ["nurikabe_deduction_scan", "nurikabe_sea_connectivity_analysis", "nurikabe_solution_space_analysis", primary]
        if primary == "nurikabe_deduction_scan": return [primary, "nurikabe_island_separation_scan", "nurikabe_two_by_two_risk_scan"]
        if primary == "nurikabe_constraint_validation": return [primary, "nurikabe_sea_connectivity_analysis", "nurikabe_solution_space_analysis"]
        return [primary or "nurikabe_puzzle_summary", "nurikabe_constraint_validation"]
    @staticmethod
    def _tool_name(scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_grid", "unsolvable_puzzle", "ambiguous_puzzle"} or scenario.tool_usage == "constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}: return "nurikabe_constraint_validation"
        if scenario.tool_usage == "deduction_scan" or scenario.task_category in {"hint", "next_best_move", "island_analysis"}: return "nurikabe_deduction_scan"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle": return "nurikabe_solution_verification"
        return None

    def _run_tool(self, name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth; size, clues = parse_puzzle(puzzle.puzzle)
        if name == "nurikabe_deduction_scan": return {"forced_sea": truth.get("forced_sea", []), "forced_island": truth.get("forced_island", []), "suggested_move": truth.get("suggested_move")}
        if name == "nurikabe_constraint_validation": return {"validity_status": truth.get("validity_status"), "solvability_status": truth.get("solvability_status"), "structural_violations": truth.get("structural_violations", []), "solution_violations": truth.get("solution_violations", [])}
        if name == "nurikabe_solution_verification": return {"solution": truth.get("solution"), "solution_mask": truth.get("solution_mask"), "unique_solution_status": truth.get("unique_solution_status")}
        if name == "nurikabe_rules_reference": return {"rules": ["Each island contains one clue and exactly its stated number of cells.", "The sea is connected, has no 2x2 block, and islands do not touch orthogonally."]}
        if name == "nurikabe_puzzle_summary": return {"size": size, "clue_count": puzzle.num_clues, "difficulty": puzzle.difficulty}
        clue_cells = [(r, c, clues[r][c]) for r in range(size) for c in range(size) if clues[r][c]]
        if name == "nurikabe_island_capacity_analysis": return {"islands": [{"clue_cell": f"r{r + 1}c{c + 1}", "required_size": value, "geometric_reach": sum(1 for rr in range(size) for cc in range(size) if abs(rr-r)+abs(cc-c) < value)} for r, c, value in clue_cells]}
        if name == "nurikabe_island_separation_scan": return {"forced_sea_between_clues": self._separators(clue_cells)}
        if name == "nurikabe_sea_connectivity_analysis": return self._sea_connectivity(truth.get("solution_mask") or [], size)
        if name == "nurikabe_two_by_two_risk_scan": return {"forced_island_to_avoid_2x2": self._two_by_two(size, truth.get("forced_sea", []))}
        if name == "nurikabe_solution_space_analysis":
            solutions = solve_nurikabe(size, clues, limit=2) if not validate_structure(size, clues) else []
            differences = [f"r{r + 1}c{c + 1}" for r in range(size) for c in range(size) if len(solutions) > 1 and solutions[0][r][c] != solutions[1][r][c]]
            return {"solution_count_capped": len(solutions), "cap": 2, "status": truth.get("solvability_status"), "witness_differences": differences}
        return {}

    @staticmethod
    def _separators(clues: list[tuple[int, int, int]]) -> list[str]:
        result = set()
        for r1, c1, _ in clues:
            for r2, c2, _ in clues:
                if r1 == r2 and abs(c1-c2) == 2: result.add(f"r{r1 + 1}c{(c1+c2)//2 + 1}")
                if c1 == c2 and abs(r1-r2) == 2: result.add(f"r{(r1+r2)//2 + 1}c{c1 + 1}")
        return sorted(result)

    @staticmethod
    def _sea_connectivity(mask: list[list[int]], size: int) -> dict[str, Any]:
        sea = {(r, c) for r in range(size) for c in range(size) if mask and mask[r][c]}; components = []
        while sea:
            stack = [sea.pop()]; count = 0
            while stack:
                r, c = stack.pop(); count += 1
                for neighbor in ((r-1,c),(r+1,c),(r,c-1),(r,c+1)):
                    if neighbor in sea: sea.remove(neighbor); stack.append(neighbor)
            components.append(count)
        return {"component_count": len(components), "component_sizes": sorted(components, reverse=True), "connected": len(components) == 1}

    @staticmethod
    def _two_by_two(size: int, forced_sea: list[str]) -> list[str]:
        sea = set(forced_sea); forced = set()
        for r in range(size-1):
            for c in range(size-1):
                cells = {f"r{rr + 1}c{cc + 1}" for rr, cc in ((r,c),(r+1,c),(r,c+1),(r+1,c+1))}
                missing = cells - sea
                if len(missing) == 1: forced.update(missing)
        return sorted(forced)
