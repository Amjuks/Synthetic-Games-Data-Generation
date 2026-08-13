from __future__ import annotations

import hashlib
import json
from typing import Any

import chess

from ..chess import ChessProblemManager, ChessToolkit, complexity_band
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
        max_attempts = max(1, int(self.config.get("generation", {}).get("max_regeneration_attempts", 3)))
        dataset_index = sample_index // max_attempts
        conversation_bit = 1 if kwargs.get("conversation_type") == "multi_turn" else 0
        record_slot = dataset_index * 2 + conversation_bit
        difficulty_levels = self.scenario_config.get("difficulty_levels", ["easy", "medium", "hard", "expert"])
        if set(difficulty_levels) >= {"easy", "medium", "hard", "expert"}:
            scenario.difficulty = ("easy", "medium", "hard", "expert")[record_slot % 4]
        edge_slots = {4, 11, 17}
        is_edge = record_slot % 20 in edge_slots
        if is_edge:
            edge_number = (record_slot // 20) * 3 + sorted(edge_slots).index(record_slot % 20)
            edge_types = (
                "illegal_move",
                "malformed_input",
                "invalid_position",
                "ambiguous_input",
                "contradictory_input",
                "incomplete_input",
            )
            edge_tasks = (
                "legal_move_check",
                "board_interpretation",
                "mistake_diagnosis",
                "notation_conversion",
                "post_game_review",
                "explain_previous_move",
            )
            scenario.edge_case = edge_types[edge_number % len(edge_types)]
            scenario.task_category = edge_tasks[edge_number % len(edge_tasks)]
        else:
            scenario.edge_case = "none"
        scenario.user_intent = self._derive_user_intent(scenario.task_category, scenario.edge_case)
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
        scenario.metadata.update({
            "dataset_sample_index": dataset_index,
            "record_slot": record_slot,
            "edge_quota_window": record_slot // 20,
        })
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
        scenario.scenario_id = hashlib.sha1(
            "|".join(
                [
                    scenario.conversation_type,
                    scenario.task_category,
                    scenario.difficulty,
                    scenario.user_expertise,
                    scenario.user_personality,
                    scenario.assistant_style,
                    scenario.tone,
                    scenario.edge_case,
                    scenario.tool_usage,
                    str(record_slot),
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]
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
        if required != used:
            errors.append("required and used Chess tool sequences do not match exactly")
        if len(used) < 2:
            errors.append("Chess samples require at least two purposeful tool calls")
        if len(set(used)) != len(used):
            errors.append("Chess tool bundles must contain distinct tools")
        score = puzzle.metadata.get("complexity_score")
        if score is None:
            errors.append("Chess sample is missing a measured complexity score")
        elif complexity_band(int(score)) != puzzle.difficulty:
            errors.append("Chess difficulty does not match the measured complexity band")
        if puzzle.metadata.get("complexity_band", puzzle.difficulty) != puzzle.difficulty:
            errors.append("Chess complexity_band metadata does not match puzzle difficulty")
        calls = puzzle.metadata.get("tool_calls", [])
        if len(calls) != len(used):
            errors.append("recorded Chess tool call count does not match tools_used")
        for index, call in enumerate(calls):
            result = call.get("output", {})
            completed = result.get("verified", result.get("valid", False))
            if not completed:
                errors.append(f"Chess tool call {index} did not complete verification")
        if scenario.conversation_type == "multi_turn":
            trace_calls = [call for call in calls if call.get("tool_name") == "chess_state_trace"]
            for call in trace_calls:
                previous = puzzle.ground_truth.get("current_fen")
                for row in call.get("output", {}).get("state_trace", []):
                    if row.get("fen_before") != previous:
                        errors.append("Chess state trace has a discontinuous fen_before")
                        break
                    previous = row.get("fen_after")
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
        self.require_verification_backends = bool(config.get("chess", {}).get("verification_backends_required", True))

    def prepare_job(self, *, job_dir: Any, accepted_history: list[dict[str, Any]], rejected_history: list[dict[str, Any]]) -> None:
        self.puzzle_manager.reserve_history([*accepted_history, *rejected_history])

    def generate_scenario(self, **kwargs: Any) -> Scenario:
        return self.scenario_generator.generate(**kwargs)

    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index)
        scenario.metadata["requested_difficulty"] = scenario.difficulty
        scenario.difficulty = puzzle.difficulty
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
        bundle_signature = hashlib.sha256("|".join(used).encode("utf-8")).hexdigest()
        puzzle.metadata.update({"tools_required": required, "tools_used": used, "tool_count": len(calls), "tool_calls": calls, "tool_bundle_signature": bundle_signature, "verification_status": verification_status})
        scenario.metadata.update({"tools_required": required, "tools_used": used, "verification_status": verification_status})
        unavailable_backends = [
            call for call in calls
            if call["tool_name"] in {"chess_engine_analysis", "chess_tablebase_lookup"}
            and not call.get("output", {}).get("verified")
        ]
        if self.require_verification_backends and unavailable_backends:
            details = "; ".join(
                f"{call['tool_name']}: {call.get('output', {}).get('reason', 'verification failed')}"
                for call in unavailable_backends
            )
            raise RuntimeError(f"Required Chess verification backend failed before model generation: {details}")
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

    def normalize_output(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        if scenario.conversation_type == "multi_turn" and isinstance(output.get("messages"), list):
            output = {**output, "messages": output["messages"][: scenario.num_turns]}
        return output

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
            "position_phase": puzzle.metadata.get("position_phase"),
            "complexity_score": puzzle.metadata.get("complexity_score"),
            "complexity_band": puzzle.metadata.get("complexity_band", puzzle.difficulty),
            "motif_theme": puzzle.metadata.get("motif_theme"),
            "canonical_state_signature": puzzle.metadata.get("canonical_state_signature"),
            "edge_mutation_type": puzzle.metadata.get("edge_mutation_type"),
            "tool_count": puzzle.metadata.get("tool_count", 0),
            "tool_bundle_signature": puzzle.metadata.get("tool_bundle_signature"),
            "catalog_version": puzzle.metadata.get("catalog_version"),
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
            "position_phase": metadata.get("position_phase"),
            "complexity_score": metadata.get("complexity_score"),
            "complexity_band": metadata.get("complexity_band"),
            "motif_theme": metadata.get("motif_theme"),
            "canonical_state_signature": metadata.get("canonical_state_signature"),
            "edge_mutation_type": metadata.get("edge_mutation_type"),
            "catalog_version": metadata.get("catalog_version"),
            "tool_count": metadata.get("tool_count", 0),
            "tool_bundle_signature": metadata.get("tool_bundle_signature"),
            "tool_names": metadata.get("tools_used", []),
            "tool_calls": sample.get("tool_usage_details", {}).get("calls", []),
            "tool_used": sample.get("tool_used", False),
            "tool_name": sample.get("tool_usage_details", {}).get("tool_name"),
        })
        return row

    def _required_tools(self, scenario: Scenario, puzzle: PuzzleRecord) -> list[str]:
        if scenario.edge_case != "none":
            tools = ["chess_parse_validate", "chess_input_diagnosis"]
            if scenario.edge_case == "illegal_move":
                tools.append("chess_legal_alternatives")
            elif scenario.edge_case == "ambiguous_input":
                tools.append("chess_legal_example")
            else:
                tools.append("chess_rules_reference")
            return tools
        category = scenario.task_category
        source = "chess_reconstruct_position" if puzzle.metadata.get("input_type") == "game_history" else "chess_parse_validate"
        bundles = {
            "rules_explanation": ["chess_rules_reference", "chess_legal_example"],
            "general_chat": ["chess_rules_reference", "chess_legal_example"],
            "teaching": [source, "chess_position_summary", "chess_positional_features"],
            "notation_conversion": [source, "chess_move_conversion"],
            "legal_move_check": [source, "chess_move_validation", "chess_legal_moves"],
            "board_interpretation": [source, "chess_position_summary", "chess_material_analysis", "chess_tactical_inspection"],
            "opening_guidance": [source, "chess_positional_features", "chess_legal_moves"],
            "explain_previous_move": [source, "chess_position_summary", "chess_move_undo"],
            "tactical_puzzle": [source, "chess_legal_moves", "chess_tactical_inspection", "chess_engine_analysis"],
            "hint": [source, "chess_legal_moves", "chess_tactical_inspection", "chess_engine_analysis"],
            "guided_solving": [source, "chess_legal_moves", "chess_tactical_inspection", "chess_engine_analysis"],
            "positional_analysis": [source, "chess_position_summary", "chess_material_analysis", "chess_positional_features"],
            "endgame_analysis": [source, "chess_position_status", "chess_material_analysis", "chess_tablebase_lookup" if self._piece_count(puzzle) <= 7 else "chess_engine_analysis"],
            "special_move": [source, "chess_move_validation", "chess_move_application", "chess_position_status"],
            "draw_rules": [source, "chess_position_status", "chess_repetition_history"],
            "terminal_state": [source, "chess_position_status", "chess_material_analysis"],
            "best_move": [source, "chess_legal_moves", "chess_engine_analysis"],
            "move_comparison": [source, "chess_legal_moves", "chess_engine_analysis"],
            "mistake_diagnosis": [source, "chess_move_undo", "chess_legal_moves", "chess_engine_analysis"],
            "continuation_speculation": [source, "chess_legal_moves", "chess_move_application"],
            "opponent_response_prediction": [source, "chess_legal_moves", "chess_engine_analysis"],
            "post_game_review": [source, "chess_move_undo", "chess_position_summary", "chess_engine_analysis"],
        }
        tools = list(bundles.get(category, [source, "chess_position_summary"]))
        if scenario.conversation_type == "multi_turn" and puzzle.ground_truth.get("current_fen"):
            tools.append("chess_state_trace")
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
        elif name == "chess_rules_reference":
            tool_input = {"category": scenario.task_category, "theme": scenario.metadata.get("rules_theme") or scenario.metadata.get("tactical_theme")}
            output = {"valid": True, "verified": True, "rules": ["Pieces move only when the resulting position is legal.", "Check must be answered immediately.", "Castling, en passant, promotion, repetition, and move counters depend on complete position state."], "engine_used": False}
        elif name == "chess_legal_example":
            example_board = chess.Board(fen) if fen else chess.Board()
            move = sorted(example_board.legal_moves, key=lambda item: item.uci())[0]
            san = example_board.san(move)
            before = example_board.fen(en_passant="fen")
            example_board.push(move)
            tool_input = {"fen": before}
            output = {"valid": True, "verified": True, "san": san, "uci": move.uci(), "fen_before": before, "fen_after": example_board.fen(en_passant="fen"), "engine_used": False}
        elif name == "chess_input_diagnosis":
            mutation = truth.get("mutation", {})
            tool_input = {"input": payload, "mutation": mutation}
            output = {"valid": True, "verified": True, "diagnosis": truth.get("errors", []), "mutation_type": mutation.get("type"), "base_fen": truth.get("base_fen"), "complete_board_inferred": False}
        elif name == "chess_legal_alternatives":
            prefix = truth.get("mutation", {}).get("valid_prefix", [])
            rebuilt = self.toolkit.reconstruct(initial_fen=payload.get("initial_fen", chess.STARTING_FEN), moves=prefix)
            alternatives = self.toolkit.legal_moves(rebuilt["final_fen"]) if rebuilt.get("valid") else {"valid": False, "moves": []}
            tool_input = {"valid_prefix": prefix, "failed_ply": truth.get("mutation", {}).get("failed_ply")}
            output = {"valid": True, "verified": True, "last_valid_fen": rebuilt.get("final_fen"), "legal_alternatives": alternatives.get("moves", [])[:20]}
        elif name == "chess_reconstruct_position":
            tool_input, output = {"pgn": payload.get("pgn")}, self.toolkit.reconstruct(pgn=payload.get("pgn", ""))
        elif name == "chess_legal_moves":
            tool_input, output = {"fen": fen}, self.toolkit.legal_moves(fen)
        elif name == "chess_move_validation":
            first = (truth.get("validated_continuation") or [None])[0]
            tool_input = {"fen": fen, "move": first.get("san") if first else None}
            output = self.toolkit.parse_move(fen, first["san"], "san") if first else {"valid": True, "verified": True, "terminal": True, "reason": "No move exists in a terminal position."}
            output["verified"] = True
        elif name == "chess_position_summary":
            status = self.toolkit.position_status(fen)
            tool_input = {"fen": fen}
            output = {"valid": True, "verified": True, "side_to_move": status.get("side_to_move"), "in_check": status.get("in_check"), "game_over": status.get("game_over"), "legal_move_count": self.toolkit.legal_moves(fen).get("count"), "position_phase": puzzle.metadata.get("position_phase"), "complexity_score": puzzle.metadata.get("complexity_score")}
        elif name == "chess_material_analysis":
            inspection = self.toolkit.inspect(fen, squares=[])
            tool_input = {"fen": fen}
            output = {"valid": True, "verified": True, "material": inspection.get("material"), "captures": inspection.get("captures", [])[:20]}
        elif name == "chess_positional_features":
            board = chess.Board(fen)
            developed = {color: sum(1 for square in chess.scan_forward(board.occupied_co[color]) if chess.square_rank(square) not in ({0, 1} if color else {6, 7})) for color in (chess.WHITE, chess.BLACK)}
            doubled = {}
            for color_name, color in (("white", chess.WHITE), ("black", chess.BLACK)):
                files = [chess.square_file(square) for square in board.pieces(chess.PAWN, color)]
                doubled[color_name] = [chess.FILE_NAMES[file] for file in range(8) if files.count(file) > 1]
            tool_input = {"fen": fen}
            output = {"valid": True, "verified": True, "developed_piece_counts": {"white": developed[chess.WHITE], "black": developed[chess.BLACK]}, "doubled_pawn_files": doubled, "castling_rights": fen.split()[2], "space_proxy": {"white": len(board.attacks(board.king(chess.WHITE))) if board.king(chess.WHITE) is not None else 0, "black": len(board.attacks(board.king(chess.BLACK))) if board.king(chess.BLACK) is not None else 0}, "engine_used": False}
        elif name == "chess_tactical_inspection":
            tool_input, output = {"fen": fen}, self.toolkit.inspect(fen)
        elif name == "chess_position_status":
            if payload.get("pgn"):
                tool_input, output = {"pgn": payload["pgn"]}, self.toolkit.history_status(pgn=payload["pgn"])
            else:
                tool_input, output = {"fen": fen}, self.toolkit.position_status(fen)
        elif name == "chess_repetition_history":
            if payload.get("pgn"):
                result = self.toolkit.history_status(pgn=payload["pgn"])
                source_input = {"pgn": payload["pgn"]}
            else:
                result = self.toolkit.position_status(fen)
                source_input = {"fen": fen}
            tool_input, output = source_input, {**result, "verified": True, "history_available": bool(payload.get("pgn"))}
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
