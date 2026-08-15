from __future__ import annotations

from src.generator.config import get_config
from src.generator.domains import get_domain_adapter
from src.generator.minesweeper import clue_at, constraint_analysis, reveal_safe
from src.generator.models import Scenario


def _scenario(task: str = "next_best_move", edge: str = "none", difficulty: str = "easy") -> Scenario:
    return Scenario("mines-test", task, "single_turn", 1, "advanced", "analytical", "coaching", "neutral", difficulty, task, edge, "none")


def test_clues_and_flood_reveal():
    mines = {(0, 0)}
    assert clue_at((1, 1), mines, 3, 3) == 1
    visible = [["#"] * 3 for _ in range(3)]
    reveal_safe(3, 3, mines, (2, 2), visible)
    assert visible[2][2] == "0"
    assert visible[1][1] == "1"
    assert visible[0][0] == "#"


def test_exact_solution_count_and_probabilities():
    analysis = constraint_analysis(2, 2, 1, ["1#", "##"])
    assert analysis["valid"]
    assert analysis["solution_count"] == 3
    assert set(analysis["probabilities"].values()) == {0.33333333}
    forced = constraint_analysis(2, 1, 1, ["1#"])
    assert forced["forced_mines"] == ["r1c2"]


def test_flags_and_exploded_mines_are_known_constraints():
    flagged = constraint_analysis(2, 1, 1, ["1F"])
    exploded = constraint_analysis(2, 1, 1, ["1*"])
    assert flagged["valid"] and flagged["solution_count"] == 1
    assert exploded["valid"] and exploded["solution_count"] == 1


def test_edge_cases_are_solver_confirmed_and_secrets_are_suppressed(tmp_path):
    config = get_config(); config["output_path"] = str(tmp_path)
    adapter = get_domain_adapter("minesweeper", config)
    assert len(adapter.tool_catalog) == 12
    invalid = adapter.select_problem(_scenario(edge="impossible_state"), 20)
    assert invalid.ground_truth["validity_status"] == "invalid"
    ambiguous = adapter.select_problem(_scenario(edge="no_forced_move"), 21)
    assert ambiguous.ground_truth["validity_status"] == "valid"
    assert not ambiguous.ground_truth["forced_safe"] and not ambiguous.ground_truth["forced_mines"]
    loss = adapter.select_problem(_scenario(edge="loss_state"), 22)
    assert loss.ground_truth["validity_status"] == "valid" and loss.ground_truth["loss"]

    hint = _scenario(task="hint")
    puzzle = adapter.select_problem(hint, 23)
    usage = adapter.maybe_use_tool(hint, puzzle)
    context = adapter.prompt_context(hint, puzzle, usage)
    assert puzzle.solution not in str(context)
    assert "mine_mask" not in str(context)


def test_explicit_solution_verification_can_expose_mask(tmp_path):
    config = get_config(); config["output_path"] = str(tmp_path)
    adapter = get_domain_adapter("minesweeper", config)
    scenario = _scenario(task="solve_board")
    puzzle = adapter.select_problem(scenario, 0)
    usage = adapter.maybe_use_tool(scenario, puzzle)
    assert any(call["tool_name"] == "minesweeper_solution_verification" for call in usage["calls"])
    assert puzzle.solution in str(adapter.prompt_context(scenario, puzzle, usage))
