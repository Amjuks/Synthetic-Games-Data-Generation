from __future__ import annotations

import json
from typing import Any

from ..chess import ChessProblemManager, ChessToolkit
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


OBJECTIVE_CATEGORIES = {
    "notation_conversion",
    "legal_move_check",
    "board_interpretation",
    "explain_previous_move",
    "tactical_puzzle",
    "positional_analysis",
    "endgame_analysis",
    "special_move",
    "draw_rules",
    "terminal_state",
    "best_move",
    "move_comparison",
    "mistake_diagnosis",
    "continuation_speculation",
    "guided_solving",
    "opponent_response_prediction",
    "post_game_review",
}

ENGINE_CATEGORIES = {
    "tactical_puzzle",
    "best_move",
    "move_comparison",
    "mistake_diagnosis",
    "opponent_response_prediction",
    "post_game_review",
}


class ChessScenarioGenerator(ScenarioGenerator):
    def generate(self, **kwargs: Any) -> Scenario:
        scenario = super().generate(**kwargs)
        sample_index = int(kwargs.get("sample_index", 0))
        scenario.tool_usage = {
            "rules_explanation": "none",
            "teaching": "none",
            "general_chat": "none",
            "notation_conversion": "parse_validate",
            "legal_move_check": "legal_moves",
            "board_interpretation": "tactical_inspection",
            "opening_guidance": "reconstruct",
            "explain_previous_move": "reconstruct",
            "tactical_puzzle": "engine_analysis",
            "positional_analysis": "tactical_inspection",
            "endgame_analysis": "tablebase_lookup",
            "special_move": "move_application",
            "draw_rules": "position_status",
            "terminal_state": "position_status",
            "best_move": "engine_analysis",
            "move_comparison": "engine_analysis",
            "mistake_diagnosis": "engine_analysis",
            "continuation_speculation": "legal_moves",
            "hint": "legal_moves",
            "guided_solving": "legal_moves",
            "opponent_response_prediction": "engine_analysis",
            "post_game_review": "engine_analysis",
        }.get(scenario.task_category, "none")
        scenario.constraints = self._build_constraints(scenario.task_category, scenario.edge_case, scenario.tool_usage)
        if scenario.task_category == "tactical_puzzle":
            scenario.metadata["tactical_theme"] = ("checks", "captures", "threats", "forks", "pins", "skewers", "discovered_attacks", "sacrifices", "mating_sequences")[sample_index % 9]
        if scenario.task_category == "positional_analysis":
            scenario.metadata["positional_theme"] = ("development", "king_safety", "pawn_structure", "weak_squares", "space", "piece_activity", "candidate_plans")[sample_index % 7]
        if scenario.task_category == "special_move":
            scenario.metadata["rules_theme"] = ("promotion", "castling", "en_passant")[sample_index % 3]
        elif scenario.task_category == "draw_rules":
            scenario.metadata["rules_theme"] = ("repetition", "fifty_move_rule", "stalemate", "insufficient_material")[sample_index % 4]
        elif scenario.task_category == "terminal_state":
            scenario.metadata["rules_theme"] = ("checkmate", "stalemate", "insufficient_material")[sample_index % 3]
        elif scenario.task_category == "endgame_analysis":
            scenario.metadata["rules_theme"] = ("promotion", "rook_endgame", "insufficient_material")[sample_index % 3]
        return scenario

    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intent = {
            "rules_explanation": "learn_chess_rules",
            "notation_conversion": "parse_or_convert_notation",
            "legal_move_check": "verify_move_legality",
            "board_interpretation": "understand_position",
            "opening_guidance": "get_opening_guidance",
            "explain_previous_move": "understand_previous_move",
            "tactical_puzzle": "solve_tactical_position",
            "positional_analysis": "evaluate_positional_features",
            "endgame_analysis": "analyze_endgame",
            "special_move": "understand_special_move",
            "draw_rules": "check_draw_conditions",
            "terminal_state": "identify_game_result",
            "best_move": "request_engine_checked_move",
            "move_comparison": "compare_candidate_moves",
            "mistake_diagnosis": "diagnose_mistake",
            "continuation_speculation": "explore_continuation",
            "hint": "request_progressive_hint",
            "guided_solving": "solve_with_guidance",
            "teaching": "learn_chess_concept",
            "opponent_response_prediction": "predict_opponent_reply",
            "post_game_review": "review_game",
            "general_chat": "general_chess_help",
        }.get(task_category, task_category)
        return f"{intent}_{edge_case}" if edge_case != "none" else intent

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = [
            "Use only the supplied FEN or validated game history as the position source.",
            "Preserve side to move, castling rights, en passant target, and move counters.",
            "Distinguish a legal move from a strategically strong move.",
            "Describe engine scores as estimates, not absolute truth.",
        ]
        if task_category in ENGINE_CATEGORIES:
            constraints.append("Do not claim a best move, forced tactic, or evaluation unless engine verification is available in tool evidence.")
        if task_category in {"hint", "guided_solving"}:
            constraints.append("Give progressive guidance before revealing a complete continuation.")
        if edge_case != "none":
            constraints.append("Identify the input problem explicitly and never infer a complete board from missing information.")
        if tool_usage != "none":
            constraints.append(f"Use the supplied {tool_usage} evidence purposefully.")
        return constraints


class ChessValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors: list[str] = []
        if output.get("conversation_type") != scenario.conversation_type:
            errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category:
            errors.append("category does not match scenario task_category")
        if output.get("board") != puzzle.rendered_board:
            errors.append("output position/history does not match selected Chess input")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip():
                errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip():
                errors.append("single-turn response is empty")
        else:
            messages = output.get("messages")
            if not isinstance(messages, list) or not messages:
                errors.append("multi-turn messages list is empty")
            else:
                if len(messages) > scenario.num_turns:
                    errors.append("multi-turn output exceeds requested turn count")
                for index, message in enumerate(messages):
                    if not str(message.get("user", "")).strip():
                        errors.append(f"message {index} is missing user text")
                    if not str(message.get("response", "")).strip():
                        errors.append(f"message {index} is missing response text")
        truth = puzzle.ground_truth
        if scenario.edge_case == "none":
            if truth.get("validity_status") != "valid" or not truth.get("current_fen"):
                errors.append("standard Chess scenario does not have a verified complete position")
        elif truth.get("validity_status") == "valid":
            errors.append("Chess edge-case scenario unexpectedly has valid ground truth")
        required = puzzle.metadata.get("tools_required", [])
        used = puzzle.metadata.get("tools_used", [])
        if any(tool not in used for tool in required):
            errors.append("required Chess verification tool was not recorded")
        return ValidationResult(is_valid=not errors, errors=errors)


