from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .models import SimilarityResult


class SimilarityDiversityChecker:
    def __init__(self, config: dict[str, Any]):
        similarity_config = config.get("similarity", {})
        self.ngram_size = int(similarity_config.get("ngram_size", 3))
        self.max_history = int(similarity_config.get("max_history", 5000))
        self.related_puzzle_similarity = float(similarity_config.get("related_puzzle_similarity", 0.35))
        self.related_edge_case_puzzle_similarity = float(
            similarity_config.get("related_edge_case_puzzle_similarity", 0.55)
        )
        self.thresholds = {
            "exact_duplicate": float(similarity_config.get("exact_duplicate_threshold", 1.0)),
            "normalized_duplicate": float(similarity_config.get("normalized_duplicate_threshold", 1.0)),
            "ngram_overlap": float(similarity_config.get("ngram_overlap_threshold", 0.92)),
            "embedding_similarity": float(similarity_config.get("embedding_similarity_threshold", 0.96)),
            "structural_similarity": float(similarity_config.get("structural_similarity_threshold", 0.97)),
            "scenario_similarity": float(similarity_config.get("scenario_similarity_threshold", 0.95)),
            "puzzle_similarity": float(similarity_config.get("puzzle_similarity_threshold", 1.0)),
        }

    def assess(self, candidate: dict[str, Any], history: list[dict[str, Any]]) -> SimilarityResult:
        if not history:
            return SimilarityResult(accepted=True, similarity_score=0.0, metrics={})

        candidate_text = self._conversation_text(candidate)
        candidate_normalized = self._normalize_text(candidate_text)
        candidate_ngrams = self._ngrams(candidate_normalized)
        candidate_vector = self._token_vector(candidate_normalized)

        best_score = 0.0
        best_reasons: list[str] = []
        best_metrics: dict[str, float] = {}

        for existing in history[-self.max_history:]:
            existing_output = existing.get("output", {})
            existing_text = self._conversation_text(existing_output)
            existing_normalized = self._normalize_text(existing_text)
            existing_ngrams = self._ngrams(existing_normalized)
            existing_vector = self._token_vector(existing_normalized)

            exact_duplicate = 1.0 if candidate_text == existing_text else 0.0
            normalized_duplicate = 1.0 if candidate_normalized == existing_normalized else 0.0
            ngram_overlap = self._jaccard(candidate_ngrams, existing_ngrams)
            embedding_similarity = self._cosine_similarity(candidate_vector, existing_vector)
            structural_similarity = self._structural_similarity(candidate, existing_output)
            scenario_similarity = self._scenario_similarity(candidate.get("scenario", {}), existing.get("scenario", {}))
            puzzle_similarity = self._puzzle_similarity(candidate.get("puzzle_metadata", {}), existing.get("puzzle_metadata", {}))

            metrics = {
                "exact_duplicate": exact_duplicate,
                "normalized_duplicate": normalized_duplicate,
                "ngram_overlap": ngram_overlap,
                "embedding_similarity": embedding_similarity,
                "structural_similarity": structural_similarity,
                "scenario_similarity": scenario_similarity,
                "puzzle_similarity": puzzle_similarity,
            }
            score = max(metrics.values())
            if score > best_score:
                best_score = score
                best_metrics = metrics
                best_reasons = [name for name, value in metrics.items() if value >= self.thresholds.get(name, 1.1)]

            if best_reasons:
                return SimilarityResult(
                    accepted=False,
                    similarity_score=best_score,
                    reasons=best_reasons,
                    metrics=best_metrics,
                )

        return SimilarityResult(
            accepted=True,
            similarity_score=best_score,
            metrics=best_metrics,
        )

    def summarize_distribution(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        stats = {
            "task_distribution": Counter(),
            "difficulty_distribution": Counter(),
            "conversation_length_distribution": Counter(),
            "user_expertise_distribution": Counter(),
            "user_personality_distribution": Counter(),
            "assistant_style_distribution": Counter(),
            "tone_distribution": Counter(),
            "edge_case_distribution": Counter(),
            "tool_usage_distribution": Counter(),
            "puzzle_reuse_distribution": Counter(),
        }
        for sample in history:
            scenario = sample.get("scenario", {})
            puzzle = sample.get("puzzle_metadata", {})
            output = sample.get("output", {})
            stats["task_distribution"][scenario.get("task_category", "unknown")] += 1
            stats["difficulty_distribution"][scenario.get("difficulty", "unknown")] += 1
            stats["conversation_length_distribution"][str(sample.get("turns", 0))] += 1
            stats["user_expertise_distribution"][scenario.get("user_expertise", "unknown")] += 1
            stats["user_personality_distribution"][scenario.get("user_personality", "unknown")] += 1
            stats["assistant_style_distribution"][scenario.get("assistant_style", "unknown")] += 1
            stats["tone_distribution"][scenario.get("tone", "unknown")] += 1
            stats["edge_case_distribution"][scenario.get("edge_case", "unknown")] += 1
            stats["tool_usage_distribution"][scenario.get("tool_usage", "unknown")] += 1
            stats["puzzle_reuse_distribution"][puzzle.get("parent_puzzle_id") or puzzle.get("puzzle_id", "unknown")] += 1
            if output.get("conversation_type") == "multi_turn":
                stats["conversation_length_distribution"][str(len(output.get("messages", [])))] += 0
        return {key: dict(value) for key, value in stats.items()}

    def _conversation_text(self, output: dict[str, Any]) -> str:
        if output.get("conversation_type") == "multi_turn":
            return " ".join(
                f"{message.get('user', '')} {message.get('response', '')}"
                for message in output.get("messages", [])
            )
        return f"{output.get('prompt', '')} {output.get('response', '')}"

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def _ngrams(self, text: str) -> set[str]:
        if not text:
            return set()
        tokens = text.split()
        if len(tokens) < self.ngram_size:
            return {" ".join(tokens)}
        return {" ".join(tokens[index:index + self.ngram_size]) for index in range(len(tokens) - self.ngram_size + 1)}

    def _token_vector(self, text: str) -> Counter[str]:
        return Counter(text.split())

    def _jaccard(self, left: set[str], right: set[str]) -> float:
        if not left and not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _cosine_similarity(self, left: Counter[str], right: Counter[str]) -> float:
        if not left or not right:
            return 0.0
        common = set(left) & set(right)
        numerator = sum(left[token] * right[token] for token in common)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _structural_similarity(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        left_messages = left.get("messages", [])
        right_messages = right.get("messages", [])
        components = [
            1.0 if left.get("conversation_type") == right.get("conversation_type") else 0.0,
            1.0 if bool(left.get("board")) == bool(right.get("board")) else 0.0,
            1.0 if len(left_messages) == len(right_messages) else 0.0,
        ]
        return sum(components) / len(components)

    def _scenario_similarity(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        keys = [
            "user_intent",
            "conversation_type",
            "num_turns",
            "user_expertise",
            "user_personality",
            "assistant_style",
            "tone",
            "difficulty",
            "task_category",
            "edge_case",
            "tool_usage",
        ]
        matches = sum(1 for key in keys if left.get(key) == right.get(key))
        return matches / len(keys)

    def _puzzle_similarity(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        if not left or not right:
            return 0.0
        if left.get("puzzle_id") == right.get("puzzle_id"):
            return 1.0
        if left.get("puzzle") and left.get("puzzle") == right.get("puzzle"):
            return 1.0
        if left.get("rendered_board") and left.get("rendered_board") == right.get("rendered_board"):
            return 1.0

        same_parent = left.get("parent_puzzle_id") and left.get("parent_puzzle_id") == right.get("parent_puzzle_id")
        same_canonical = left.get("canonical_signature") and left.get("canonical_signature") == right.get("canonical_signature")
        if same_parent or same_canonical:
            left_edge_case = str(left.get("metadata", {}).get("edge_case_kind", ""))
            right_edge_case = str(right.get("metadata", {}).get("edge_case_kind", ""))
            if left_edge_case and left_edge_case == right_edge_case:
                return self.related_edge_case_puzzle_similarity
            return self.related_puzzle_similarity
        return 0.0
