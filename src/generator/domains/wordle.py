from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, compact_tool_context, make_tool_catalog
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator
from ..wordle import ALLOWED_WORDS, ANSWER_WORDS, WordlePuzzleManager, frequency_analysis, hard_mode_valid, parse_game, rank_guesses, score_feedback, validate_history


class WordleScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {"next_best_guess": "request_ranked_guess", "feedback_check": "verify_wordle_feedback", "rules_explanation": "learn_wordle_rules", "solve_word": "request_target_verification", "candidate_analysis": "inspect_remaining_answers", "opener_strategy": "compare_opening_words", "hard_mode_check": "validate_hard_mode_guess", "hint": "request_wordle_hint", "mistake_correction": "diagnose_wordle_mistake", "technique_discussion": "discuss_information_strategy", "beginner_question": "learn_wordle_basics", "advanced_question": "analyze_entropy", "general_chat": "general_wordle_help"}
        base = intents.get(task_category, task_category); return f"{base}_{edge_case}" if edge_case != "none" else base
    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = ["Use the supplied guesses and G/Y/B feedback exactly.", "Apply duplicate-letter feedback with green matches before yellow allocation.", "Do not reveal the target unless explicit solution verification is requested."]
        if task_category == "hint": constraints.append("Prefer letter or candidate guidance over naming the target.")
        if edge_case == "malformed_input": constraints.append("Diagnose malformed history without inventing feedback.")
        if tool_usage != "none": constraints.append(f"Use the requested evidence mode: {tool_usage}.")
        return constraints


class WordleValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors = []
        if output.get("conversation_type") != scenario.conversation_type: errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category: errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip(): errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip(): errors.append("single-turn response is empty")
        elif not isinstance(output.get("messages"), list) or not output["messages"]: errors.append("multi-turn messages list is empty")
        if scenario.edge_case != "malformed_input" and output.get("board") != puzzle.rendered_board: errors.append("output board does not match Wordle history")
        truth = puzzle.ground_truth; mutation = truth.get("mutation", {}).get("type")
        if scenario.edge_case == "none" and truth.get("validity_status") != "valid": errors.append("standard Wordle scenario requires a valid history")
        if scenario.edge_case in {"invalid_guess", "contradictory_feedback", "impossible_history", "hard_mode_violation", "repeated_guess"} and truth.get("validity_status") != "invalid": errors.append(f"{scenario.edge_case} is not validator-confirmed")
        if scenario.edge_case == "exhausted_attempts" and (truth.get("attempts_remaining") != 0 or truth.get("solved")): errors.append("exhausted_attempts is not an unsolved completed game")
        if scenario.edge_case != "none" and mutation != scenario.edge_case: errors.append("edge mutation does not match scenario")
        return ValidationResult(not errors, errors)


