from __future__ import annotations

import pytest

from src.generator.config import get_config
from src.generator.domains import get_domain_adapter


DOMAINS = ("sudoku", "kenken", "kakuro", "starbattle", "nonogram", "hitori", "nurikabe", "shikaku", "futoshiki", "kakurasu", "sumplete", "othello", "minesweeper", "wordle")
NEW_DOMAINS = ("othello", "minesweeper", "wordle", "shikaku", "futoshiki", "kakurasu", "sumplete")


@pytest.mark.parametrize("domain", NEW_DOMAINS)
def test_new_domain_banks_and_usage_are_persistent(tmp_path, domain):
    config = get_config(); config["output_path"] = str(tmp_path / domain)
    adapter = get_domain_adapter(domain, config)
    assert adapter.puzzle_manager.bank_path.exists()
    assert len(adapter.puzzle_manager.bank_path.read_text(encoding="utf-8").splitlines()) == len(adapter.puzzle_manager.base_bank)
    scenario = adapter.generate_scenario(sample_index=0, conversation_type="single_turn", max_turns=2, distribution_stats={})
    puzzle = adapter.select_problem(scenario, 0)
    adapter.mark_problem_used(puzzle)
    assert adapter.puzzle_manager.usage_path.exists()


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_puzzle_domain_spans_complexity_and_uses_verified_bundles(tmp_path, domain):
    config = get_config()
    config["output_path"] = str(tmp_path / domain)
    config["output_dir"] = str(tmp_path / domain)
    adapter = get_domain_adapter(domain, config)
    attempts = config["generation"]["max_regeneration_attempts"]
    rows = []

    for record_slot in range(8):
        conversation_type = "multi_turn" if record_slot % 2 else "single_turn"
        dataset_index = record_slot // 2
        scenario = adapter.generate_scenario(
            sample_index=dataset_index * attempts,
            conversation_type=conversation_type,
            max_turns=4,
            distribution_stats={},
        )
        puzzle = adapter.select_problem(scenario, dataset_index * attempts)
        usage = adapter.maybe_use_tool(scenario, puzzle)
        rows.append((scenario, puzzle, usage))

    assert {puzzle.difficulty for _, puzzle, _ in rows} == {"easy", "medium", "hard", "expert"}
    assert len({puzzle.puzzle_id for _, puzzle, _ in rows}) == len(rows)
    assert all(puzzle.metadata["complexity_band"] == puzzle.difficulty for _, puzzle, _ in rows)
    assert all(puzzle.metadata["complexity_score"] is not None for _, puzzle, _ in rows)
    assert all(len(usage["calls"]) >= 2 for _, _, usage in rows)
    assert all(len({call["tool_name"] for call in usage["calls"]}) == len(usage["calls"]) for _, _, usage in rows)
    assert all(all(call["output"]["verified"] for call in usage["calls"]) for _, _, usage in rows)
    assert len({call["tool_name"] for _, _, usage in rows for call in usage["calls"]}) >= 4
    assert not [error for _, puzzle, _ in rows for error in adapter.tool_validation_errors(puzzle)]


@pytest.mark.parametrize("domain", DOMAINS)
def test_all_puzzle_domains_continue_with_balanced_reuse_after_finite_pool(tmp_path, domain):
    config = get_config()
    config["output_path"] = str(tmp_path / domain)
    config["output_dir"] = str(tmp_path / domain)
    config["puzzles"]["variant_scan_limit"] = 32
    adapter = get_domain_adapter(domain, config)
    scenario = adapter.generate_scenario(
        sample_index=0,
        conversation_type="multi_turn",
        max_turns=4,
        distribution_stats={},
    )

    puzzles = [adapter.select_problem(scenario, index) for index in range(100)]

    assert len(puzzles) == 100
    assert all(puzzle.puzzle_id for puzzle in puzzles)
    assert all("reused_in_job" in puzzle.metadata for puzzle in puzzles)


def test_kenken_continues_past_a_full_thousand_sample_job(tmp_path):
    config = get_config()
    config["output_path"] = str(tmp_path / "kenken-long")
    config["output_dir"] = str(tmp_path / "kenken-long")
    config["puzzles"]["variant_scan_limit"] = 32
    adapter = get_domain_adapter("kenken", config)
    scenario = adapter.generate_scenario(
        sample_index=0,
        conversation_type="multi_turn",
        max_turns=6,
        distribution_stats={},
    )
    scenario.edge_case = "none"
    scenario.difficulty = "medium"

    puzzles = [adapter.select_problem(scenario, index) for index in range(2_500)]

    assert len(puzzles) == 2_500
    assert any(puzzle.metadata["reused_in_job"] for puzzle in puzzles)
    counts: dict[str, int] = {}
    for puzzle in puzzles:
        counts[puzzle.puzzle_id] = counts.get(puzzle.puzzle_id, 0) + 1
    assert max(counts.values()) - min(counts.values()) <= 1
