import csv
import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.models import Scenario
from src.generator.shikaku import (
    ShikakuPuzzleManager,
    canonical_puzzle,
    parse_puzzle,
    solution_violations,
    solve_shikaku,
    validate_structure,
)


def shikaku_config(tmp_path):
    config = get_config()
    config["domain"] = "shikaku"
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
    task="rectangle_analysis",
    tool="rectangle_candidate_scan",
):
    return Scenario(
        "shikaku-test",
        "analyze_rectangle_candidates",
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


def _solution_signature(rectangles):
    return tuple(
        sorted(
            (
                rectangle["clue_cell"],
                rectangle["top_left"],
                rectangle["bottom_right"],
            )
            for rectangle in rectangles
        )
    )


def test_exact_solver_fixtures_cover_unique_invalid_unsolvable_and_ambiguous():
    unique_clues = [{"cell": "r1c1", "value": 4}]
    encoded = canonical_puzzle(2, 2, unique_clues)
    assert parse_puzzle(encoded) == (2, 2, unique_clues)
    assert validate_structure(2, 2, unique_clues) == []

    unique_solutions = solve_shikaku(2, 2, unique_clues, limit=2)
    assert [_solution_signature(solution) for solution in unique_solutions] == [
        (("r1c1", "r1c1", "r2c2"),)
    ]
    assert solution_violations(2, 2, unique_clues, unique_solutions[0]) == []

    invalid_clues = [
        {"cell": "r1c1", "value": 1},
        {"cell": "r1c1", "value": 3},
    ]
    assert validate_structure(2, 2, invalid_clues)

    unsolvable_clues = [
        {"cell": "r1c1", "value": 3},
        {"cell": "r2c2", "value": 1},
    ]
    assert validate_structure(2, 2, unsolvable_clues) == []
    assert solve_shikaku(2, 2, unsolvable_clues, limit=2) == []

    ambiguous_clues = [
        {"cell": "r1c1", "value": 2},
        {"cell": "r2c2", "value": 2},
    ]
    assert validate_structure(2, 2, ambiguous_clues) == []
    ambiguous_solutions = solve_shikaku(2, 2, ambiguous_clues, limit=3)
    assert len(ambiguous_solutions) == 2
    assert {_solution_signature(solution) for solution in ambiguous_solutions} == {
        (
            ("r1c1", "r1c1", "r1c2"),
            ("r2c2", "r2c1", "r2c2"),
        ),
        (
            ("r1c1", "r1c1", "r2c1"),
            ("r2c2", "r1c2", "r2c2"),
        ),
    }


def test_generated_bank_is_deterministic_unique_and_covers_difficulties(tmp_path):
    config = resolve_domain_config(shikaku_config(tmp_path), "shikaku")
    first = ShikakuPuzzleManager(config)
    second = ShikakuPuzzleManager(config)

    assert [puzzle.puzzle for puzzle in first.base_bank] == [
        puzzle.puzzle for puzzle in second.base_bank
    ]
    assert [
        (puzzle.difficulty, puzzle.metadata["height"], puzzle.metadata["width"])
        for puzzle in first.base_bank
    ] == [
        ("easy", 5, 5),
        ("medium", 6, 6),
        ("hard", 7, 7),
        ("expert", 8, 8),
    ]

    for puzzle in first.base_bank:
        height, width, clues = parse_puzzle(puzzle.puzzle)
        solutions = solve_shikaku(height, width, clues, limit=2)
        assert validate_structure(height, width, clues) == []
        assert len(solutions) == 1
        assert solution_violations(height, width, clues, solutions[0]) == []
        assert puzzle.ground_truth["solvability_status"] == "unique"


def test_transformations_and_edge_cases_are_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("shikaku", shikaku_config(tmp_path))
    variants = [adapter.select_problem(scenario(), index) for index in range(8)]

    assert [puzzle.transformation for puzzle in variants] == [
        "identity",
        "rotate_90",
        "rotate_180",
        "rotate_270",
        "reflect_horizontal",
        "reflect_vertical",
        "transpose",
        "anti_transpose",
    ]
    for puzzle in variants:
        height, width, clues = parse_puzzle(puzzle.puzzle)
        assert validate_structure(height, width, clues) == []
        assert len(solve_shikaku(height, width, clues, limit=2)) == 1

    expected = {
        "invalid_clues": ("invalid", "unknown", 0),
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
            "prompt": "Can you inspect this Shikaku partition?",
            "response": "I checked every clue area and the exact rectangle cover.",
            "board": puzzle.rendered_board,
        }
        assert adapter.validate(output, selected, puzzle).is_valid


def test_more_than_sixty_four_indexed_selections_are_unique_and_replayable(tmp_path):
    selection_count = 65
    first = ShikakuPuzzleManager(
        resolve_domain_config(shikaku_config(tmp_path / "first"), "shikaku")
    )
    second = ShikakuPuzzleManager(
        resolve_domain_config(shikaku_config(tmp_path / "second"), "shikaku")
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


def test_adapter_validation_tools_and_prompt_are_shikaku_specific_and_compact(tmp_path):
    generator = ConversationGenerator(shikaku_config(tmp_path))
    selected = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected, 0)
    tool_usage = generator.domain.maybe_use_tool(selected, puzzle)
    prompt = generator._build_generation_prompt(selected, puzzle, tool_usage)

    assert "Shikaku" in generator.domain.prompts["system_prompt"]
    assert "rectangle_analysis" in generator.domain.config["scenario"]["task_categories"]
    assert tool_usage["tool_name"] == "shikaku_rectangle_candidate_scan"
    assert "candidate_rectangles" in tool_usage["tool_output"]
    impact = generator.domain._run_tool("shikaku_move_impact_analysis", puzzle)
    suggested = impact["suggested_move"]
    assert suggested
    chosen = (
        suggested["clue_cell"],
        suggested["top_left"],
        suggested["bottom_right"],
    )
    assert chosen not in {
        (
            eliminated["rectangle"]["clue_cell"],
            eliminated["rectangle"]["top_left"],
            eliminated["rectangle"]["bottom_right"],
        )
        for eliminated in impact["eliminated_candidates"]
    }
    assert len(tool_usage["calls"]) >= 2
    assert len({call["tool_name"] for call in tool_usage["calls"]}) == len(
        tool_usage["calls"]
    )
    assert all(call["output"]["verified"] for call in tool_usage["calls"])
    assert generator.domain.validate(
        {
            "conversation_type": "single_turn",
            "category": selected.task_category,
            "prompt": "Which rectangle should I inspect first?",
            "response": "Start with the clue having only one feasible rectangle.",
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
                "prompt": "Help me reason about this Shikaku grid.",
                "response": "Begin with the clue whose area has the fewest rectangle placements.",
                "category": category,
            }
        )


def test_end_to_end_shikaku_generation_persists_compatible_outputs(tmp_path):
    config = shikaku_config(tmp_path)
    config["domains"]["shikaku"]["scenario"].update(
        {
            "task_categories": ["rectangle_analysis"],
            "difficulty_levels": ["easy"],
            "edge_cases": ["none"],
            "tool_usage_modes": ["rectangle_candidate_scan"],
        }
    )
    generator = ConversationGenerator(config)
    generator.model_client = PromptAwareModel()

    result = generator.run(
        samples=1,
        conversation_type="single_turn",
        job_name="shikaku-job",
    )
    sample_path = tmp_path / "shikaku-job" / "samples.jsonl"
    sample = json.loads(sample_path.read_text(encoding="utf-8").splitlines()[0])
    csv_path = tmp_path / "shikaku-job" / "single_turn.csv"
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result["domain"] == "shikaku"
    assert result["accepted_samples"] == 1
    assert sample["domain"] == "shikaku"
    assert sample["conversation_type"] == "single_turn"
    assert sample["puzzle_metadata"]["metadata"]["clues"]
    assert sample["ground_truth"]["solvability_status"] == "unique"
    assert sample["ground_truth"]["solution_rectangles"]
    assert sample["ground_truth"]["solution_violations"] == []
    assert sample["tool_usage_details"]["calls"]
    assert sample["output"]["board"].startswith("Shikaku ")
    assert sample["output"]["prompt"] == "Help me reason about this Shikaku grid."
    assert len(rows) == 1
    assert rows[0]["domain"] == "shikaku"
    assert rows[0]["grid_height"] == "5"
    assert rows[0]["grid_width"] == "5"
    assert rows[0]["clues"]
    assert (tmp_path / "shikaku_puzzle_bank.jsonl").exists()
    assert (tmp_path / "shikaku_puzzle_usage.json").exists()
