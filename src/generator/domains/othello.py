from __future__ import annotations

import json
from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..othello import OthelloPuzzleManager, apply_move, legal_moves, parse_state, positional_features, render_board, search_position, status, validate_state
from ..scenario import ScenarioGenerator


class OthelloScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {"next_best_move": "request_engine_move", "legal_move_check": "check_othello_move", "board_analysis": "analyze_position", "mobility_analysis": "compare_mobility", "corner_strategy": "discuss_corner_control", "endgame_analysis": "solve_endgame", "hint": "request_othello_hint", "mistake_correction": "diagnose_othello_mistake", "rules_explanation": "learn_othello_rules", "technique_discussion": "discuss_othello_strategy", "beginner_question": "learn_othello_basics", "advanced_question": "analyze_search_tradeoffs", "general_chat": "general_othello_help"}
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = ["Preserve the supplied Othello board and side to move.", "Every move must bracket at least one opposing disc in a straight line.", "Describe depth-limited analysis as an engine recommendation, not an exact result."]
        if task_category == "hint": constraints.append("Give a focused strategic hint before revealing the recommended move.")
        if edge_case == "malformed_input": constraints.append("Diagnose the malformed state without reconstructing missing cells.")
        if tool_usage != "none": constraints.append(f"Use the requested evidence mode: {tool_usage}.")
        return constraints


class OthelloValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors: list[str] = []
        if output.get("conversation_type") != scenario.conversation_type: errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category: errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip(): errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip(): errors.append("single-turn response is empty")
        elif not isinstance(output.get("messages"), list) or not output["messages"]: errors.append("multi-turn messages list is empty")
        if scenario.edge_case == "malformed_input":
            if not output.get("board"): errors.append("malformed scenario needs its supplied input")
        elif output.get("board") != puzzle.rendered_board: errors.append("output board does not match selected Othello position")
        truth = puzzle.ground_truth
        if scenario.edge_case == "none" and truth.get("validity_status") != "valid": errors.append("standard Othello scenario requires a valid position")
        if scenario.edge_case == "invalid_board" and truth.get("validity_status") != "invalid": errors.append("invalid_board lacks a structural violation")
        if scenario.edge_case == "forced_pass" and not truth.get("position_status", {}).get("forced_pass"): errors.append("forced_pass position still has a legal move")
        if scenario.edge_case == "terminal_position" and not truth.get("position_status", {}).get("terminal"): errors.append("terminal_position is not terminal")
        return ValidationResult(not errors, errors)


