import csv
import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.kakurasu import (
    KakurasuPuzzleManager,
    canonical_puzzle,
    parse_puzzle,
    solution_violations,
    solve_kakurasu,
    validate_structure,
)
from src.generator.models import Scenario


def kakurasu_config(tmp_path):
    config = get_config()
    config["domain"] = "kakurasu"
    config["output_path"] = str(tmp_path)
    config["output_dir"] = str(tmp_path)
    config["model"] = {"model_name": "test-model"}
    config["similarity"] = {
        **config["similarity"],
        **{
            key: 1.1
            for key in (
                "exact_duplicate_threshold",
                "normalized_duplicate_threshold",
                "ngram_overlap_threshold",
                "embedding_similarity_threshold",
                "structural_similarity_threshold",
                "scenario_similarity_threshold",
                "puzzle_similarity_threshold",
            )
        },
    }
    return config


def scenario(
    edge_case="none",
    difficulty="easy",
    task="weighted_sum_analysis",
    tool="weighted_sum_scan",
):
    return Scenario(
        "kakurasu-test",
        "analyze_weighted_subsets",
        "single_turn",
        1,
        "intermediate",
        "analytical",
        "coaching",
        "neutral",
        difficulty,
        task,
        edge_case,
        tool,
    )


def _derive_targets(mask):
    height = len(mask)
    width = len(mask[0])
    row_targets = [
        sum(column + 1 for column, selected in enumerate(row) if selected)
        for row in mask
    ]
    column_targets = [
        sum(row + 1 for row in range(height) if mask[row][column])
        for column in range(width)
    ]
    return row_targets, column_targets


def _mask_signature(mask):
    return tuple(tuple(row) for row in mask)


def test_canonical_parser_structure_and_exact_positional_weights():
    expected = [
        [1, 0, 1],
        [0, 1, 1],
    ]
    row_targets, column_targets = _derive_targets(expected)

    # Rows use 1-based column weights; columns use 1-based row weights.
    assert row_targets == [4, 5]
    assert column_targets == [1, 2, 3]

    encoded = canonical_puzzle(2, 3, row_targets, column_targets)
    assert parse_puzzle(encoded) == (2, 3, row_targets, column_targets)
    assert validate_structure(2, 3, row_targets, column_targets) == []
    assert solve_kakurasu(2, 3, row_targets, column_targets, limit=2) == [expected]
    assert solution_violations(
        2,
        3,
        row_targets,
        column_targets,
        expected,
    ) == []


def test_invalid_target_and_row_zero_positive_column_are_classified_exactly():
    assert validate_structure(2, 3, [7, 0], [0, 0, 0])

    row_targets = [0, 0]
    column_targets = [1, 0, 0]
    assert validate_structure(2, 3, row_targets, column_targets) == []
    assert solve_kakurasu(2, 3, row_targets, column_targets, limit=2) == []


def test_three_by_three_targets_of_three_have_exactly_two_solutions():
    targets = [3, 3, 3]
    solutions = solve_kakurasu(3, 3, targets, targets, limit=3)

    assert validate_structure(3, 3, targets, targets) == []
    assert len(solutions) == 2
    assert {_mask_signature(solution) for solution in solutions} == {
        (
            (1, 1, 0),
            (1, 1, 0),
            (0, 0, 1),
        ),
        (
            (0, 0, 1),
            (0, 0, 1),
            (1, 1, 0),
        ),
    }
    assert all(
        solution_violations(3, 3, targets, targets, solution) == []
        for solution in solutions
    )


