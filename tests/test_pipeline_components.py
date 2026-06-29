from src.generator.diversity import SimilarityDiversityChecker
from src.generator.puzzles import PuzzleManager
from src.generator.scenario import ScenarioGenerator
from src.generator.validation import SampleValidator


def make_config(output_path: str) -> dict:
    return {
        "output_path": output_path,
        "generation": {"random_seed": 17},
        "scenario": {
            "task_categories": ["next_best_move", "hint"],
            "difficulty_levels": ["easy", "medium"],
            "user_expertise_levels": ["beginner", "advanced"],
            "user_personalities": ["careful", "curious"],
            "assistant_styles": ["coaching", "concise"],
            "tones": ["neutral", "friendly"],
            "edge_cases": ["none", "invalid_board"],
            "tool_usage_modes": ["none", "candidate_scan"],
        },
        "puzzles": {
            "persistent_bank_filename": "puzzle_bank.jsonl",
            "usage_stats_filename": "puzzle_usage.json",
        },
        "similarity": {
            "max_history": 10,
            "ngram_size": 2,
            "exact_duplicate_threshold": 1.0,
            "normalized_duplicate_threshold": 1.0,
            "ngram_overlap_threshold": 0.8,
            "embedding_similarity_threshold": 0.95,
            "structural_similarity_threshold": 1.0,
            "scenario_similarity_threshold": 1.0,
            "puzzle_similarity_threshold": 1.0,
        },
    }


def test_scenario_generator_prefers_underrepresented_categories(tmp_path):
    generator = ScenarioGenerator(make_config(str(tmp_path)))

    scenario = generator.generate(
        sample_index=0,
        conversation_type="single_turn",
        max_turns=1,
        distribution_stats={"task_distribution": {"next_best_move": 5, "hint": 0}},
    )

    assert scenario.task_category == "hint"


def test_puzzle_manager_returns_variant_with_parent_reference(tmp_path):
    manager = PuzzleManager(make_config(str(tmp_path)))
    scenario = ScenarioGenerator(make_config(str(tmp_path))).generate(
        sample_index=0,
        conversation_type="single_turn",
        max_turns=1,
        distribution_stats={},
    )

    puzzle = manager.select_puzzle(scenario, 0)

    assert puzzle.puzzle_id
    assert puzzle.parent_puzzle_id
    assert puzzle.rendered_board


def test_validator_rejects_wrong_board(tmp_path):
    config = make_config(str(tmp_path))
    scenario = ScenarioGenerator(config).generate(
        sample_index=0,
        conversation_type="single_turn",
        max_turns=1,
        distribution_stats={},
    )
    puzzle = PuzzleManager(config).select_puzzle(scenario, 0)

    result = SampleValidator().validate(
        {
            "conversation_type": "single_turn",
            "category": scenario.task_category,
            "prompt": "P",
            "response": "R",
            "board": "wrong board",
        },
        scenario,
        puzzle,
    )

    assert result.is_valid is False
    assert "output board does not match selected puzzle" in result.errors


def test_similarity_checker_flags_exact_duplicate(tmp_path):
    checker = SimilarityDiversityChecker(make_config(str(tmp_path)))
    candidate = {
        "scenario": {"task_category": "hint", "conversation_type": "single_turn"},
        "puzzle_metadata": {"puzzle_id": "a", "canonical_signature": "root", "parent_puzzle_id": "root"},
        "output": {"conversation_type": "single_turn", "prompt": "Same prompt", "response": "Same response"},
        "turns": 1,
    }
    history = [candidate]

    result = checker.assess(candidate, history)

    assert result.accepted is False
    assert result.similarity_score == 1.0
    assert result.reasons


def test_similarity_checker_allows_related_transformed_puzzles(tmp_path):
    checker = SimilarityDiversityChecker(make_config(str(tmp_path)))
    history = [
        {
            "scenario": {"task_category": "hint", "conversation_type": "single_turn"},
            "puzzle_metadata": {
                "puzzle_id": "variant-a",
                "parent_puzzle_id": "base-hard-1",
                "canonical_signature": "base-hard-1",
                "transformation": "swap_cols_within_stack",
                "metadata": {},
            },
            "output": {"conversation_type": "single_turn", "prompt": "Prompt A", "response": "Response A"},
            "turns": 1,
        }
    ]
    candidate = {
        "scenario": {"task_category": "validity_check", "conversation_type": "single_turn"},
        "puzzle_metadata": {
            "puzzle_id": "variant-b",
            "parent_puzzle_id": "base-hard-1",
            "canonical_signature": "base-hard-1",
            "transformation": "ambiguous_8",
            "metadata": {"edge_case_kind": "ambiguous_board"},
        },
        "output": {"conversation_type": "single_turn", "prompt": "Prompt B", "response": "Response B"},
        "turns": 1,
    }

    result = checker.assess(candidate, history)

    assert result.accepted is True
    assert result.metrics["puzzle_similarity"] < 1.0
