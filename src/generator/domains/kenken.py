from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage
from ..kenken import KenKenPuzzleManager, parse_puzzle, validate_structure
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class KenKenScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intent_map = {
            "next_best_move": "ask_for_next_deduction",
            "validity_check": "verify_value_or_cage",
            "rules_explanation": "learn_kenken_rules",
            "solve_puzzle": "request_solution_guidance",
            "cage_analysis": "analyze_cage_combinations",
            "hint": "ask_for_hint",
            "mistake_correction": "debug_constraint_mistake",
            "technique_discussion": "discuss_kenken_strategy",
            "beginner_question": "learn_kenken_basics",
            "advanced_question": "analyze_constraint_interaction",
            "general_chat": "general_kenken_help",
        }
        base = intent_map.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = [
            "Use the supplied KenKen cage layout exactly unless malformed input is explicitly required.",
            "Ground every claim in cage arithmetic and row/column uniqueness.",
        ]
        if task_category == "hint":
            constraints.append("Prefer a deduction or constrained cage over revealing the full solution.")
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's KenKen assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed puzzle representation before helping.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class KenKenValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        errors: list[str] = []
        if output.get("conversation_type") != scenario.conversation_type:
            errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category:
            errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip():
                errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip():
                errors.append("single-turn response is empty")
        else:
            messages = output.get("messages", [])
            if not isinstance(messages, list) or not messages:
                errors.append("multi-turn messages list is empty")
            else:
                for index, message in enumerate(messages):
                    if not str(message.get("user", "")).strip():
                        errors.append(f"message {index} is missing user text")
                    if not str(message.get("response", "")).strip():
                        errors.append(f"message {index} is missing response text")
        board = output.get("board", "")
        if scenario.edge_case == "malformed_input":
            if not board:
                errors.append("malformed_input scenario requires a malformed puzzle string")
        elif board != puzzle.rendered_board:
            errors.append("output board does not match selected puzzle")

        size, cages = parse_puzzle(puzzle.puzzle)
        violations = validate_structure(size, cages)
        truth = puzzle.ground_truth
        if scenario.edge_case == "none":
            if violations:
                errors.append("standard scenario has structurally invalid cages")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique KenKen puzzle")
        if truth.get("solution") != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_cages" and not violations:
            errors.append("invalid_cages scenario has no structural violation")
        if scenario.edge_case == "unsolvable_puzzle" and truth.get("solvability_status") != "unsolvable":
            errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if scenario.edge_case == "ambiguous_puzzle" and truth.get("solvability_status") != "ambiguous":
            errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class KenKenDomainAdapter(VariedDomainSupport):
    name = "kenken"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = KenKenScenarioGenerator(config)
        self.puzzle_manager = KenKenPuzzleManager(config)
        self.validator = KenKenValidator()
        self._initialize_variety_support()

    def generate_scenario(
        self,
        *,
        sample_index: int,
        conversation_type: str,
        max_turns: int,
        distribution_stats: dict[str, Any],
    ) -> Scenario:
        return self.scenario_generator.generate(
            sample_index=sample_index,
            conversation_type=conversation_type,
            max_turns=max_turns,
            distribution_stats=distribution_stats,
        )

    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        return self._select_varied_problem(scenario, sample_index)

    def mark_problem_used(self, puzzle: PuzzleRecord) -> None:
        self.puzzle_manager.mark_used(puzzle)

    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "size": puzzle.metadata.get("size"), "cages": puzzle.metadata.get("cages", [])}, output_for=lambda name: self._run_tool(name, puzzle))

    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain": self.name,
            "tools_required": puzzle.metadata.get("tools_required", []),
            "calls": [self._compact_tool_usage({"used": True, "tool_name": call["tool_name"], "tool_input": call["input"], "tool_output": call["output"], "reason": call["reason"]}, puzzle) for call in tool_usage.get("calls", [])],
        }

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        # The rendered board below the JSON sections is the one canonical cage
        # representation supplied to the model.  Do not repeat puzzle.puzzle or
        # metadata.cages here: candidate-heavy KenKen requests otherwise grow by
        # tens or hundreds of thousands of characters.
        return {
            "puzzle_id": puzzle.puzzle_id,
            "size": puzzle.metadata.get("size"),
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "transformation": puzzle.metadata.get("transformation"),
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        # Full ground truth remains in samples.jsonl.  The model only needs one
        # solved-grid representation and the conclusions required to ground its
        # response; cages already appear in the rendered board.
        return {
            "solution_grid": truth.get("solution_grid"),
            "validity_status": truth.get("validity_status"),
            "solvability_status": truth.get("solvability_status"),
            "unique_solution_status": truth.get("unique_solution_status"),
            "solution_count": truth.get("solution_count"),
            "structural_violations": list(truth.get("structural_violations", []))[:20],
            "forced_values": self._limited_mapping(truth.get("forced_values", {}), 12),
            "suggested_move": self._compact_suggested_move(truth.get("suggested_move")),
        }

    def _compact_tool_usage(self, tool_usage: dict[str, Any], puzzle: PuzzleRecord) -> dict[str, Any]:
        tool_input = dict(tool_usage.get("tool_input") or {})
        tool_input.pop("cages", None)
        compact = {
            "used": bool(tool_usage.get("used")),
            "tool_name": tool_usage.get("tool_name"),
            "reason": tool_usage.get("reason"),
            "tool_input": tool_input or None,
        }
        output = dict(tool_usage.get("tool_output") or {})
        tuples = output.get("cage_candidate_tuples")
        if isinstance(tuples, dict):
            selected_ids = self._select_prompt_cages(puzzle, tuples)
            cage_lookup = {
                str(cage.get("id")): cage
                for cage in puzzle.metadata.get("cages", [])
            }
            output["cage_candidate_summary"] = {
                cage_id: {
                    "count": len(values),
                    "examples": values[:8],
                    "clue": {
                        "target": cage_lookup.get(cage_id, {}).get("target"),
                        "operation": cage_lookup.get(cage_id, {}).get("operation"),
                        "cells": cage_lookup.get(cage_id, {}).get("cells", []),
                    },
                }
                for cage_id in selected_ids
                for values in [tuples[cage_id]]
            }
            output["candidate_summary_scope"] = {
                "included_cages": len(selected_ids),
                "total_cages": len(tuples),
            }
            output.pop("cage_candidate_tuples", None)
            output["forced_values"] = self._limited_mapping(output.get("forced_values", {}), 12)

        if "suggested_move" in output:
            output["suggested_move"] = self._compact_suggested_move(output.get("suggested_move"))

        evaluations = output.get("cage_evaluations")
        if isinstance(evaluations, list):
            failed = [item for item in evaluations if not item.get("satisfied", False)]
            output["cage_evaluations"] = (failed or evaluations)[:8]
            output["cage_evaluation_scope"] = {
                "included": len(output["cage_evaluations"]),
                "total": len(evaluations),
                "failed_only": bool(failed),
            }

        # The compact ground-truth projection already carries the solved grid.
        # Keep only the tool's verification conclusion, not a second solution.
        if compact.get("tool_name") == "kenken_solution_verification":
            output.pop("solution", None)
            output.pop("solution_grid", None)
        compact["tool_output"] = output
        return compact

    def _select_prompt_cages(self, puzzle: PuzzleRecord, tuples: dict[str, list[Any]]) -> list[str]:
        selected: list[str] = []
        suggested = puzzle.ground_truth.get("suggested_move") or {}
        suggested_cell = suggested.get("cell")
        for cage in puzzle.metadata.get("cages", []):
            cage_id = str(cage.get("id"))
            if suggested_cell in cage.get("cells", []) and cage_id in tuples:
                selected.append(cage_id)

        # Fill the small projection with the most constrained remaining cages.
        for cage_id in sorted(tuples, key=lambda item: (len(tuples[item]), item)):
            if cage_id not in selected:
                selected.append(cage_id)
            if len(selected) >= 3:
                break
        return selected[:3]

    @staticmethod
    def _limited_mapping(value: Any, limit: int) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return dict(sorted(value.items())[:limit])

    @staticmethod
    def _compact_suggested_move(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        compact = dict(value)
        candidates = compact.pop("candidate_tuples", None)
        if isinstance(candidates, list):
            compact["candidate_tuple_summary"] = {
                "count": len(candidates),
                "examples": candidates[:8],
            }
        return compact

    def generation_guidance(self) -> str:
        return (
            "Use the supplied KenKen size and cage layout exactly. Base all arithmetic on the given cage "
            "targets and operations, and never modify the representation unless malformed input is required."
        )

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {})
        row["domain"] = sample.get("domain", self.name)
        row["grid_size"] = metadata.get("size")
        row["cages"] = metadata.get("cages", [])
        row["tool_used"] = sample.get("tool_used", False)
        row["tool_name"] = sample.get("tool_usage_details", {}).get("tool_name")
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["kenken_rules_reference", "kenken_puzzle_summary"]
        primary = self._tool_name_for_scenario(scenario)
        if primary == "kenken_solution_verification": return ["kenken_cage_analysis", primary]
        if primary == "kenken_cage_analysis": return [primary, "kenken_constraint_validation"]
        if primary == "kenken_constraint_validation": return [primary, "kenken_cage_analysis"]
        return [primary or "kenken_puzzle_summary", "kenken_constraint_validation"]

    def _tool_name_for_scenario(self, scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_cages", "unsolvable_puzzle", "ambiguous_puzzle"}:
            return "kenken_constraint_validation"
        if scenario.tool_usage == "cage_candidate_scan" or scenario.task_category in {"hint", "next_best_move", "cage_analysis"}:
            return "kenken_cage_analysis"
        if scenario.tool_usage == "cage_constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}:
            return "kenken_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "kenken_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        if tool_name == "kenken_cage_analysis":
            return {
                "cage_candidate_tuples": truth.get("cage_candidate_tuples", {}),
                "forced_values": truth.get("forced_values", {}),
                "suggested_move": truth.get("suggested_move"),
            }
        if tool_name == "kenken_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "cage_evaluations": truth.get("cage_evaluations", []),
            }
        if tool_name == "kenken_solution_verification":
            return {
                "solution": truth.get("solution"),
                "solution_grid": truth.get("solution_grid"),
                "unique_solution_status": truth.get("unique_solution_status"),
            }
        if tool_name == "kenken_rules_reference": return {"rules": ["Use each number once per row and column.", "Every cage must satisfy its target and operation."]}
        if tool_name == "kenken_puzzle_summary": return {"size": puzzle.metadata.get("size"), "cage_count": len(puzzle.metadata.get("cages", [])), "difficulty": puzzle.difficulty, "strategies": puzzle.required_strategies}
        return {}

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "kenken_cage_analysis": "The scenario needs cage combinations or a constrained next deduction.",
            "kenken_constraint_validation": "The scenario needs deterministic cage and Latin-square checks.",
            "kenken_solution_verification": "The scenario needs solver-backed solution verification.",
        }
        return reasons.get(tool_name, f"The {scenario.task_category} scenario requested KenKen tool support.")