class WordleDomainAdapter(VariedDomainSupport):
    name = "wordle"
    tool_catalog = make_tool_catalog(name, {
        "wordle_input_validation": "Validate history shape, vocabulary membership, feedback encoding, and attempt count.",
        "wordle_feedback_scoring": "Recompute duplicate-aware feedback for an observed guess.",
        "wordle_history_validation": "Check whether one or more answers satisfy the complete history.",
        "wordle_candidate_filter": "Return all classic answer words consistent with the history.",
        "wordle_letter_constraint_analysis": "Derive green positions, yellow exclusions, and letter count bounds.",
        "wordle_frequency_analysis": "Measure letter presence and positional frequency in remaining answers.",
        "wordle_entropy_analysis": "Partition remaining answers and score guesses by information entropy.",
        "wordle_guess_ranking": "Rank the complete accepted-guess vocabulary deterministically.",
        "wordle_hard_mode_validation": "Check whether a guess reuses every revealed hint required by hard mode.",
        "wordle_duplicate_letter_analysis": "Explain minimum and maximum counts implied by mixed duplicate feedback.",
        "wordle_solution_verification": "Reveal and verify the stored target only for explicit solve requests.",
        "wordle_game_summary": "Summarize attempts, mode, candidate count, solved state, and vocabulary version.",
        "wordle_rules_reference": "Canonical five-letter, six-attempt, feedback, and hard-mode rules.",
    })
    def __init__(self, config: dict[str, Any]):
        self.config, self.prompts = config, config.get("prompts", {})
        self.scenario_generator, self.puzzle_manager, self.validator = WordleScenarioGenerator(config), WordlePuzzleManager(config), WordleValidator(); self._initialize_variety_support()
    def generate_scenario(self, **kwargs: Any) -> Scenario: return self.scenario_generator.generate(**kwargs)
    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord: return self._select_varied_problem(scenario, sample_index)
    def mark_problem_used(self, puzzle: PuzzleRecord) -> None: self.puzzle_manager.mark_used(puzzle)
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult: return self.validator.validate(output, scenario, puzzle)
    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]: return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "history": puzzle.puzzle}, output_for=lambda name: self._run_tool(name, puzzle))
    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]: return {"puzzle_id": puzzle.puzzle_id, "difficulty": puzzle.difficulty, "hard_mode": puzzle.metadata.get("hard_mode"), "guess_count": puzzle.metadata.get("guess_count"), "max_guesses": puzzle.metadata.get("max_guesses"), "candidate_count": puzzle.metadata.get("candidate_count"), "vocabulary_size": puzzle.metadata.get("vocabulary_size")}
    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {"validity_status": truth.get("validity_status"), "validation_errors": truth.get("validation_errors", [])[:10], "candidate_count": truth.get("candidate_count"), "candidates": truth.get("candidates", [])[:20], "constraints": truth.get("constraints", {}), "recommended_guess": truth.get("recommended_guess"), "solved": truth.get("solved"), "attempts_remaining": truth.get("attempts_remaining")}
    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        context = compact_tool_context(self.name, puzzle, tool_usage)
        if scenario.task_category == "solve_word" or scenario.tool_usage == "solution_verification":
            for target, original in zip(context["calls"], tool_usage.get("calls", [])):
                if original["tool_name"] == "wordle_solution_verification": target["output"] = original["output"]
            return context
        return self._hide_target(context, puzzle.solution)

    @classmethod
    def _hide_target(cls, value: Any, target: str) -> Any:
        """Remove persisted answer text from non-verification projections."""
        if isinstance(value, dict):
            return {key: cls._hide_target(item, target) for key, item in value.items() if key not in {"target", "solution"}}
        if isinstance(value, list):
            return [cls._hide_target(item, target) for item in value if item != target]
        if isinstance(value, tuple):
            return tuple(cls._hide_target(item, target) for item in value if item != target)
        return "[hidden answer]" if value == target else value
    def generation_guidance(self) -> str: return "Ground all claims in exact feedback and the classic vocabulary. Never expose the target outside explicit solution verification."
    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {}); row.update({"domain": sample.get("domain", self.name), "word_length": metadata.get("word_length"), "hard_mode": metadata.get("hard_mode"), "guess_count": metadata.get("guess_count"), "candidate_count": metadata.get("candidate_count"), "vocabulary_size": metadata.get("vocabulary_size"), "tool_used": sample.get("tool_used", False), "tool_name": sample.get("tool_usage_details", {}).get("tool_name")}); self._add_flat_tool_metadata(row, sample); return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["wordle_input_validation", "wordle_game_summary", "wordle_rules_reference"]
        if scenario.edge_case in {"invalid_guess", "contradictory_feedback", "impossible_history", "repeated_guess"}: return ["wordle_input_validation", "wordle_history_validation", "wordle_feedback_scoring"]
        if scenario.edge_case == "hard_mode_violation": return ["wordle_input_validation", "wordle_hard_mode_validation", "wordle_letter_constraint_analysis"]
        if scenario.edge_case == "exhausted_attempts": return ["wordle_game_summary", "wordle_history_validation", "wordle_candidate_filter"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["wordle_rules_reference", "wordle_game_summary", "wordle_duplicate_letter_analysis"]
        if scenario.task_category == "solve_word": return ["wordle_history_validation", "wordle_candidate_filter", "wordle_solution_verification"]
        if scenario.task_category == "feedback_check": return ["wordle_input_validation", "wordle_feedback_scoring", "wordle_duplicate_letter_analysis"]
        if scenario.task_category == "hard_mode_check": return ["wordle_letter_constraint_analysis", "wordle_hard_mode_validation", "wordle_candidate_filter"]
        if scenario.task_category in {"opener_strategy", "advanced_question"}: return ["wordle_frequency_analysis", "wordle_entropy_analysis", "wordle_guess_ranking"]
        if scenario.task_category in {"candidate_analysis", "technique_discussion"}: return ["wordle_candidate_filter", "wordle_letter_constraint_analysis", "wordle_frequency_analysis", "wordle_duplicate_letter_analysis"]
        if scenario.task_category == "mistake_correction": return ["wordle_history_validation", "wordle_feedback_scoring", "wordle_hard_mode_validation"]
        return ["wordle_candidate_filter", "wordle_frequency_analysis", "wordle_entropy_analysis", "wordle_guess_ranking"]

    def _run_tool(self, name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth; history, hard, maximum = parse_game(puzzle.puzzle); candidates = tuple(truth.get("candidates", []))
        if name == "wordle_input_validation": return {"validity_status": truth.get("validity_status"), "errors": truth.get("validation_errors", []), "allowed_vocabulary_size": len(ALLOWED_WORDS), "answer_vocabulary_size": len(ANSWER_WORDS)}
        if name == "wordle_feedback_scoring":
            item = history[-1] if history else None
            return {"guess": item.get("guess") if item else None, "recorded_feedback": item.get("feedback") if item else None, "recomputed_feedback": score_feedback(puzzle.solution, item["guess"]) if item and len(item.get("guess", "")) == 5 else None, "matches_target": bool(item and len(item.get("guess", "")) == 5 and score_feedback(puzzle.solution, item["guess"]) == item.get("feedback"))}
        if name == "wordle_history_validation": return {"validity_status": truth.get("validity_status"), "errors": truth.get("validation_errors", []), "candidate_count": truth.get("candidate_count")}
        if name == "wordle_candidate_filter": return {"candidate_count": len(candidates), "candidates": list(candidates)}
        if name == "wordle_letter_constraint_analysis": return truth.get("constraints", {})
        if name == "wordle_frequency_analysis": return frequency_analysis(candidates)
        if name in {"wordle_entropy_analysis", "wordle_guess_ranking"}:
            ranking = list(rank_guesses(candidates, 10))
            return {"candidate_count": len(candidates), "evaluated_guess_count": len(ALLOWED_WORDS), "ranking": ranking, "recommended_guess": ranking[0]["guess"] if ranking else None, "exact": True}
        if name == "wordle_hard_mode_validation":
            guess = truth.get("recommended_guess") or (history[-1]["guess"] if history else "")
            prefix = history[:-1] if truth.get("mutation", {}).get("type") == "hard_mode_violation" else history
            valid, errors = hard_mode_valid(guess, prefix); return {"guess": guess, "hard_mode": hard, "valid": valid, "violations": errors}
        if name == "wordle_duplicate_letter_analysis": return {"minimum_counts": truth.get("constraints", {}).get("minimum_counts", {}), "maximum_counts": truth.get("constraints", {}).get("maximum_counts", {}), "duplicate_guesses": [item for item in history if len(set(item.get("guess", ""))) < 5]}
        if name == "wordle_solution_verification": return {"target": puzzle.solution, "solution": puzzle.solution, "consistent_with_history": puzzle.solution in candidates, "verified": True}
        if name == "wordle_game_summary": return {"guess_count": len(history), "max_guesses": maximum, "attempts_remaining": truth.get("attempts_remaining"), "hard_mode": hard, "candidate_count": len(candidates), "solved": truth.get("solved"), "classic_vocabulary": {"allowed": len(ALLOWED_WORDS), "answers": len(ANSWER_WORDS)}}
        if name == "wordle_rules_reference": return {"rules": ["Guess an accepted five-letter word in at most six attempts.", "Green is correct position, yellow is present elsewhere, and gray is absent after duplicate allocation.", "Hard mode requires all revealed greens and yellow letter counts to be reused."]}
        return {}