class OthelloDomainAdapter(VariedDomainSupport):
    name = "othello"
    tool_catalog = make_tool_catalog(name, {
        "othello_board_validation": "Parse and validate board shape, symbols, turn, pass state, and edge claims.",
        "othello_legal_move_scan": "List legal moves and every disc flipped by each move.",
        "othello_move_application": "Apply the recommended legal move and return the exact next state.",
        "othello_disc_count_analysis": "Count black, white, and empty squares.",
        "othello_mobility_analysis": "Compare current and opponent legal mobility.",
        "othello_positional_analysis": "Analyze corners, edges, frontier discs, parity, and mobility.",
        "othello_move_comparison": "Compare legal moves using deterministic alpha-beta analysis.",
        "othello_endgame_search": "Return exact terminal search only when the endgame is small enough.",
        "othello_state_summary": "Summarize turn, pass, terminal, winner, and search completeness.",
        "othello_rules_reference": "Canonical Othello placement, flipping, pass, and ending rules.",
    })

    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = OthelloScenarioGenerator(config), OthelloPuzzleManager(config), OthelloValidator()
        self._initialize_variety_support()

    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self._select_varied_problem(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)

    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "state": puzzle.puzzle}, output_for=lambda name: self._run_tool(name, puzzle))

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]: return {"puzzle_id": puzzle.puzzle_id, "difficulty": puzzle.difficulty, "side_to_move": puzzle.metadata.get("side_to_move"), "empty_count": puzzle.metadata.get("empty_count"), "transformation": puzzle.transformation}
    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {"validity_status": truth.get("validity_status"), "structural_violations": truth.get("structural_violations", []), "position_status": truth.get("position_status", {}), "recommended_move": truth.get("recommended_move"), "unique_best_move": truth.get("unique_best_move"), "analysis": {key: truth.get("analysis", {}).get(key) for key in ("analysis_depth", "exact", "nodes")}}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]: del scenario; return compact_tool_context(self.name, puzzle, tool_usage)
    def generation_guidance(self) -> str: return "Use the supplied Othello state exactly. Legal moves must flip bracketed discs; distinguish depth-limited recommendations from exact endgame results."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "side_to_move": metadata.get("side_to_move"), "empty_count": metadata.get("empty_count"), "analysis_exact": metadata.get("analysis_exact"), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); self._add_flat_tool_metadata(row, sample); return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case in {"malformed_input", "invalid_board", "contradictory_input"}: return ["othello_board_validation", "othello_state_summary", "othello_rules_reference"]
        if scenario.edge_case == "illegal_move": return ["othello_board_validation", "othello_legal_move_scan", "othello_state_summary"]
        if scenario.edge_case in {"forced_pass", "terminal_position"}: return ["othello_board_validation", "othello_legal_move_scan", "othello_state_summary", "othello_rules_reference"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["othello_rules_reference", "othello_state_summary", "othello_disc_count_analysis"]
        if scenario.task_category == "legal_move_check": return ["othello_board_validation", "othello_legal_move_scan", "othello_move_application"]
        if scenario.task_category == "endgame_analysis": return ["othello_legal_move_scan", "othello_endgame_search", "othello_state_summary"]
        if scenario.task_category in {"next_best_move", "hint", "mistake_correction"}: return ["othello_legal_move_scan", "othello_positional_analysis", "othello_move_comparison", "othello_move_application"]
        if scenario.task_category in {"mobility_analysis", "corner_strategy", "technique_discussion", "advanced_question"}: return ["othello_mobility_analysis", "othello_positional_analysis", "othello_move_comparison"]
        return ["othello_state_summary", "othello_disc_count_analysis", "othello_positional_analysis"]

    def _run_tool(self, name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        try: board, side, passes = parse_state(puzzle.puzzle)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError): board, side, passes = "", "?", 0
        if name == "othello_board_validation":
            return {"validity_status": truth.get("validity_status"), "violations": truth.get("structural_violations", []), "mutation": truth.get("mutation", {})}
        if name == "othello_legal_move_scan": return {"side_to_move": side, "moves": truth.get("legal_moves", {}), "count": len(truth.get("legal_moves", {}))}
        if name == "othello_move_application":
            move = truth.get("recommended_move")
            if truth.get("validity_status") != "valid" or move in {None, "terminal"}: return {"applied": False, "reason": "No applicable legal move."}
            next_board, next_side, next_passes, flips = apply_move(board, side, move)
            return {"applied": True, "move": move, "flipped": flips, "next_state": {"board": next_board, "side_to_move": next_side, "consecutive_passes": next_passes}, "rendered_board": render_board(next_board, next_side)}
        if name == "othello_disc_count_analysis": return {"disc_counts": truth.get("disc_counts", {})}
        if name == "othello_mobility_analysis": return {"mobility": truth.get("positional_features", {}).get("mobility", {}), "legal_moves": sorted(truth.get("legal_moves", {}))}
        if name == "othello_positional_analysis": return truth.get("positional_features", {})
        if name == "othello_move_comparison": return truth.get("analysis", {})
        if name == "othello_endgame_search":
            analysis = truth.get("analysis", {})
            return analysis if analysis.get("exact") else {"exact": False, "available": False, "reason": "Exact search is limited to positions with at most ten empty squares.", "depth_limited_recommendation": analysis.get("best_move")}
        if name == "othello_state_summary": return truth.get("position_status", {}) | {"validity_status": truth.get("validity_status"), "analysis_exact": truth.get("analysis", {}).get("exact", False)}
        if name == "othello_rules_reference": return {"rules": ["Place a disc only where it brackets at least one opposing line.", "Flip every bracketed opposing disc in all eight directions.", "A player with no legal move passes; the game ends when neither player can move."]}
        return {}
