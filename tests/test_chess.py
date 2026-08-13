import csv
import json

import chess
import pytest

from src.generator.chess import ChessProblemManager, ChessToolkit, complexity_band, split_for_lineage
from src.generator.config import resolve_domain_config
from src.generator.domains.chess import ChessDomainAdapter
from src.generator.generator import ConversationGenerator
from src.generator.models import Scenario


def chess_config(tmp_path):
    return {
        "domain": "chess",
        "output_path": str(tmp_path),
        "output_dir": str(tmp_path),
        "max_turns": 4,
        "generation": {"random_seed": 29, "max_regeneration_attempts": 2, "max_prompt_characters": 100_000},
        "model": {"model_name": "test-model"},
        "storage": {"metadata_filename": "samples.jsonl", "rejected_filename": "rejected.jsonl", "stats_filename": "stats.json"},
        "puzzles": {"persistent_bank_filename": "chess_bank.jsonl", "usage_stats_filename": "chess_usage.json", "lineage_count": 16, "snapshots_per_lineage": 4, "catalog_version": "test-v2"},
        "similarity": {"ngram_overlap_threshold": 1.1, "embedding_similarity_threshold": 1.1, "structural_similarity_threshold": 1.1, "scenario_similarity_threshold": 1.1, "puzzle_similarity_threshold": 1.1},
        "scenario": {
            "task_categories": ["board_interpretation"],
            "difficulty_levels": ["easy"],
            "user_expertise_levels": ["intermediate"],
            "user_personalities": ["analytical"],
            "assistant_styles": ["explanatory"],
            "tones": ["neutral"],
            "edge_cases": ["none"],
            "tool_usage_modes": ["tactical_inspection"],
        },
        "prompts": {"system_prompt": "chess system", "single_turn_prompt": "single", "multi_turn_prompt": "multi {max_turns}"},
        "chess": {"engine_path": None, "engine_auto_install": False, "tablebase_online": False, "verification_backends_required": False},
    }


def scenario(category="legal_move_check", edge="none", conversation_type="single_turn"):
    return Scenario(
        "chess-test", category, conversation_type, 1 if conversation_type == "single_turn" else 3,
        "intermediate", "analytical", "coaching", "neutral", "easy", category, edge, "legal_moves",
    )


def test_fen_parser_preserves_all_state_fields_and_en_passant():
    toolkit = ChessToolkit()
    fen = "rnbqkbnr/1pp1pppp/p7/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"

    parsed = toolkit.parse_fen(fen)
    move = toolkit.apply_move(fen, "exd6", "san")

    assert parsed["valid"] is True
    assert parsed["fen"] == fen
    assert parsed["fields"] == {
        "piece_placement": fen.split()[0],
        "side_to_move": "white",
        "castling_rights": "KQkq",
        "en_passant_target": "d6",
        "halfmove_clock": 0,
        "fullmove_number": 3,
    }
    assert move["is_en_passant"] is True
    assert move["uci"] == "e5d6"
    assert move["fen_after"].split()[1:] == ["b", "KQkq", "-", "0", "3"]


def test_fen_parser_rejects_every_incomplete_prefix_without_crashing():
    toolkit = ChessToolkit()
    complete = "8/8/8/8/8/8/4K3/7k w - - 0 1"
    parts = complete.split()

    for field_count in range(1, 6):
        incomplete = " ".join(parts[:field_count])
        parsed = toolkit.parse_fen(incomplete)
        assert parsed["valid"] is False
        assert parsed["provided_field_count"] == field_count
        assert "exactly 6 fields" in parsed["errors"][0]

    malformed = toolkit.parse_fen("8/8/8 broken")
    assert malformed["valid"] is False
    assert malformed["provided_field_count"] == 2