class ChessDomainAdapter:
    name = "chess"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = ChessScenarioGenerator(config)
        self.puzzle_manager = ChessProblemManager(config)
        self.toolkit: ChessToolkit = self.puzzle_manager.toolkit
        self.validator = ChessValidator()

    def generate_scenario(self, **kwargs: Any) -> Scenario:
        return self.scenario_generator.generate(**kwargs)

    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index)
        scenario.metadata.update({
            "input_type": puzzle.metadata.get("input_type"),
            "notation_format": puzzle.metadata.get("notation_format"),
            "side_to_move": puzzle.metadata.get("side_to_move"),
            "lineage_id": puzzle.metadata.get("lineage_id"),
            "dataset_split": puzzle.metadata.get("dataset_split"),
        })
        return puzzle

    def mark_problem_used(self, puzzle: PuzzleRecord) -> None:
        self.puzzle_manager.mark_used(puzzle)

    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        required = self._required_tools(scenario, puzzle)
        calls = [self._run_tool(name, scenario, puzzle) for name in required]
        used = [call["tool_name"] for call in calls]
        verified = all(call.get("output", {}).get("verified", call.get("output", {}).get("valid", True)) for call in calls)
        if not calls:
            verification_status = "not_required"
        elif verified:
            verification_status = "verified"
        else:
            verification_status = "partial_unavailable"
        puzzle.metadata.update({"tools_required": required, "tools_used": used, "verification_status": verification_status})
        scenario.metadata.update({"tools_required": required, "tools_used": used, "verification_status": verification_status})
        if not calls:
            return {"used": False, "tool_name": None, "tool_input": None, "tool_output": None, "reason": None, "calls": [], "verification_status": verification_status}
        primary = calls[0]
        return {
            "used": True,
            "tool_name": primary["tool_name"],
            "tool_input": primary["input"],
            "tool_output": primary["output"],
            "reason": primary["reason"],
            "calls": calls,
            "verification_status": verification_status,
        }

    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        return {
            "puzzle_id": puzzle.puzzle_id,
            "input": json.loads(puzzle.puzzle),
            "difficulty": puzzle.difficulty,
            "input_type": puzzle.metadata.get("input_type"),
            "notation_format": puzzle.metadata.get("notation_format"),
            "side_to_move": puzzle.metadata.get("side_to_move"),
            "lineage_id": puzzle.metadata.get("lineage_id"),
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {
            "validity_status": truth.get("validity_status"),
            "verification_status": truth.get("verification_status"),
            "current_fen": truth.get("current_fen"),
            "fen_fields": truth.get("fen_fields"),
            "side_to_move": truth.get("side_to_move"),
            "position_status": truth.get("position_status"),
            "validated_continuation": truth.get("validated_continuation", []),
            "errors": truth.get("errors", []),
        }

    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        calls = []
        for call in tool_usage.get("calls", []):
            output = dict(call.get("output") or {})
            if "moves" in output and isinstance(output["moves"], list):
                output["moves"] = output["moves"][:40]
            if "squares" in output and isinstance(output["squares"], dict):
                output["squares"] = dict(list(output["squares"].items())[:20])
            calls.append({**call, "output": output})
        return {
            "domain": self.name,
            "tools_required": puzzle.metadata.get("tools_required", []),
            "tools_used": puzzle.metadata.get("tools_used", []),
            "verification_status": tool_usage.get("verification_status"),
            "calls": calls,
        }

    def generation_guidance(self) -> str:
        return (
            "Ground every objective Chess claim in the supplied tool evidence. Do not reconstruct a board from an incomplete move fragment. "
            "Use only legal SAN/PGN continuations from the validated state trace. In multi-turn exchanges, follow fen_before/fen_after exactly after each played move. "
            "If engine or tablebase evidence is unavailable, say the requested claim is unverified and offer non-engine teaching instead of inventing a result."
        )

    def sample_metadata(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "input_type": puzzle.metadata.get("input_type"),
            "conversation_type": scenario.conversation_type,
            "category": scenario.task_category,
            "difficulty": scenario.difficulty,
            "side_to_move": puzzle.metadata.get("side_to_move"),
            "notation_format": puzzle.metadata.get("notation_format"),
            "tools_required": puzzle.metadata.get("tools_required", []),
            "tools_used": puzzle.metadata.get("tools_used", []),
            "verification_status": tool_usage.get("verification_status"),
            "lineage_id": puzzle.metadata.get("lineage_id"),
            "dataset_split": puzzle.metadata.get("dataset_split"),
        }

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("metadata", {})
        row.update({
            "domain": self.name,
            "input_type": metadata.get("input_type"),
            "side_to_move": metadata.get("side_to_move"),
            "notation_format": metadata.get("notation_format"),
            "tools_required": metadata.get("tools_required", []),
            "tools_used": metadata.get("tools_used", []),
            "verification_status": metadata.get("verification_status"),
            "lineage_id": metadata.get("lineage_id"),
            "dataset_split": metadata.get("dataset_split"),
            "tool_used": sample.get("tool_used", False),
            "tool_name": sample.get("tool_usage_details", {}).get("tool_name"),
        })
        return row

    def _required_tools(self, scenario: Scenario, puzzle: PuzzleRecord) -> list[str]:
        if scenario.edge_case != "none":
            return ["chess_parse_validate"]
        category = scenario.task_category
        tools: list[str] = []
        if puzzle.metadata.get("input_type") == "game_history" and category in OBJECTIVE_CATEGORIES | {"opening_guidance"}:
            tools.append("chess_reconstruct_position")
        elif category in OBJECTIVE_CATEGORIES:
            tools.append("chess_parse_validate")
        if category in {"notation_conversion", "legal_move_check", "special_move", "continuation_speculation", "guided_solving"}:
            tools.append("chess_legal_moves")
        if category == "notation_conversion":
            tools.append("chess_move_conversion")
        if category == "special_move":
            tools.append("chess_move_application")
        if category in {"mistake_diagnosis", "post_game_review"} and puzzle.ground_truth.get("moves_san"):
            tools.append("chess_move_undo")
        if category in {"board_interpretation", "positional_analysis", "tactical_puzzle", "opening_guidance", "explain_previous_move"}:
            tools.append("chess_tactical_inspection")
        if category in {"draw_rules", "terminal_state", "endgame_analysis", "special_move"}:
            tools.append("chess_position_status")
        if category == "endgame_analysis" and self._piece_count(puzzle) <= 7:
            tools.append("chess_tablebase_lookup")
        if category in ENGINE_CATEGORIES:
            tools.append("chess_engine_analysis")
        if scenario.conversation_type == "multi_turn" and puzzle.ground_truth.get("current_fen"):
            tools.append("chess_state_trace")
        if scenario.tool_usage == "none" and category not in OBJECTIVE_CATEGORIES and category not in {"opening_guidance"}:
            return []
        return list(dict.fromkeys(tools))

    def _run_tool(self, name: str, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        fen = truth.get("current_fen")
        payload = json.loads(puzzle.puzzle)
        reason = f"The {scenario.task_category} request needs deterministic {name.removeprefix('chess_').replace('_', ' ')} evidence."
        if name == "chess_parse_validate":
            if payload.get("input_type") == "game_history" and payload.get("pgn"):
                tool_input, output = {"pgn": payload["pgn"]}, self.toolkit.parse_pgn(payload["pgn"])
            elif payload.get("fen"):
                tool_input, output = {"fen": payload["fen"]}, self.toolkit.parse_fen(payload["fen"])
                claimed = payload.get("claimed_side_to_move")
                actual = output.get("fields", {}).get("side_to_move")
                if claimed and actual and claimed != actual:
                    output = {**output, "valid": False, "contradiction": {"claimed_side_to_move": claimed, "fen_side_to_move": actual}, "errors": ["Claimed side to move contradicts the FEN."]}
            elif payload.get("moves") and payload.get("initial_fen"):
                tool_input = {"initial_fen": payload.get("initial_fen"), "moves": payload["moves"], "notation_format": "san"}
                output = self.toolkit.reconstruct(initial_fen=payload.get("initial_fen") or "", moves=payload["moves"], notation_format="san")
            else:
                tool_input = payload
                output = {"valid": False, "verified": True, "errors": ["A complete initial position was not supplied; no board was inferred."]}
            output["verified"] = True
        elif name == "chess_reconstruct_position":
            tool_input, output = {"pgn": payload.get("pgn")}, self.toolkit.reconstruct(pgn=payload.get("pgn", ""))
        elif name == "chess_legal_moves":
            tool_input, output = {"fen": fen}, self.toolkit.legal_moves(fen)
        elif name == "chess_tactical_inspection":
            tool_input, output = {"fen": fen}, self.toolkit.inspect(fen)
        elif name == "chess_position_status":
            if payload.get("pgn"):
                tool_input, output = {"pgn": payload["pgn"]}, self.toolkit.history_status(pgn=payload["pgn"])
            else:
                tool_input, output = {"fen": fen}, self.toolkit.position_status(fen)
        elif name == "chess_move_conversion":
            first = (truth.get("validated_continuation") or [None])[0]
            tool_input = {"fen": fen, "notation": first.get("uci") if first else None, "from_format": "uci"}
            output = self.toolkit.convert_move(fen, first["uci"], "uci") if first else {"valid": False, "verified": True, "reason": "No legal move is available to convert."}
        elif name == "chess_move_application":
            first = (truth.get("validated_continuation") or [None])[0]
            tool_input = {"fen": fen, "notation": first.get("san") if first else None, "notation_format": "san"}
            output = self.toolkit.apply_move(fen, first["san"], "san") if first else {"valid": False, "verified": True, "reason": "No legal move is available to apply."}
        elif name == "chess_move_undo":
            tool_input = {"initial_fen": truth.get("initial_fen"), "moves": truth.get("moves_san", []), "count": 1, "notation_format": "san"}
            output = self.toolkit.undo_moves(truth.get("initial_fen"), truth.get("moves_san", []), count=1, notation_format="san")
        elif name == "chess_engine_analysis":
            tool_input, output = {"fen": fen, "multipv": 3}, self.toolkit.engine_analysis(fen, multipv=3)
        elif name == "chess_tablebase_lookup":
            tool_input, output = {"fen": fen}, self.toolkit.tablebase_lookup(fen)
        elif name == "chess_state_trace":
            tool_input = {"fen": fen, "plies": len(truth.get("validated_continuation", []))}
            output = {"valid": True, "verified": True, "state_trace": truth.get("validated_continuation", [])}
        else:
            tool_input, output = {}, {"valid": False, "verified": False, "errors": ["Unknown tool"]}
        return {"tool_name": name, "input": tool_input, "output": output, "reason": reason}

    @staticmethod
    def _piece_count(puzzle: PuzzleRecord) -> int:
        fen = puzzle.ground_truth.get("current_fen")
        return sum(1 for char in (fen or "").split(" ", 1)[0] if char.isalpha())