def test_generated_bank_is_deterministic_unique_and_covers_difficulties(tmp_path):
    config = resolve_domain_config(kakurasu_config(tmp_path), "kakurasu")
    first = KakurasuPuzzleManager(config)
    second = KakurasuPuzzleManager(config)

    assert [puzzle.puzzle for puzzle in first.base_bank] == [
        puzzle.puzzle for puzzle in second.base_bank
    ]
    assert [
        (
            puzzle.difficulty,
            puzzle.metadata["height"],
            puzzle.metadata["width"],
        )
        for puzzle in first.base_bank
    ] == [
        ("easy", 4, 4),
        ("medium", 5, 5),
        ("hard", 6, 6),
        ("expert", 7, 7),
    ]

    for puzzle in first.base_bank:
        height, width, row_targets, column_targets = parse_puzzle(puzzle.puzzle)
        solutions = solve_kakurasu(
            height,
            width,
            row_targets,
            column_targets,
            limit=2,
        )
        assert validate_structure(height, width, row_targets, column_targets) == []
        assert len(solutions) == 1
        assert solution_violations(
            height,
            width,
            row_targets,
            column_targets,
            solutions[0],
        ) == []
        assert puzzle.ground_truth["solvability_status"] == "unique"


def test_d4_complement_variants_and_edge_cases_are_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("kakurasu", kakurasu_config(tmp_path))
    d4 = [
        "identity",
        "rotate_90",
        "rotate_180",
        "rotate_270",
        "reflect_horizontal",
        "reflect_vertical",
        "transpose",
        "anti_transpose",
    ]
    expected_transformations = [
        *d4,
        *(f"complement_{transformation}" for transformation in d4),
    ]
    for difficulty in ("easy", "medium", "hard", "expert"):
        variants = [
            adapter.select_problem(scenario(difficulty=difficulty), index)
            for index in range(16)
        ]
        assert [puzzle.transformation for puzzle in variants] == expected_transformations
        for puzzle in variants:
            height, width, row_targets, column_targets = parse_puzzle(puzzle.puzzle)
            assert validate_structure(height, width, row_targets, column_targets) == []
            solutions = solve_kakurasu(
                height,
                width,
                row_targets,
                column_targets,
                limit=2,
            )
            assert len(solutions) == 1
            assert puzzle.ground_truth["solvability_status"] == "unique"
            assert puzzle.ground_truth["solution_mask"] == solutions[0]

    expected = {
        "invalid_targets": ("invalid", "unknown", 0),
        "unsolvable_puzzle": ("valid", "unsolvable", 0),
        "ambiguous_puzzle": ("valid", "ambiguous", 2),
        "malformed_input": ("malformed", "unknown", 0),
    }
    for index, (edge_case, statuses) in enumerate(expected.items()):
        selected = scenario(edge_case=edge_case)
        puzzle = adapter.select_problem(selected, index)
        truth = puzzle.ground_truth
        assert (
            truth["validity_status"],
            truth["solvability_status"],
            truth["solution_count"],
        ) == statuses
        output = {
            "conversation_type": "single_turn",
            "category": selected.task_category,
            "prompt": "Can you verify these Kakurasu targets?",
            "response": "I checked every row with column weights and every column with row weights.",
            "board": puzzle.rendered_board,
        }
        assert adapter.validate(output, selected, puzzle).is_valid


def test_more_than_sixty_four_indexed_selections_are_distinct_and_replayable(tmp_path):
    selection_count = 65
    first = KakurasuPuzzleManager(
        resolve_domain_config(kakurasu_config(tmp_path / "first"), "kakurasu")
    )
    second = KakurasuPuzzleManager(
        resolve_domain_config(kakurasu_config(tmp_path / "second"), "kakurasu")
    )

    first_records = [
        first.select_puzzle(scenario(), sample_index)
        for sample_index in range(selection_count)
    ]
    second_records = [
        second.select_puzzle(scenario(), sample_index)
        for sample_index in range(selection_count)
    ]

    assert len({puzzle.puzzle_id for puzzle in first_records}) == selection_count
    assert len({puzzle.puzzle for puzzle in first_records}) == selection_count
    assert [
        (puzzle.puzzle_id, puzzle.puzzle, puzzle.solution, puzzle.transformation)
        for puzzle in first_records
    ] == [
        (puzzle.puzzle_id, puzzle.puzzle, puzzle.solution, puzzle.transformation)
        for puzzle in second_records
    ]


