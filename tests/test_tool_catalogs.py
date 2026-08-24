from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.generator.config import get_config
from src.generator.domain_support import (
    PUZZLE_ROUTE_CATEGORIES,
    PUZZLE_ROUTE_EDGES,
    PUZZLE_ROUTE_MODES,
)
from src.generator.domains import SUPPORTED_DOMAINS, get_domain_adapter
from src.generator.models import Scenario


PUZZLE_DOMAINS = ("sudoku", "kenken", "kakuro", "starbattle", "nonogram", "hitori", "nurikabe", "shikaku", "futoshiki", "kakurasu", "sumplete", "othello", "minesweeper", "wordle")
NEW_SOLVER_DOMAINS = ("shikaku", "futoshiki", "kakurasu", "sumplete")
NEW_TOOL_OUTPUT_KEYS = {
    "sudoku": {
        "sudoku_unit_analysis": "units", "sudoku_naked_single_scan": "naked_singles",
        "sudoku_hidden_single_scan": "hidden_singles", "sudoku_locked_candidate_scan": "locked_candidates",
        "sudoku_move_impact_analysis": "peer_eliminations",
    },
    "kenken": {
        "kenken_latin_unit_analysis": "rows", "kenken_cage_feasibility": "cage_feasibility",
        "kenken_cage_intersection_analysis": "cell_candidates", "kenken_move_impact_analysis": "eliminations",
        "kenken_solution_space_analysis": "solution_count_capped",
    },
    "kakuro": {
        "kakuro_run_topology": "cells", "kakuro_crossing_analysis": "cell_candidates",
        "kakuro_run_feasibility": "runs", "kakuro_move_impact_analysis": "eliminations",
        "kakuro_solution_space_analysis": "solution_count_capped",
    },
    "starbattle": {
        "starbattle_row_quota_analysis": "rows", "starbattle_column_quota_analysis": "columns",
        "starbattle_region_quota_analysis": "regions", "starbattle_adjacency_exclusion_scan": "excluded_by_adjacency",
        "starbattle_solution_space_analysis": "solution_count_capped",
    },
    "nonogram": {
        "nonogram_row_pattern_analysis": "rows", "nonogram_column_pattern_analysis": "columns",
        "nonogram_overlap_deduction": "line_overlaps", "nonogram_cross_line_propagation": "forced_filled",
        "nonogram_solution_space_analysis": "solution_count_capped",
    },
    "hitori": {
        "hitori_row_duplicate_groups": "groups", "hitori_column_duplicate_groups": "groups",
        "hitori_adjacency_risk_analysis": "must_remain_unshaded", "hitori_connectivity_analysis": "component_count",
        "hitori_solution_space_analysis": "solution_count_capped",
    },
    "nurikabe": {
        "nurikabe_island_capacity_analysis": "islands", "nurikabe_island_separation_scan": "forced_sea_between_clues",
        "nurikabe_sea_connectivity_analysis": "component_count", "nurikabe_two_by_two_risk_scan": "forced_island_to_avoid_2x2",
        "nurikabe_solution_space_analysis": "solution_count_capped",
    },
    "shikaku": {
        "shikaku_clue_area_analysis": "clues", "shikaku_exact_cover_analysis": "most_constrained_clue",
        "shikaku_cell_ownership_analysis": "forced_ownership", "shikaku_move_impact_analysis": "eliminated_candidates",
        "shikaku_solution_space_analysis": "solution_count_capped",
    },
    "futoshiki": {
        "futoshiki_row_unit_analysis": "rows", "futoshiki_column_unit_analysis": "columns",
        "futoshiki_inequality_chain_analysis": "chains", "futoshiki_move_impact_analysis": "eliminations",
        "futoshiki_solution_space_analysis": "solution_count_capped",
    },
    "kakurasu": {
        "kakurasu_row_pattern_analysis": "rows", "kakurasu_column_pattern_analysis": "columns",
        "kakurasu_weight_contribution_analysis": "contributions", "kakurasu_move_impact_analysis": "eliminations",
        "kakurasu_solution_space_analysis": "solution_count_capped",
    },
    "sumplete": {
        "sumplete_row_subset_analysis": "rows", "sumplete_column_subset_analysis": "columns",
        "sumplete_target_balance_analysis": "balances", "sumplete_move_impact_analysis": "eliminations",
        "sumplete_solution_space_analysis": "solution_count_capped",
    },
    "othello": {
        "othello_legal_move_scan": "moves", "othello_mobility_analysis": "mobility",
        "othello_move_comparison": "comparisons", "othello_state_summary": "validity_status",
    },
    "minesweeper": {
        "minesweeper_frontier_analysis": "components", "minesweeper_probability_analysis": "probabilities",
        "minesweeper_solution_space_analysis": "solution_count", "minesweeper_board_summary": "mine_count",
    },
    "wordle": {
        "wordle_history_validation": "candidate_count", "wordle_candidate_filter": "candidates",
        "wordle_letter_constraint_analysis": "green_positions", "wordle_game_summary": "guess_count",
    },
}


def _scenario(task: str = "advanced_question", edge: str = "none") -> Scenario:
    return Scenario("catalog-test", task, "single_turn", 1, "advanced", "analytical", "coaching", "neutral", "easy", task, edge, "none")


def test_every_domain_declares_at_least_ten_reachable_tools():
    for domain, adapter_class in SUPPORTED_DOMAINS.items():
        catalog = adapter_class.tool_catalog
        assert len(catalog) >= 10, domain
        assert len(catalog) == len(set(catalog))
        assert all(name.startswith(f"{domain}_") for name in catalog)
        assert all(spec.description and spec.output_purpose for spec in catalog.values())


@pytest.mark.parametrize("domain", PUZZLE_DOMAINS)
def test_new_tool_executors_return_json_evidence(tmp_path, domain):
    config = get_config(); config["output_path"] = str(tmp_path / domain)
    adapter = get_domain_adapter(domain, config)
    scenario = _scenario()
    puzzle = adapter.select_problem(scenario, 0)

    assert set(adapter.catalog_route_names()) == set(adapter.tool_catalog)
    for tool_name, expected_key in NEW_TOOL_OUTPUT_KEYS[domain].items():
        output = adapter._run_tool(tool_name, puzzle)
        assert expected_key in output, tool_name
        json.dumps(output)


@pytest.mark.parametrize("domain", PUZZLE_DOMAINS)
def test_malformed_routes_use_diagnostics_without_solution_space(tmp_path, domain):
    config = get_config(); config["output_path"] = str(tmp_path / domain)
    adapter = get_domain_adapter(domain, config)
    malformed = _scenario(task="rules_explanation", edge="malformed_input")
    names = adapter._tool_bundle(malformed)
    assert any("validation" in name for name in names)
    assert not any("solution_space" in name or "solution_verification" in name for name in names)


@pytest.mark.parametrize("domain", NEW_SOLVER_DOMAINS)
def test_new_solver_domains_keep_every_registered_route_to_two_to_five_distinct_tools(
    tmp_path, domain
):
    config = get_config()
    config["output_path"] = str(tmp_path / domain)
    adapter = get_domain_adapter(domain, config)

    for category in PUZZLE_ROUTE_CATEGORIES:
        for edge_case in PUZZLE_ROUTE_EDGES:
            for tool_usage in PUZZLE_ROUTE_MODES:
                scenario = SimpleNamespace(
                    task_category=category,
                    edge_case=edge_case,
                    tool_usage=tool_usage,
                )
                names = adapter._tool_bundle(scenario)
                assert 2 <= len(names) <= 5
                assert len(names) == len(set(names))
                assert set(names) <= set(adapter.tool_catalog)
