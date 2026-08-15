from __future__ import annotations

from src.generator.config import get_config
from src.generator.domains import get_domain_adapter
from src.generator.models import Scenario
from src.generator.othello import (
    apply_move,
    flips_for_move,
    initial_board,
    legal_moves,
    parse_state,
    parse_square,
    search_position,
    status,
    transform_board,
)


def _scenario(task: str = "next_best_move", edge: str = "none", difficulty: str = "easy") -> Scenario:
    return Scenario("othello-test", task, "single_turn", 1, "advanced", "analytical", "coaching", "neutral", difficulty, task, edge, "none")


def test_opening_moves_and_move_application_are_exact():
    board = initial_board()
    assert set(legal_moves(board, "B")) == {"d3", "c4", "f5", "e6"}
    next_board, side, passes, flipped = apply_move(board, "B", "d3")
    assert flipped == ["d4"]
    assert next_board[parse_square("d3")] == "B"
    assert next_board[parse_square("d4")] == "B"
    assert side == "W" and passes == 0


def test_move_can_flip_in_all_eight_directions():
    cells = ["."] * 64
    center = (3, 3)
    for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
        cells[(center[0] + dr) * 8 + center[1] + dc] = "W"
        cells[(center[0] + 2 * dr) * 8 + center[1] + 2 * dc] = "B"
    assert len(flips_for_move("".join(cells), "B", center[0] * 8 + center[1])) == 8


def test_symmetries_and_search_are_deterministic():
    board = initial_board()
    assert transform_board(transform_board(board, "rotate_180"), "rotate_180") == board
    assert search_position(board, "B", 2) == search_position(board, "B", 2)
    assert search_position(board, "B", 2)["exact"] is False


def test_forced_pass_terminal_edges_and_ten_tool_contract(tmp_path):
    config = get_config(); config["output_path"] = str(tmp_path)
    adapter = get_domain_adapter("othello", config)
    assert len(adapter.tool_catalog) == 10
    for edge, expected in (("forced_pass", "forced_pass"), ("terminal_position", "terminal")):
        puzzle = adapter.select_problem(_scenario(edge=edge), len(adapter.puzzle_manager.base_bank) + len(edge))
        board_status = puzzle.ground_truth["position_status"]
        assert board_status[expected]
        usage = adapter.maybe_use_tool(_scenario(edge=edge), puzzle)
        assert len(usage["calls"]) >= 2
        assert all(call["output"]["verified"] for call in usage["calls"])


def test_expert_endgame_is_labelled_exact(tmp_path):
    config = get_config(); config["output_path"] = str(tmp_path)
    adapter = get_domain_adapter("othello", config)
    puzzle = adapter.select_problem(_scenario(task="endgame_analysis", difficulty="expert"), 0)
    assert puzzle.metadata["empty_count"] <= 10
    assert puzzle.ground_truth["analysis"]["exact"]
    assert status(*parse_state(puzzle.puzzle))["terminal"] or puzzle.ground_truth["analysis"]["best_move"]
