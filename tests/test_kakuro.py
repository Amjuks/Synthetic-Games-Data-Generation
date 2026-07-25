import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.kakuro import (
    KakuroPuzzleManager,
    parse_puzzle,
    run_combinations,
    run_permutations,
    solve_kakuro,
    validate_structure,
)
from src.generator.models import Scenario


def kakuro_config(tmp_path):
    config = get_config()
    config["domain"] = "kakuro"
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


def scenario(edge_case="none", difficulty="easy", task="run_analysis", tool="run_candidate_scan"):
    return Scenario(
        scenario_id="kakuro-test",
        user_intent="analyze_run_combinations",
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


def test_run_candidates_have_distinct_digits_and_exact_sum():
    combinations = run_combinations(2, 3)
    permutations = run_permutations(2, 3)

    assert combinations == ((1, 2),)
    assert set(permutations) == {(1, 2), (2, 1)}
    assert all(len(values) == len(set(values)) and sum(values) == 3 for values in permutations)


def test_generated_bank_is_deterministic_unique_and_covers_sizes(tmp_path):
    config = resolve_domain_config(kakuro_config(tmp_path), "kakuro")
    first = KakuroPuzzleManager(config)
    second = KakuroPuzzleManager(config)

    assert [puzzle.puzzle for puzzle in first.base_bank] == [puzzle.puzzle for puzzle in second.base_bank]
    assert [(puzzle.difficulty, puzzle.metadata["height"], puzzle.metadata["width"]) for puzzle in first.base_bank] == [
        ("easy", 5, 5),
        ("medium", 7, 7),
        ("hard", 9, 9),
        ("expert", 9, 9),
    ]
    for puzzle in first.base_bank:
        height, width, blocks, runs = parse_puzzle(puzzle.puzzle)
        assert validate_structure(height, width, blocks, runs) == []
        assert max(len(run["cells"]) for run in runs) <= 6
        assert len(solve_kakuro(height, width, blocks, runs, limit=2)) == 1
        assert puzzle.ground_truth["solvability_status"] == "unique"


def test_transpose_and_digit_relabel_preserve_uniqueness(tmp_path):
    manager = KakuroPuzzleManager(resolve_domain_config(kakuro_config(tmp_path), "kakuro"))
    variants = [manager.select_puzzle(scenario(), index) for index in range(3)]

    assert [puzzle.transformation for puzzle in variants] == ["identity", "transpose", "digit_relabel"]
    assert variants[2].solution != variants[0].solution
    for puzzle in variants:
        height, width, blocks, runs = parse_puzzle(puzzle.puzzle)
        assert validate_structure(height, width, blocks, runs) == []
        assert len(solve_kakuro(height, width, blocks, runs, limit=2)) == 1


def test_edge_variants_are_structure_and_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("kakuro", kakuro_config(tmp_path))
    expected = {
        "invalid_clues": ("invalid", "unknown"),
        "unsolvable_puzzle": ("valid", "unsolvable"),
        "ambiguous_puzzle": ("valid", "ambiguous"),
        "malformed_input": ("malformed", "unknown"),
    }
    for index, (edge_case, statuses) in enumerate(expected.items()):
        selected_scenario = scenario(edge_case=edge_case)
        puzzle = adapter.select_problem(selected_scenario, index)
        assert (puzzle.ground_truth["validity_status"], puzzle.ground_truth["solvability_status"]) == statuses
        output = {
            "conversation_type": "single_turn",
            "category": selected_scenario.task_category,
            "prompt": "Can you inspect this Kakuro?",
            "response": "I checked its clue sums and crossing runs.",
            "board": puzzle.rendered_board,
        }
        assert adapter.validate(output, selected_scenario, puzzle).is_valid


def test_profile_and_tools_are_kakuro_specific(tmp_path):
    config = kakuro_config(tmp_path)
    adapter = get_domain_adapter("kakuro", config)
    selected_scenario = scenario()
    puzzle = adapter.select_problem(selected_scenario, 0)
    tool = adapter.maybe_use_tool(selected_scenario, puzzle)

    assert "Kakuro" in adapter.prompts["system_prompt"]
    assert "run_analysis" in adapter.config["scenario"]["task_categories"]
    assert tool["tool_name"] == "kakuro_run_analysis"
    assert "run_candidate_combinations" in tool["tool_output"]
    assert "cell_candidates" in tool["tool_output"]


def test_largest_prompt_is_compact_and_does_not_duplicate_candidate_maps(tmp_path):
    generator = ConversationGenerator(kakuro_config(tmp_path))
    selected_scenario = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected_scenario, 0)
    tool = generator.domain.maybe_use_tool(selected_scenario, puzzle)

    prompt = generator._build_generation_prompt(selected_scenario, puzzle, tool)

    assert len(prompt) < 50_000
    assert prompt.count('"run_candidate_combinations"') == 1
    assert prompt.count("Ground Truth JSON:") == 1
    assert "You are a helpful Kakuro" in prompt


class PromptAwareModel:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt):
        self.prompt = prompt
        marker = '"task_category": "'
        category = prompt.split(marker, 1)[1].split('"', 1)[0]
        return json.dumps({
            "conversation_type": "single_turn",
            "category": category,
            "prompt": "Help me reason about this Kakuro.",
            "response": "Start with the run having the fewest distinct-digit combinations.",
        })


def test_end_to_end_kakuro_generation_writes_compatible_outputs(tmp_path):
    generator = ConversationGenerator(kakuro_config(tmp_path))
    model = PromptAwareModel()
    generator.model_client = model

    result = generator.run(samples=1, conversation_type="single_turn", job_name="kakuro-job")
    sample = json.loads((tmp_path / "kakuro-job" / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0])
    csv_text = (tmp_path / "kakuro-job" / "single_turn.csv").read_text(encoding="utf-8")

    assert result["domain"] == "kakuro"
    assert sample["domain"] == "kakuro"
    assert sample["puzzle_metadata"]["metadata"]["runs"]
    assert sample["ground_truth"]["run_evaluations"]
    assert sample["output"]["board"].startswith("Kakuro ")
    assert "grid_height" in csv_text and "grid_width" in csv_text and "runs" in csv_text
    assert (tmp_path / "kakuro_puzzle_bank.jsonl").exists()
    assert (tmp_path / "kakuro_puzzle_usage.json").exists()
