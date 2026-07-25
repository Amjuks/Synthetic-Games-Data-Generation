import json

import pytest

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.jobs import JobManager
from src.generator.kenken import (
    KenKenPuzzleManager,
    cage_satisfied,
    parse_puzzle,
    solve_kenken,
    validate_structure,
)
from src.generator.models import Scenario


def kenken_config(tmp_path):
    config = get_config()
    config["domain"] = "kenken"
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


def scenario(edge_case="none", difficulty="easy", task="cage_analysis", tool="cage_candidate_scan"):
    return Scenario(
        scenario_id="kenken-test",
        user_intent="analyze_cage_combinations",
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


def test_cage_arithmetic_uses_order_independent_subtraction_and_exact_division():
    assert cage_satisfied((2, 5), "-", 3)
    assert cage_satisfied((2, 6), "/", 3)
    assert not cage_satisfied((2, 5), "/", 2)


def test_generated_bank_is_deterministic_unique_and_covers_requested_sizes(tmp_path):
    config = resolve_domain_config(kenken_config(tmp_path), "kenken")
    first = KenKenPuzzleManager(config)
    second = KenKenPuzzleManager(config)

    assert [puzzle.puzzle for puzzle in first.base_bank] == [puzzle.puzzle for puzzle in second.base_bank]
    assert [(puzzle.difficulty, puzzle.metadata["size"]) for puzzle in first.base_bank] == [
        ("easy", 4),
        ("medium", 5),
        ("hard", 6),
        ("expert", 6),
    ]
    for puzzle in first.base_bank:
        size, cages = parse_puzzle(puzzle.puzzle)
        assert validate_structure(size, cages) == []
        assert len(solve_kenken(size, cages, limit=2)) == 1
        assert puzzle.ground_truth["solvability_status"] == "unique"


def test_geometric_variants_preserve_unique_solution(tmp_path):
    manager = KenKenPuzzleManager(resolve_domain_config(kenken_config(tmp_path), "kenken"))
    for index in range(8):
        puzzle = manager.select_puzzle(scenario(), index)
        size, cages = parse_puzzle(puzzle.puzzle)
        assert validate_structure(size, cages) == []
        assert len(solve_kenken(size, cages, limit=2)) == 1
        assert puzzle.parent_puzzle_id


def test_edge_variants_are_solver_and_structure_confirmed(tmp_path):
    adapter = get_domain_adapter("kenken", kenken_config(tmp_path))
    manager = adapter.puzzle_manager
    expected = {
        "invalid_cages": ("invalid", "unknown"),
        "unsolvable_puzzle": ("valid", "unsolvable"),
        "ambiguous_puzzle": ("valid", "ambiguous"),
        "malformed_input": ("malformed", "unknown"),
    }
    for index, (edge_case, statuses) in enumerate(expected.items()):
        puzzle = manager.select_puzzle(scenario(edge_case=edge_case), index)
        assert (puzzle.ground_truth["validity_status"], puzzle.ground_truth["solvability_status"]) == statuses
        if edge_case == "invalid_cages":
            assert puzzle.ground_truth["structural_violations"]
        if edge_case == "malformed_input":
            assert len(puzzle.rendered_board.splitlines()) == 3
        selected_scenario = scenario(edge_case=edge_case)
        output = {
            "conversation_type": "single_turn",
            "category": selected_scenario.task_category,
            "prompt": "Can you check this puzzle?",
            "response": "I checked its cage and Latin-square constraints.",
            "board": puzzle.rendered_board,
        }
        assert adapter.validate(output, selected_scenario, puzzle).is_valid


def test_domain_profiles_keep_prompts_and_scenarios_isolated(tmp_path):
    config = kenken_config(tmp_path)
    sudoku = get_domain_adapter("sudoku", config)
    kenken = get_domain_adapter("kenken", config)

    assert "Sudoku" in sudoku.prompts["system_prompt"]
    assert "KenKen" in kenken.prompts["system_prompt"]
    assert "cage_analysis" in kenken.config["scenario"]["task_categories"]
    assert "cage_analysis" not in sudoku.config["scenario"]["task_categories"]


def test_adapter_tools_and_validation_use_kenken_ground_truth(tmp_path):
    adapter = get_domain_adapter("kenken", kenken_config(tmp_path))
    selected_scenario = scenario()
    puzzle = adapter.select_problem(selected_scenario, 0)
    tool = adapter.maybe_use_tool(selected_scenario, puzzle)
    output = {
        "conversation_type": "single_turn",
        "category": "cage_analysis",
        "prompt": "Which cage should I inspect?",
        "response": "Start with the most constrained cage.",
        "board": puzzle.rendered_board,
    }

    assert tool["tool_name"] == "kenken_cage_analysis"
    assert "cage_candidate_tuples" in tool["tool_output"]
    assert adapter.validate(output, selected_scenario, puzzle).is_valid
    output["board"] = "wrong"
    assert not adapter.validate(output, selected_scenario, puzzle).is_valid


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
            "prompt": "Help me reason about this KenKen.",
            "response": "Use the cage clue and row uniqueness together.",
        })


