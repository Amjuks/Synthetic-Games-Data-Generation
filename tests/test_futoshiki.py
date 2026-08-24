import csv
import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.futoshiki import (
    FutoshikiPuzzleManager,
    canonical_puzzle,
    parse_puzzle,
    solution_violations,
    solve_futoshiki,
    validate_structure,
)
from src.generator.generator import ConversationGenerator
from src.generator.models import Scenario


def futoshiki_config(tmp_path):
    config = get_config()
    config["domain"] = "futoshiki"
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
    task="inequality_analysis",
    tool="inequality_scan",
):
    return Scenario(
        "futoshiki-test",
        "analyze_inequality_constraints",
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


def _givens_for_rows(rows):
    return [
        {"cell": f"r{row_index + 1}c{column_index + 1}", "value": value}
        for row_index, row in enumerate(rows)
        for column_index, value in enumerate(row)
    ]


def test_canonical_parser_structure_and_latin_solver_do_not_apply_sudoku_boxes():
    expected = [
        [1, 2, 3, 4],
        [2, 3, 4, 1],
        [3, 4, 1, 2],
        [4, 1, 2, 3],
    ]
    givens = _givens_for_rows(expected[:3])
    inequalities = [
        {"id": "I1", "left": "r1c3", "relation": "<", "right": "r1c4"},
        {"id": "I2", "left": "r1c4", "relation": ">", "right": "r2c4"},
        {"id": "I3", "left": "r4c2", "relation": "<", "right": "r4c3"},
    ]

    encoded = canonical_puzzle(4, givens, inequalities)
    assert parse_puzzle(encoded) == (4, givens, inequalities)
    assert validate_structure(4, givens, inequalities) == []
    assert solve_futoshiki(4, givens, inequalities, limit=2) == [expected]
    assert solution_violations(4, givens, inequalities, expected) == []

    # The top-left 2x2 contains two 2s. That is valid Futoshiki because only
    # complete rows and columns are Latin units; Sudoku-style boxes do not exist.
    top_left_box = [expected[row][column] for row in range(2) for column in range(2)]
    assert len(set(top_left_box)) < len(top_left_box)

    invalid_givens = [
        {"cell": "r1c1", "value": 1},
        {"cell": "r1c1", "value": 2},
    ]
    invalid_inequalities = [
        {"id": "I1", "left": "r1c1", "relation": "=", "right": "r1c2"}
    ]
    assert validate_structure(4, invalid_givens, inequalities)
    assert validate_structure(4, [], invalid_inequalities)


def test_strict_two_by_two_inequality_cycle_has_no_solution():
    cycle = [
        {"id": "I1", "left": "r1c1", "relation": "<", "right": "r1c2"},
        {"id": "I2", "left": "r1c2", "relation": "<", "right": "r2c2"},
        {"id": "I3", "left": "r2c2", "relation": "<", "right": "r2c1"},
        {"id": "I4", "left": "r2c1", "relation": "<", "right": "r1c1"},
    ]

    assert validate_structure(2, [], cycle) == []
    assert solve_futoshiki(2, [], cycle, limit=2) == []


def test_empty_and_sparse_grids_are_solver_confirmed_ambiguous():
    empty_solutions = solve_futoshiki(3, [], [], limit=3)
    sparse_solutions = solve_futoshiki(
        3,
        [{"cell": "r1c1", "value": 1}],
        [{"id": "I1", "left": "r1c1", "relation": "<", "right": "r1c2"}],
        limit=3,
    )

    assert len(empty_solutions) >= 2
    assert len(sparse_solutions) >= 2
    assert all(solution_violations(3, [], [], grid) == [] for grid in empty_solutions)
    assert all(
        solution_violations(
            3,
            [{"cell": "r1c1", "value": 1}],
            [{"id": "I1", "left": "r1c1", "relation": "<", "right": "r1c2"}],
            grid,
        )
        == []
        for grid in sparse_solutions
    )


def test_generated_bank_is_deterministic_unique_and_covers_difficulties(tmp_path):
    config = resolve_domain_config(futoshiki_config(tmp_path), "futoshiki")
    first = FutoshikiPuzzleManager(config)
    second = FutoshikiPuzzleManager(config)

    assert [puzzle.puzzle for puzzle in first.base_bank] == [
        puzzle.puzzle for puzzle in second.base_bank
    ]
    assert [
        (puzzle.difficulty, puzzle.metadata["size"])
        for puzzle in first.base_bank
    ] == [
        ("easy", 4),
        ("medium", 5),
        ("hard", 6),
        ("expert", 7),
    ]

    for puzzle in first.base_bank:
        size, givens, inequalities = parse_puzzle(puzzle.puzzle)
        solutions = solve_futoshiki(size, givens, inequalities, limit=2)
        assert validate_structure(size, givens, inequalities) == []
        assert len(solutions) == 1
        assert solution_violations(size, givens, inequalities, solutions[0]) == []
        assert puzzle.ground_truth["solvability_status"] == "unique"


def test_d4_complement_variants_and_edge_cases_are_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("futoshiki", futoshiki_config(tmp_path))
    variants = [adapter.select_problem(scenario(), index) for index in range(16)]
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
    assert [puzzle.transformation for puzzle in variants] == [
        *d4,
        *(f"complement_{transformation}" for transformation in d4),
    ]
    for puzzle in variants:
        size, givens, inequalities = parse_puzzle(puzzle.puzzle)
        assert validate_structure(size, givens, inequalities) == []
        assert len(solve_futoshiki(size, givens, inequalities, limit=2)) == 1

    expected = {
        "invalid_constraints": ("invalid", "unknown", 0),
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
            "prompt": "Can you verify these Futoshiki inequalities?",
            "response": "I checked the Latin rows and columns and every strict inequality.",
            "board": puzzle.rendered_board,
        }
        assert adapter.validate(output, selected, puzzle).is_valid


def test_more_than_sixty_four_indexed_selections_are_unique_and_replayable(tmp_path):
    selection_count = 65
    first = FutoshikiPuzzleManager(
        resolve_domain_config(futoshiki_config(tmp_path / "first"), "futoshiki")
    )
    second = FutoshikiPuzzleManager(
        resolve_domain_config(futoshiki_config(tmp_path / "second"), "futoshiki")
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


def test_adapter_tools_validator_and_prompt_are_futoshiki_specific_and_compact(tmp_path):
    generator = ConversationGenerator(futoshiki_config(tmp_path))
    selected = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected, 0)
    tool_usage = generator.domain.maybe_use_tool(selected, puzzle)
    prompt = generator._build_generation_prompt(selected, puzzle, tool_usage)

    assert "Futoshiki" in generator.domain.prompts["system_prompt"]
    assert "inequality_analysis" in generator.domain.config["scenario"]["task_categories"]
    assert tool_usage["tool_name"] == "futoshiki_candidate_scan"
    assert "cell_candidates" in tool_usage["tool_output"]
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
            "prompt": "Which value is most constrained by these signs?",
            "response": "Compare its row and column candidates with both neighboring inequalities.",
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
                "prompt": "Help me reason about this Futoshiki grid.",
                "response": "Start with the cells constrained by both a Latin unit and an inequality.",
                "category": category,
            }
        )


