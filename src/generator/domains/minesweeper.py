from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..minesweeper import MinesweeperPuzzleManager, mines_from_mask, neighbors, parse_state
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class MinesweeperScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {"next_best_move": "request_safe_action", "validity_check": "validate_minesweeper_state", "rules_explanation": "learn_minesweeper_rules", "solve_board": "request_complete_mine_layout", "probability_analysis": "compare_mine_probabilities", "flag_analysis": "check_flags", "chord_analysis": "analyze_chord", "hint": "request_minesweeper_hint", "mistake_correction": "diagnose_flagging_mistake", "technique_discussion": "discuss_constraint_strategy", "beginner_question": "learn_minesweeper_basics", "advanced_question": "analyze_frontier_components", "general_chat": "general_minesweeper_help"}
        base = intents.get(task_category, task_category); return f"{base}_{edge_case}" if edge_case != "none" else base
    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = ["Use the supplied visible Minesweeper state exactly.", "Distinguish logically forced cells from probability-based choices.", "Do not reveal the hidden mine mask unless complete verification is explicitly requested."]
        if task_category == "hint": constraints.append("Give one safe or forced flag action when available; otherwise state the exact lowest risk.")
        if edge_case == "malformed_input": constraints.append("Diagnose the malformed board without reconstructing it.")
        if tool_usage != "none": constraints.append(f"Use the requested evidence mode: {tool_usage}.")
        return constraints


class MinesweeperValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors = []
        if output.get("conversation_type") != scenario.conversation_type: errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category: errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip(): errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip(): errors.append("single-turn response is empty")
        elif not isinstance(output.get("messages"), list) or not output["messages"]: errors.append("multi-turn messages list is empty")
        if scenario.edge_case != "malformed_input" and output.get("board") != puzzle.rendered_board: errors.append("output board does not match selected Minesweeper state")
        truth = puzzle.ground_truth
        if scenario.edge_case == "none" and truth.get("validity_status") != "valid": errors.append("standard Minesweeper scenario requires a consistent state")
        if scenario.edge_case in {"invalid_clues", "contradictory_flags", "impossible_state"} and truth.get("validity_status") != "invalid": errors.append(f"{scenario.edge_case} is not solver-confirmed invalid")
        if scenario.edge_case == "loss_state" and not truth.get("loss"): errors.append("loss_state does not reveal a mine")
        if scenario.edge_case == "no_forced_move" and (truth.get("forced_safe") or truth.get("forced_mines")): errors.append("no_forced_move still contains a forced action")
        return ValidationResult(not errors, errors)


