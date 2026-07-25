import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.models import Scenario
from src.generator.starbattle import (
    StarBattlePuzzleManager,
    parse_puzzle,
    solution_violations,
    solve_starbattle,
    validate_structure,
)


def starbattle_config(tmp_path):
    config = get_config()
    config["domain"] = "starbattle"
    config["output_path"] = str(tmp_path)
    config["output_dir"] = str(tmp_path)
    config["model"] = {"model_name": "test-model"}
    config["similarity"] = {
        **config["similarity"],
        "exact_duplicate_threshold": 1.1,
        "normalized_duplicate_threshold": 1.1,
        "ngram_overlap_threshold": 1.1,
        "embedding_similarity_threshold": 1.1,
        "structural_similarity_threshold": 1.1,
        "scenario_similarity_threshold": 1.1,
        "puzzle_similarity_threshold": 1.1,
    }
    return config


def scenario(
    edge_case="none",
    difficulty="easy",
    task="region_analysis",
    tool="candidate_scan",
):
    return Scenario(
        scenario_id="starbattle-test",
        user_intent="analyze_region_candidates",
        conversation_type="single_turn",
        num_turns=1,
        user_expertise="intermediate",
        user_personality="analytical",
        assistant_style="coaching",
        tone="neutral",
        difficulty=difficulty,
        task_category=task,
        edge_case=edge_case,
        tool_usage=tool,
    )


def test_solution_validation_enforces_counts_and_diagonal_non_touching():
    regions = [
        {"id": f"R{row + 1}", "cells": [f"r{row + 1}c{col + 1}" for col in range(5)]}
        for row in range(5)
    ]
    valid_grid = [
        [0, 0, 1, 0, 0],
        [0, 0, 0, 0, 1],
        [0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0],
        [1, 0, 0, 0, 0],
    ]
    touching_grid = [
        [1, 0, 0, 0, 0],
        [0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0],
        [0, 0, 0, 0, 1],
        [0, 0, 1, 0, 0],
    ]

    assert solution_violations(5, 1, regions, valid_grid) == []
    assert any(
        violation["type"] == "touching_stars"
        for violation in solution_violations(5, 1, regions, touching_grid)
    )


def test_generated_bank_is_deterministic_unique_and_covers_sizes(tmp_path):
    config = resolve_domain_config(starbattle_config(tmp_path), "starbattle")
    first = StarBattlePuzzleManager(config)
    second = StarBattlePuzzleManager(config)

    assert [puzzle.puzzle for puzzle in first.base_bank] == [
        puzzle.puzzle for puzzle in second.base_bank
    ]
    assert [
        (puzzle.difficulty, puzzle.metadata["size"], puzzle.metadata["stars_per_unit"])
        for puzzle in first.base_bank
    ] == [
        ("easy", 5, 1),
        ("medium", 6, 1),
        ("hard", 7, 1),
        ("expert", 7, 1),
    ]
    for puzzle in first.base_bank:
        size, stars_per_unit, regions = parse_puzzle(puzzle.puzzle)
        assert validate_structure(size, stars_per_unit, regions) == []
        assert len(solve_starbattle(size, stars_per_unit, regions, limit=2)) == 1
        assert puzzle.ground_truth["solution_violations"] == []


def test_geometric_variants_preserve_structure_and_uniqueness(tmp_path):
    manager = StarBattlePuzzleManager(
        resolve_domain_config(starbattle_config(tmp_path), "starbattle")
    )
    variants = [manager.select_puzzle(scenario(), index) for index in range(8)]

    assert len({puzzle.transformation for puzzle in variants}) == 8
    for puzzle in variants:
        size, stars_per_unit, regions = parse_puzzle(puzzle.puzzle)
        assert validate_structure(size, stars_per_unit, regions) == []
        assert len(solve_starbattle(size, stars_per_unit, regions, limit=2)) == 1


def test_edge_variants_are_structure_and_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("starbattle", starbattle_config(tmp_path))
    expected = {
        "invalid_regions": ("invalid", "unknown"),
        "unsolvable_puzzle": ("valid", "unsolvable"),
        "ambiguous_puzzle": ("valid", "ambiguous"),
        "malformed_input": ("malformed", "unknown"),
    }
    for index, (edge_case, statuses) in enumerate(expected.items()):
        selected_scenario = scenario(edge_case=edge_case)
        puzzle = adapter.select_problem(selected_scenario, index)
        assert (
            puzzle.ground_truth["validity_status"],
            puzzle.ground_truth["solvability_status"],
        ) == statuses
        if edge_case == "ambiguous_puzzle":
            assert puzzle.ground_truth["forced_stars"] == []
            assert puzzle.ground_truth["forced_empty"] == []
        output = {
            "conversation_type": "single_turn",
            "category": selected_scenario.task_category,
            "prompt": "Can you inspect this Star Battle?",
            "response": "I checked its regions, line counts, and non-touching rule.",
            "board": puzzle.rendered_board,
        }
        assert adapter.validate(output, selected_scenario, puzzle).is_valid


def test_profile_tools_and_prompt_are_starbattle_specific_and_compact(tmp_path):
    config = starbattle_config(tmp_path)
    generator = ConversationGenerator(config)
    selected_scenario = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected_scenario, 0)
    tool = generator.domain.maybe_use_tool(selected_scenario, puzzle)
    prompt = generator._build_generation_prompt(selected_scenario, puzzle, tool)

    assert "Star Battle" in generator.domain.prompts["system_prompt"]
    assert "region_analysis" in generator.domain.config["scenario"]["task_categories"]
    assert tool["tool_name"] == "starbattle_candidate_analysis"
    assert "forced_stars" in tool["tool_output"]
    assert len(prompt) < 10_000
    assert '"regions"' not in prompt
    assert prompt.count("Region layout:") == 1


class PromptAwareModel:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt):
        self.prompt = prompt
        marker = '"task_category": "'
        category = prompt.split(marker, 1)[1].split('"', 1)[0]
        return json.dumps(
            {
                "prompt": "Help me find a forced star in this Star Battle.",
                "response": "Use the row, column, region, and non-touching constraints together.",
                "category": category,
            }
        )


def test_end_to_end_starbattle_generation_writes_compatible_outputs(tmp_path):
    generator = ConversationGenerator(starbattle_config(tmp_path))
    model = PromptAwareModel()
    generator.model_client = model

    result = generator.run(
        samples=1,
        conversation_type="single_turn",
        job_name="starbattle-job",
    )
    sample = json.loads(
        (tmp_path / "starbattle-job" / "samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    csv_text = (tmp_path / "starbattle-job" / "single_turn.csv").read_text(
        encoding="utf-8"
    )

    assert result["domain"] == "starbattle"
    assert sample["domain"] == "starbattle"
    assert sample["puzzle_metadata"]["metadata"]["regions"]
    assert sample["ground_truth"]["region_evaluations"]
    assert sample["output"]["board"].startswith("Star Battle ")
    assert "stars_per_unit" in csv_text and "regions" in csv_text
    assert (tmp_path / "starbattle_puzzle_bank.jsonl").exists()
    assert (tmp_path / "starbattle_puzzle_usage.json").exists()