def test_pgn_san_uci_reconstruction_conversion_and_undo_are_consistent():
    toolkit = ChessToolkit()
    pgn = """[Event \"Synthetic\"]\n[Result \"*\"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *"""

    parsed = toolkit.parse_pgn(pgn)
    rebuilt = toolkit.reconstruct(pgn=pgn)
    converted = toolkit.convert_move(rebuilt["state_trace"][2]["fen"], "g1f3", "uci")
    undone = toolkit.undo_moves(chess.STARTING_FEN, ["e4", "e5", "Nf3"], count=1)

    assert parsed["valid"] is True
    assert [move["san"] for move in parsed["moves"]] == ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    assert rebuilt["final_fen"] == parsed["final_fen"]
    assert converted == {"valid": True, "san": "Nf3", "uci": "g1f3", "is_capture": False, "is_castling": False, "is_en_passant": False, "promotion": None}
    assert undone["fen"] == rebuilt["state_trace"][2]["fen"]
    assert toolkit.parse_move(chess.STARTING_FEN, "e2e5", "uci")["valid"] is False


def test_terminal_draw_repetition_and_insufficient_material_detection():
    toolkit = ChessToolkit()
    mate = toolkit.position_status("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
    stale = toolkit.position_status("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    insufficient = toolkit.position_status("8/8/8/8/8/8/4K3/7k w - - 0 1")
    repetition = toolkit.history_status(moves=["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8"])

    assert mate["checkmate"] and mate["outcome"]["termination"] == "checkmate"
    assert stale["stalemate"] and stale["outcome"]["result"] == "1/2-1/2"
    assert insufficient["insufficient_material"]
    assert repetition["can_claim_threefold_repetition"]


def test_inspection_reports_attacks_defenders_pins_checks_captures_and_material():
    toolkit = ChessToolkit()
    result = toolkit.inspect("4k3/4r3/8/8/8/8/4R3/4K3 w - - 0 1", squares=["e2", "e1"])

    assert result["valid"] is True
    assert result["squares"]["e2"]["white_piece_pinned"] is True
    assert "e7" in result["squares"]["e2"]["attacked_by_black"]
    assert "e1" in result["squares"]["e2"]["defenders"]
    assert isinstance(result["checks"], list) and isinstance(result["captures"], list)
    assert result["material"]["balance_white_minus_black"] == 0
    assert "fork_candidates" in result["tactical_features"]


def test_programmatic_bank_is_legal_deterministic_and_split_by_lineage(tmp_path):
    first = ChessProblemManager(chess_config(tmp_path / "one"))
    second = ChessProblemManager(chess_config(tmp_path / "two"))

    assert [(row.canonical_signature, row.solution) for row in first.base_bank] == [(row.canonical_signature, row.solution) for row in second.base_bank]
    assert len(first.base_bank) >= 64
    assert {row.metadata["catalog_version"] for row in first.base_bank} == {"test-v2"}
    assert all("complexity_score" in row.metadata and "position_phase" in row.metadata for row in first.base_bank)
    for record in first.base_bank:
        assert ChessToolkit().parse_fen(record.solution)["valid"] is True
        assert record.metadata["dataset_split"] == split_for_lineage(record.metadata["lineage_id"])
    for lineage in first.lineages:
        descendants = [row for row in first.base_bank if row.metadata["lineage_id"] == lineage.lineage_id]
        assert {row.metadata["dataset_split"] for row in descendants} == {split_for_lineage(lineage.lineage_id)}
        assert {row.metadata["input_type"] for row in descendants} == {"fen", "game_history"}


def test_incomplete_fragment_is_not_reconstructed_and_edge_is_verified(tmp_path):
    adapter = ChessDomainAdapter(chess_config(tmp_path))
    selected = adapter.select_problem(scenario(edge="ambiguous_input"), 0)
    usage = adapter.maybe_use_tool(scenario(edge="ambiguous_input"), selected)

    assert selected.ground_truth["current_fen"] is None
    assert selected.ground_truth["validity_status"] == "incomplete"
    assert usage["calls"][0]["output"]["valid"] is False
    assert "no board was inferred" in usage["calls"][0]["output"]["errors"][0].lower()
    assert usage["verification_status"] == "verified"


def test_adapter_records_purposeful_tools_state_trace_and_unavailable_engine(tmp_path):
    adapter = ChessDomainAdapter(chess_config(tmp_path))
    selected_scenario = scenario(category="best_move", conversation_type="multi_turn")
    selected = adapter.select_problem(selected_scenario, 1)
    usage = adapter.maybe_use_tool(selected_scenario, selected)
    names = [call["tool_name"] for call in usage["calls"]]

    assert names == ["chess_parse_validate", "chess_legal_moves", "chess_engine_analysis", "chess_state_trace"]
    assert usage["calls"][2]["output"]["available"] is False
    assert usage["verification_status"] == "partial_unavailable"
    trace = usage["calls"][3]["output"]["state_trace"]
    board = chess.Board(selected.ground_truth["current_fen"])
    for row in trace:
        assert row["fen_before"] == board.fen(en_passant="fen")
        move = board.parse_san(row["san"])
        assert move.uci() == row["uci"]
        board.push(move)
        assert row["fen_after"] == board.fen(en_passant="fen")
    assert selected.metadata["tools_required"] == names
    assert selected.metadata["tools_used"] == names


def test_special_move_endgame_and_repetition_scenarios_select_applicable_positions(tmp_path):
    adapter = ChessDomainAdapter(chess_config(tmp_path))
    castle_scenario = scenario(category="special_move")
    castle_scenario.metadata["rules_theme"] = "castling"
    castle = adapter.select_problem(castle_scenario, 1)
    castle_usage = adapter.maybe_use_tool(castle_scenario, castle)
    application = next(call for call in castle_usage["calls"] if call["tool_name"] == "chess_move_application")
    assert application["output"]["is_castling"] is True

    endgame = adapter.select_problem(scenario(category="endgame_analysis"), 3)
    endgame_usage = adapter.maybe_use_tool(scenario(category="endgame_analysis"), endgame)
    tablebase = next(call for call in endgame_usage["calls"] if call["tool_name"] == "chess_tablebase_lookup")
    assert endgame.metadata["piece_count"] <= 7
    assert tablebase["output"]["applicable"] is True

    repetition_scenario = scenario(category="draw_rules")
    repetition_scenario.metadata["rules_theme"] = "repetition"
    repetition = adapter.select_problem(repetition_scenario, 0)
    repetition_usage = adapter.maybe_use_tool(repetition_scenario, repetition)
    status = next(call for call in repetition_usage["calls"] if call["tool_name"] == "chess_position_status")
    assert status["output"]["can_claim_threefold_repetition"] is True


def test_required_engine_verification_fails_closed_before_generation(tmp_path):
    config = chess_config(tmp_path)
    config["chess"]["verification_backends_required"] = True
    adapter = ChessDomainAdapter(config)
    selected_scenario = scenario(category="best_move")
    selected = adapter.select_problem(selected_scenario, 1)

    with pytest.raises(RuntimeError, match="Required Chess verification backend failed before model generation"):
        adapter.maybe_use_tool(selected_scenario, selected)


class ChessPromptModel:
    def generate(self, prompt):
        assert "Domain: chess" in prompt
        assert "castling_rights" in prompt
        return json.dumps({"prompt": "What does this position say?", "response": "I will interpret only the supplied FEN and verified inspection."})


def test_end_to_end_chess_generation_preserves_schema_and_explicit_metadata(tmp_path):
    generator = ConversationGenerator(chess_config(tmp_path))
    generator.model_client = ChessPromptModel()

    result = generator.run(samples=1, conversation_type="single_turn", job_name="chess-job")
    sample = json.loads((tmp_path / "chess-job" / "samples.jsonl").read_text(encoding="utf-8").strip())

    assert result["domain"] == "chess"
    assert sample["domain"] == "chess"
    assert sample["tool_usage_details"]["calls"]
    for key in ("input_type", "conversation_type", "category", "difficulty", "side_to_move", "notation_format", "tools_required", "tools_used", "verification_status", "lineage_id", "dataset_split", "complexity_band", "tool_bundle_signature"):
        assert key in sample["metadata"]
    assert sample["metadata"]["difficulty"] == complexity_band(sample["metadata"]["complexity_score"])
    assert len(sample["metadata"]["tools_used"]) == len(set(sample["metadata"]["tools_used"]))
    with (tmp_path / "chess-job" / "single_turn.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["domain"] == "chess"
    assert row["dataset_split"] in {"train", "validation", "test"}
    assert int(row["tool_count"]) >= 2
    assert row["tool_names"]


def test_hundred_record_selection_audit_has_exact_quota_and_unique_states(tmp_path):
    config = chess_config(tmp_path)
    config["puzzles"]["lineage_count"] = 64
    adapter = ChessDomainAdapter(config)
    rows = []
    for record_slot in range(100):
        conversation_type = "multi_turn" if record_slot % 2 else "single_turn"
        dataset_index = record_slot // 2
        scenario_index = dataset_index * config["generation"]["max_regeneration_attempts"]
        selected_scenario = adapter.generate_scenario(
            sample_index=scenario_index,
            conversation_type=conversation_type,
            max_turns=6,
            distribution_stats={},
        )
        selected = adapter.select_problem(selected_scenario, scenario_index)
        rows.append((selected_scenario, selected))

    assert len({puzzle.puzzle for _, puzzle in rows}) == 100
    assert len({puzzle.metadata["canonical_state_signature"] for _, puzzle in rows}) == 100
    assert sum(selected_scenario.edge_case != "none" for selected_scenario, _ in rows) == 15
    assert {puzzle.difficulty for _, puzzle in rows} == {"easy", "medium", "hard", "expert"}
    assert all(puzzle.difficulty == complexity_band(puzzle.metadata["complexity_score"]) for _, puzzle in rows)
    assert {puzzle.metadata["input_type"] for _, puzzle in rows} >= {"fen", "game_history"}
    rendered = {puzzle.rendered_board for _, puzzle in rows}
    assert "Moves: 1. e4 e5 2. Ke3" not in rendered
    assert "FEN: 8/8/8 broken" not in rendered
    assert "FEN: 8/8/8/8/8/8/8/8 w - - 0 1" not in rendered
    assert "Move fragment: Nf3\nPosition: unspecified" not in rendered


def test_exhausted_catalog_extends_deterministically_without_reuse(tmp_path):
    config = chess_config(tmp_path)
    config["puzzles"]["lineage_count"] = 8
    manager = ChessProblemManager(config)
    original_count = len(manager.base_bank)
    manager.reserved_states.update(
        record.metadata["canonical_state_signature"] for record in manager.base_bank
    )

    selected = manager.select_puzzle(scenario(category="board_interpretation"), 0)

    assert len(manager.base_bank) > original_count
    assert selected.metadata["canonical_state_signature"] not in {
        record.metadata["canonical_state_signature"] for record in manager.base_bank[:original_count]
    }
    assert sum(1 for _ in (tmp_path / "chess_bank.jsonl").open(encoding="utf-8")) == len(manager.base_bank)


def test_every_category_routes_at_least_two_purposeful_tools(tmp_path):
    adapter = ChessDomainAdapter(chess_config(tmp_path))
    categories = [
        "rules_explanation", "general_chat", "teaching", "notation_conversion", "legal_move_check",
        "board_interpretation", "opening_guidance", "explain_previous_move", "tactical_puzzle", "hint",
        "guided_solving", "positional_analysis", "endgame_analysis", "special_move", "draw_rules",
        "terminal_state", "best_move", "move_comparison", "mistake_diagnosis", "continuation_speculation",
        "opponent_response_prediction", "post_game_review",
    ]
    for index, category in enumerate(categories):
        selected_scenario = scenario(category=category)
        selected = adapter.select_problem(selected_scenario, index)
        names = adapter._required_tools(selected_scenario, selected)
        assert len(names) >= 2, category
        assert len(names) == len(set(names)), category
        if category in {"rules_explanation", "general_chat", "teaching", "opening_guidance", "positional_analysis"}:
            assert "chess_engine_analysis" not in names


def test_chess_normalizes_excess_model_turns_before_validation(tmp_path):
    adapter = ChessDomainAdapter(chess_config(tmp_path))
    selected_scenario = scenario(category="teaching", conversation_type="multi_turn")
    selected_scenario.num_turns = 2
    selected = adapter.select_problem(selected_scenario, 0)
    output = {
        "conversation_type": "multi_turn",
        "category": "teaching",
        "board": selected.rendered_board,
        "messages": [
            {"user": "one", "response": "one"},
            {"user": "two", "response": "two"},
            {"user": "three", "response": "three"},
        ],
    }

    normalized = adapter.normalize_output(output, selected_scenario, selected)

    assert len(normalized["messages"]) == 2