def test_end_to_end_kenken_generation_writes_compatible_dataset(tmp_path):
    generator = ConversationGenerator(kenken_config(tmp_path))
    model = PromptAwareModel()
    generator.model_client = model

    result = generator.run(samples=1, conversation_type="single_turn", job_name="kenken-job")
    sample = json.loads((tmp_path / "kenken-job" / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert result["domain"] == "kenken"
    assert sample["domain"] == "kenken"
    assert sample["puzzle_metadata"]["metadata"]["size"] in {4, 5, 6}
    assert sample["ground_truth"]["cages"]
    assert sample["output"]["board"].startswith("KenKen ")
    assert "You are a helpful KenKen assistant" in model.prompt
    assert (tmp_path / "kenken-job" / "single_turn.csv").exists()
    assert (tmp_path / "kenken_puzzle_bank.jsonl").exists()


def test_existing_job_can_resume_with_different_settings(tmp_path):
    config = kenken_config(tmp_path)
    JobManager(job_name="domain-locked", config=config).save_status(
        domain="sudoku",
        conversation_type="both",
        max_turns=9,
    )
    generator = ConversationGenerator(config)

    generator.run(samples=0, conversation_type="single_turn", max_turns=4, job_name="domain-locked")

    progress = json.loads((tmp_path / "domain-locked" / "progress.json").read_text(encoding="utf-8"))
    assert progress["domain"] == "kenken"
    assert progress["conversation_type"] == "single_turn"
    assert progress["max_turns"] == 4
    assert progress["resume_configuration_changes"] == {
        "domain": {"previous": "sudoku", "current": "kenken"},
        "conversation_type": {"previous": "both", "current": "single_turn"},
        "max_turns": {"previous": 9, "current": 4},
    }
    assert progress["configuration_history"][-1]["changes"] == progress["resume_configuration_changes"]
    log_text = (tmp_path / "domain-locked" / "generation.log").read_text(encoding="utf-8")
    assert "resume_configuration_changed" in log_text


def test_largest_kenken_prompts_use_compact_solver_projections(tmp_path):
    generator = ConversationGenerator(kenken_config(tmp_path))
    cases = [
        scenario(difficulty="expert", task="cage_analysis", tool="cage_candidate_scan"),
        scenario(
            edge_case="ambiguous_puzzle",
            difficulty="expert",
            task="validity_check",
            tool="cage_constraint_check",
        ),
        scenario(difficulty="expert", task="solve_puzzle", tool="solution_verification"),
    ]

    for index, selected_scenario in enumerate(cases):
        puzzle = generator.domain.select_problem(selected_scenario, index)
        tool_usage = generator.domain.maybe_use_tool(selected_scenario, puzzle)
        prompt = generator._build_generation_prompt(selected_scenario, puzzle, tool_usage)

        assert len(prompt) < 10_000
        assert '"cages"' not in prompt
        assert "cage_candidate_tuples" not in prompt
        assert prompt.count("Cage layout:") == 1
        # Prompt compaction must not alter complete persisted ground truth.
        assert "cages" in puzzle.ground_truth
        assert "cage_candidate_tuples" in puzzle.ground_truth


def test_prompt_budget_stops_request_before_model_call(tmp_path):
    config = kenken_config(tmp_path)
    config["generation"]["max_prompt_characters"] = 100
    generator = ConversationGenerator(config)
    selected_scenario = scenario()
    puzzle = generator.domain.select_problem(selected_scenario, 0)
    tool_usage = generator.domain.maybe_use_tool(selected_scenario, puzzle)

    class ModelThatMustNotRun:
        called = False

        def generate(self, prompt):
            self.called = True
            raise AssertionError("model should not be called")

    model = ModelThatMustNotRun()
    generator.model_client = model
    with pytest.raises(ValueError, match="model API was not called"):
        generator._generate_output(selected_scenario, puzzle, tool_usage)
    assert not model.called