def test_end_to_end_futoshiki_generation_persists_compatible_outputs(tmp_path):
    config = futoshiki_config(tmp_path)
    config["domains"]["futoshiki"]["scenario"].update(
        {
            "task_categories": ["inequality_analysis"],
            "difficulty_levels": ["easy"],
            "edge_cases": ["none"],
            "tool_usage_modes": ["inequality_scan"],
        }
    )
    generator = ConversationGenerator(config)
    generator.model_client = PromptAwareModel()

    result = generator.run(
        samples=1,
        conversation_type="single_turn",
        job_name="futoshiki-job",
    )
    sample_path = tmp_path / "futoshiki-job" / "samples.jsonl"
    sample = json.loads(sample_path.read_text(encoding="utf-8").splitlines()[0])
    csv_path = tmp_path / "futoshiki-job" / "single_turn.csv"
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result["domain"] == "futoshiki"
    assert result["accepted_samples"] == 1
    assert sample["domain"] == "futoshiki"
    assert sample["conversation_type"] == "single_turn"
    assert sample["puzzle_metadata"]["metadata"]["inequalities"]
    assert sample["ground_truth"]["solvability_status"] == "unique"
    assert sample["ground_truth"]["solution_grid"]
    assert sample["ground_truth"]["solution_violations"] == []
    assert sample["tool_usage_details"]["calls"]
    assert sample["output"]["board"].startswith("Futoshiki ")
    assert sample["output"]["prompt"] == "Help me reason about this Futoshiki grid."
    assert len(rows) == 1
    assert rows[0]["domain"] == "futoshiki"
    assert rows[0]["grid_size"] == "4"
    assert rows[0]["givens"]
    assert rows[0]["inequalities"]
    assert (tmp_path / "futoshiki_puzzle_bank.jsonl").exists()
    assert (tmp_path / "futoshiki_puzzle_usage.json").exists()
