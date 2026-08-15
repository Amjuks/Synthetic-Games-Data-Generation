from __future__ import annotations

import hashlib

from src.generator.config import get_config
from src.generator.domains import get_domain_adapter
from src.generator.models import Scenario
from src.generator.wordle import (
    ALLOWED_WORDS,
    ANSWER_WORDS,
    EXPECTED,
    filter_candidates,
    hard_mode_valid,
    rank_guesses,
    score_feedback,
    validate_history,
)


def _scenario(task: str = "next_best_guess", edge: str = "none", difficulty: str = "easy") -> Scenario:
    return Scenario("wordle-test", task, "single_turn", 1, "advanced", "analytical", "coaching", "neutral", difficulty, task, edge, "none")


def test_vocabulary_counts_checksums_and_membership():
    assert len(ANSWER_WORDS) == 2315
    assert len(ALLOWED_WORDS) == 12972
    assert set(ANSWER_WORDS) <= set(ALLOWED_WORDS)
    assert hashlib.sha256("\n".join(ANSWER_WORDS).encode()).hexdigest() == EXPECTED["wordle_answers.txt"][1]
    assert hashlib.sha256("\n".join(ALLOWED_WORDS).encode()).hexdigest() == EXPECTED["wordle_allowed.txt"][1]


def test_duplicate_letter_feedback_uses_two_pass_allocation():
    assert score_feedback("apple", "alley") == "GYBYB"
    assert score_feedback("civic", "vivid") == "BGGGB"


def test_candidate_filter_history_validation_and_hard_mode():
    history = [{"guess": "alley", "feedback": score_feedback("apple", "alley")}]
    assert "apple" in filter_candidates(history)
    assert validate_history(history)["valid"]
    valid, errors = hard_mode_valid("ample", history)
    assert valid and not errors
    valid, errors = hard_mode_valid("cigar", history)
    assert not valid and errors
    impossible = validate_history([{"guess": "aahed", "feedback": "GGGGG"}])
    assert not impossible["valid"] and impossible["candidate_count"] == 0


def test_entropy_ranking_is_deterministic_and_prefers_smaller_worst_case():
    candidates = ("cigar", "rebut", "sissy", "humph")
    first = rank_guesses(candidates, 20)
    assert first == rank_guesses(candidates, 20)
    assert list(first) == sorted(first, key=lambda item: (-item["entropy_bits"], item["worst_case_remaining"], -item["possible_answer"], item["guess"]))


def test_edges_catalog_and_target_suppression(tmp_path):
    config = get_config(); config["output_path"] = str(tmp_path)
    adapter = get_domain_adapter("wordle", config)
    assert len(adapter.tool_catalog) == 13
    for index, edge in enumerate(("invalid_guess", "impossible_history", "hard_mode_violation", "repeated_guess")):
        puzzle = adapter.select_problem(_scenario(edge=edge), 20 + index)
        assert puzzle.ground_truth["validity_status"] == "invalid"
    exhausted = adapter.select_problem(_scenario(edge="exhausted_attempts"), 30)
    assert exhausted.ground_truth["attempts_remaining"] == 0
    assert not exhausted.ground_truth["solved"]

    hint = _scenario(task="hint")
    puzzle = adapter.select_problem(hint, 31)
    usage = adapter.maybe_use_tool(hint, puzzle)
    context = adapter.prompt_context(hint, puzzle, usage)
    assert puzzle.solution not in str(context)
    assert "target" not in str(context)


def test_explicit_word_solution_can_expose_target(tmp_path):
    config = get_config(); config["output_path"] = str(tmp_path)
    adapter = get_domain_adapter("wordle", config)
    scenario = _scenario(task="solve_word")
    puzzle = adapter.select_problem(scenario, 0)
    usage = adapter.maybe_use_tool(scenario, puzzle)
    assert puzzle.solution in str(adapter.prompt_context(scenario, puzzle, usage))