class MinesweeperDomainAdapter(VariedDomainSupport):
    name = "minesweeper"
    tool_catalog = make_tool_catalog(name, {
        "minesweeper_board_validation": "Validate dimensions, symbols, clues, flags, total mines, and constraint consistency.",
        "minesweeper_neighbor_analysis": "Show clue equations for neighboring hidden and flagged cells.",
        "minesweeper_frontier_analysis": "Describe connected frontier constraint components.",
        "minesweeper_forced_safe_scan": "Find cells safe in every consistent mine layout.",
        "minesweeper_forced_mine_scan": "Find cells mined in every consistent mine layout.",
        "minesweeper_probability_analysis": "Return exact mine probabilities over all consistent layouts.",
        "minesweeper_flag_validation": "Verify visible flags against the stored generated layout.",
        "minesweeper_chord_analysis": "Find revealed clues whose flags permit a chord action.",
        "minesweeper_solution_space_analysis": "Report exact consistent-layout count and frontier decomposition.",
        "minesweeper_solution_verification": "Return the complete generated mine mask for explicit solve requests.",
        "minesweeper_board_summary": "Summarize size, mines, flags, revealed cells, and frontier size.",
        "minesweeper_rules_reference": "Canonical reveal, clue, flag, chord, win, and loss rules.",
    })
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = MinesweeperScenarioGenerator(config), MinesweeperPuzzleManager(config), MinesweeperValidator(); self._initialize_variety_support()
    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self._select_varied_problem(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)
    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]: return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "visible_state": puzzle.puzzle}, output_for=lambda name: self._run_tool(name, puzzle))
    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]: return {"puzzle_id": puzzle.puzzle_id, "width": puzzle.metadata.get("width"), "height": puzzle.metadata.get("height"), "mine_count": puzzle.metadata.get("mine_count"), "difficulty": puzzle.difficulty, "transformation": puzzle.transformation}
    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {key: truth.get(key) for key in ("validity_status", "solution_count", "unique_solution_status", "forced_safe", "forced_mines", "probabilities", "suggested_move", "loss")} | {"structural_violations": truth.get("structural_violations", [])[:10]}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        context = compact_tool_context(self.name, puzzle, tool_usage)
        if scenario.task_category == "solve_board" or scenario.tool_usage == "solution_verification":
            for target, original in zip(context["calls"], tool_usage.get("calls", [])):
                if original["tool_name"] == "minesweeper_solution_verification": target["output"] = original["output"]
        return context
    def generation_guidance(self) -> str: return "Use exact frontier evidence. Never call a probability-based reveal safe, and do not expose the mine mask outside explicit solve verification."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "grid_width": metadata.get("width"), "grid_height": metadata.get("height"), "mine_count": metadata.get("mine_count"), "revealed_count": metadata.get("revealed_count"), "frontier_size": metadata.get("frontier_size"), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); self._add_flat_tool_metadata(row, sample); return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["minesweeper_board_validation", "minesweeper_board_summary", "minesweeper_rules_reference"]
        if scenario.edge_case in {"invalid_clues", "contradictory_flags", "impossible_state"}: return ["minesweeper_board_validation", "minesweeper_neighbor_analysis", "minesweeper_solution_space_analysis"]
        if scenario.edge_case == "loss_state": return ["minesweeper_board_validation", "minesweeper_board_summary", "minesweeper_rules_reference"]
        if scenario.edge_case == "no_forced_move": return ["minesweeper_frontier_analysis", "minesweeper_probability_analysis", "minesweeper_solution_space_analysis"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["minesweeper_rules_reference", "minesweeper_board_summary", "minesweeper_neighbor_analysis"]
        if scenario.task_category == "solve_board": return ["minesweeper_solution_space_analysis", "minesweeper_solution_verification", "minesweeper_board_validation"]
        if scenario.task_category in {"validity_check", "mistake_correction", "flag_analysis"}: return ["minesweeper_board_validation", "minesweeper_flag_validation", "minesweeper_neighbor_analysis"]
        if scenario.task_category == "chord_analysis": return ["minesweeper_neighbor_analysis", "minesweeper_flag_validation", "minesweeper_chord_analysis"]
        if scenario.task_category == "probability_analysis": return ["minesweeper_frontier_analysis", "minesweeper_probability_analysis", "minesweeper_solution_space_analysis"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}: return ["minesweeper_neighbor_analysis", "minesweeper_frontier_analysis", "minesweeper_probability_analysis", "minesweeper_chord_analysis"]
        return ["minesweeper_forced_safe_scan", "minesweeper_forced_mine_scan", "minesweeper_probability_analysis", "minesweeper_frontier_analysis"]

    def _run_tool(self, name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth; width, height, total_mines, visible = parse_state(puzzle.puzzle)
        if name == "minesweeper_board_validation": return {"validity_status": truth.get("validity_status"), "violations": truth.get("structural_violations", []), "loss": truth.get("loss", False)}
        if name == "minesweeper_neighbor_analysis": return {"constraints": truth.get("constraints", [])}
        if name == "minesweeper_frontier_analysis": return {"components": truth.get("components", []), "frontier_cells": truth.get("frontier_cells", []), "off_frontier_count": truth.get("off_frontier_count", 0)}
        if name == "minesweeper_forced_safe_scan": return {"forced_safe": truth.get("forced_safe", []), "suggested_move": truth.get("suggested_move")}
        if name == "minesweeper_forced_mine_scan": return {"forced_mines": truth.get("forced_mines", []), "suggested_move": truth.get("suggested_move")}
        if name == "minesweeper_probability_analysis": return {"probabilities": truth.get("probabilities", {}), "suggested_move": truth.get("suggested_move"), "exact": True}
        if name == "minesweeper_flag_validation":
            mines = mines_from_mask(puzzle.solution, width, height); flags = {(r, c) for r in range(height) for c in range(width) if visible[r][c] == "F"}
            return {"correct_flags": sorted(f"r{r+1}c{c+1}" for r, c in flags & mines), "incorrect_flags": sorted(f"r{r+1}c{c+1}" for r, c in flags - mines), "unflagged_mines": len(mines - flags)}
        if name == "minesweeper_chord_analysis":
            opportunities = []
            for r in range(height):
                for c in range(width):
                    if visible[r][c].isdigit():
                        adjacent = neighbors(r, c, height, width); flags = sum(visible[rr][cc] == "F" for rr, cc in adjacent); hidden = [f"r{rr+1}c{cc+1}" for rr, cc in adjacent if visible[rr][cc] == "#"]
                        if hidden and flags == int(visible[r][c]): opportunities.append({"clue_cell": f"r{r+1}c{c+1}", "reveals": hidden})
            return {"chord_opportunities": opportunities}
        if name == "minesweeper_solution_space_analysis": return {"solution_count": truth.get("solution_count", 0), "unique": truth.get("unique_solution_status", False), "components": truth.get("components", []), "exact": True}
        if name == "minesweeper_solution_verification": return {"mine_mask": truth.get("mine_mask"), "solution": truth.get("solution"), "verified_layout": True}
        if name == "minesweeper_board_summary": return {"width": width, "height": height, "mine_count": total_mines, "total_mines": total_mines, "flags": sum(row.count("F") for row in visible), "revealed": sum(char.isdigit() for row in visible for char in row), "hidden": sum(row.count("#") for row in visible)}
        if name == "minesweeper_rules_reference": return {"rules": ["A number counts mines in its eight neighboring cells.", "Flags mark suspected mines but do not change clue arithmetic except as assignments.", "Chord only when adjacent flags equal the clue; revealing a mine loses."]}
        return {}