def test_adapter_tools_validator_and_prompt_are_kakurasu_specific_and_compact(tmp_path):
    generator = ConversationGenerator(kakurasu_config(tmp_path))
    selected = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected, 0)
    tool_usage = generator.domain.maybe_use_tool(selected, puzzle)
    prompt = generator._build_generation_prompt(selected, puzzle, tool_usage)

    assert "Kakurasu" in generator.domain.prompts["system_prompt"]
    assert "weighted_sum_analysis" in generator.domain.config["scenario"]["task_categories"]
    assert tool_usage["tool_name"] == "kakurasu_subset_analysis"
    assert "row_patterns" in tool_usage["tool_output"]
    assert "forced_selected" in tool_usage["tool_output"]
    assert "forced_unselected" in tool_usage["tool_output"]
    assert "suggested_move" in tool_usage["tool_output"]
    assert len(tool_usage["calls"]) >= 2
    assert len({call["tool_name"] for call in tool_usage["calls"]}) == len(
        tool_usage["calls"]
    )
    assert all(call["output"]["verified"] for call in tool_usage["calls"])
    json.dumps(tool_usage)
    assert generator.domain.validate(
        {
            "conversation_type": "single_turn",
            "category": selected.task_category,
            "prompt": "Which weighted subset is most constrained?",
            "response": "Start with the target having the fewest positional-weight subsets.",
            "board": puzzle.rendered_board,
        },
        selected,
        puzzle,
    ).is_valid
    assert len(prompt) < 50_000
    assert prompt.count("Ground Truth JSON:") == 1
    assert prompt.count("Tool Context JSON:") == 1


class PromptAwareModel:
    def generate(self, prompt):
        category = prompt.split('"task_category": "', 1)[1].split('"', 1)[0]
        return json.dumps(
            {
                "prompt": "Help me reason about this Kakurasu grid.",
                "response": "Compare each target with the available 1-based positional weights.",
                "category": category,
            }
        )


def test_end_to_end_kakurasu_generation_persists_compatible_outputs(tmp_path):
    config = kakurasu_config(tmp_path)
    config["domains"]["kakurasu"]["scenario"].update(
        {
            "task_categories": ["weighted_sum_analysis"],
            "difficulty_levels": ["easy"],
            "edge_cases": ["none"],
            "tool_usage_modes": ["weighted_sum_scan"],
        }
    )
    generator = ConversationGenerator(config)
    generator.model_client = PromptAwareModel()

    result = generator.run(
        samples=1,
        conversation_type="single_turn",
        job_name="kakurasu-job",
    )
    sample_path = tmp_path / "kakurasu-job" / "samples.jsonl"
    sample = json.loads(sample_path.read_text(encoding="utf-8").splitlines()[0])
    csv_path = tmp_path / "kakurasu-job" / "single_turn.csv"
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result["domain"] == "kakurasu"
    assert result["accepted_samples"] == 1
    assert sample["domain"] == "kakurasu"
    assert sample["conversation_type"] == "single_turn"
    assert sample["puzzle_metadata"]["metadata"]["row_targets"]
    assert sample["puzzle_metadata"]["metadata"]["column_targets"]
    assert sample["ground_truth"]["solvability_status"] == "unique"
    assert sample["ground_truth"]["solution_mask"]
    assert sample["ground_truth"]["solution_violations"] == []
    assert sample["tool_usage_details"]["calls"]
    assert sample["output"]["board"].startswith("Kakurasu ")
    assert sample["output"]["prompt"] == "Help me reason about this Kakurasu grid."
    assert len(rows) == 1
    assert rows[0]["domain"] == "kakurasu"
    assert rows[0]["grid_height"] == "4"
    assert rows[0]["grid_width"] == "4"
    assert json.loads(rows[0]["row_weights"]) == [1, 2, 3, 4]
    assert json.loads(rows[0]["column_weights"]) == [1, 2, 3, 4]
    assert rows[0]["row_targets"]
    assert rows[0]["column_targets"]
    assert (tmp_path / "kakurasu_puzzle_bank.jsonl").exists()
    assert (tmp_path / "kakurasu_puzzle_usage.json").exists()
