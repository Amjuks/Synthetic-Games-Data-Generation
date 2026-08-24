from __future__ import annotations

from types import SimpleNamespace

from src.generator.config import get_config
from src.generator.domains import get_domain_adapter


def test_sudoku_variant_pool_does_not_exhaust_for_large_job(tmp_path):
    config = get_config()
    config["output_path"] = str(tmp_path / "sudoku")
    config["output_dir"] = str(tmp_path / "sudoku")
    adapter = get_domain_adapter("sudoku", config)
    scenario = SimpleNamespace(
        difficulty="medium",
        edge_case="none",
        task_category="solve_puzzle",
        tool_usage="solution_verification",
    )

    # Cover more selections than a 1,000-row job can consume through normal
    # regeneration. Reserving every result models accepted and rejected rows.
    puzzles = [adapter.select_problem(scenario, index) for index in range(5_000)]

    assert len({puzzle.puzzle_id for puzzle in puzzles}) == len(puzzles)
    assert len({puzzle.puzzle for puzzle in puzzles}) == len(puzzles)
    assert all(puzzle.ground_truth["validity_status"] == "valid" for puzzle in puzzles)
    assert all(puzzle.ground_truth["unique_solution_status"] for puzzle in puzzles)
