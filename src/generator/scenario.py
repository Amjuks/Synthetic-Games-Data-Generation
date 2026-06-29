from __future__ import annotations

import hashlib
import random
from typing import Any

from .models import Scenario


class ScenarioGenerator:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.scenario_config = config.get("scenario", {})
        self.seed = int(config.get("generation", {}).get("random_seed", 17))

    def generate(
        self,
        *,
        sample_index: int,
        conversation_type: str,
        max_turns: int,
        distribution_stats: dict[str, Any],
    ) -> Scenario:
        rng = random.Random(self.seed + sample_index * 97 + (1 if conversation_type == "multi_turn" else 0))
        task_category = self._pick_underrepresented(
            self.scenario_config.get("task_categories", []),
            distribution_stats.get("task_distribution", {}),
            rng,
        )
        difficulty = self._pick_underrepresented(
            self.scenario_config.get("difficulty_levels", []),
            distribution_stats.get("difficulty_distribution", {}),
            rng,
        )
        user_expertise = self._pick_underrepresented(
            self.scenario_config.get("user_expertise_levels", []),
            distribution_stats.get("user_expertise_distribution", {}),
            rng,
        )
        edge_case = self._pick_underrepresented(
            self.scenario_config.get("edge_cases", []),
            distribution_stats.get("edge_case_distribution", {}),
            rng,
        )
        user_personality = self._pick_underrepresented(
            self.scenario_config.get("user_personalities", []),
            distribution_stats.get("user_personality_distribution", {}),
            rng,
        )
        assistant_style = self._pick_underrepresented(
            self.scenario_config.get("assistant_styles", []),
            distribution_stats.get("assistant_style_distribution", {}),
            rng,
        )
        tone = self._pick_underrepresented(
            self.scenario_config.get("tones", []),
            distribution_stats.get("tone_distribution", {}),
            rng,
        )
        tool_usage = self._pick_underrepresented(
            self.scenario_config.get("tool_usage_modes", []),
            distribution_stats.get("tool_usage_distribution", {}),
            rng,
        )

        if task_category in {"rules_explanation", "general_chat", "technique_discussion"}:
            edge_case = "none"

        user_intent = self._derive_user_intent(task_category, edge_case)
        constraints = self._build_constraints(task_category, edge_case, tool_usage)
        num_turns = 1 if conversation_type == "single_turn" else rng.randint(2, max_turns)
        scenario_id = hashlib.sha1(
            "|".join(
                [
                    conversation_type,
                    task_category,
                    difficulty,
                    user_expertise,
                    user_personality,
                    assistant_style,
                    tone,
                    edge_case,
                    tool_usage,
                    str(sample_index),
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]

        return Scenario(
            scenario_id=scenario_id,
            user_intent=user_intent,
            conversation_type=conversation_type,
            num_turns=num_turns,
            user_expertise=user_expertise,
            user_personality=user_personality,
            assistant_style=assistant_style,
            tone=tone,
            difficulty=difficulty,
            task_category=task_category,
            edge_case=edge_case,
            tool_usage=tool_usage,
            constraints=constraints,
            metadata={"sample_index": sample_index},
        )

    def _pick_underrepresented(self, options: list[str], counts: dict[str, int], rng: random.Random) -> str:
        if not options:
            return "unknown"
        scored = sorted((counts.get(option, 0), option) for option in options)
        minimum = scored[0][0]
        candidates = [option for count, option in scored if count == minimum]
        return rng.choice(candidates)

    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intent_map = {
            "next_best_move": "ask_for_next_move",
            "validity_check": "verify_move",
            "rules_explanation": "learn_rules",
            "solve_puzzle": "request_solution_guidance",
            "solve_row_column_box": "focus_on_subgrid",
            "hint": "ask_for_hint",
            "mistake_correction": "debug_mistake",
            "technique_discussion": "discuss_strategy",
            "beginner_question": "learn_basics",
            "advanced_question": "analyze_advanced_pattern",
            "general_chat": "general_sudoku_help",
        }
        base_intent = intent_map.get(task_category, task_category)
        if edge_case != "none":
            return f"{base_intent}_{edge_case}"
        return base_intent

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = [
            "Use the supplied puzzle exactly unless malformed input is explicitly required.",
            "Keep the conversation realistic and grounded in Sudoku reasoning.",
        ]
        if task_category == "hint":
            constraints.append("Prefer giving guidance over full solutions.")
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed board before helping.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints
