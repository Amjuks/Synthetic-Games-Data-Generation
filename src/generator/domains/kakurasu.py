from __future__ import annotations

import json
from typing import Any

from ..domain_support import (
    VariedDomainSupport,
    build_tool_usage,
    compact_tool_context,
    make_tool_catalog,
)
from ..kakurasu import (
    KakurasuPuzzleManager,
    parse_puzzle,
    solve_kakurasu,
    validate_structure,
)
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class KakurasuScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {
            "next_best_move": "ask_for_next_weighted_selection",
            "validity_check": "verify_kakurasu_selection_or_target",
            "rules_explanation": "learn_kakurasu_rules",
            "solve_puzzle": "request_kakurasu_solution_guidance",
            "weighted_sum_analysis": "analyze_weighted_subset_patterns",
            "hint": "ask_for_kakurasu_hint",
            "mistake_correction": "debug_weighted_sum_mistake",
            "technique_discussion": "discuss_kakurasu_strategy",
            "beginner_question": "learn_kakurasu_basics",
            "advanced_question": "analyze_cross_line_subset_constraints",
            "general_chat": "general_kakurasu_help",
        }
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(
        self,
        task_category: str,
        edge_case: str,
        tool_usage: str,
    ) -> list[str]:
        constraints = [
            "Use the supplied Kakurasu dimensions and row/column targets exactly unless malformed input is explicitly required.",
            "Ground every claim in the fixed 1-based row and column weights and both crossing target sums.",
        ]
        if task_category == "hint":
            constraints.append(
                "Prefer one forced selected or unselected cell over revealing the complete mask."
            )
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Kakurasu weighting assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed target grid before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class KakurasuValidator:
    def validate(
        self,
        output: dict[str, Any],
        scenario: Scenario,
        puzzle: PuzzleRecord,
    ) -> ValidationResult:
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
                    if not isinstance(message, dict):
                        errors.append(f"message {index} is not an object")
                        continue
                    if not str(message.get("user", "")).strip():
                        errors.append(f"message {index} is missing user text")
                    if not str(message.get("response", "")).strip():
                        errors.append(f"message {index} is missing response text")

        board = output.get("board", "")
        if scenario.edge_case == "malformed_input":
            if not str(board).strip():
                errors.append("malformed_input scenario requires a malformed puzzle string")
        elif board != puzzle.rendered_board:
            errors.append("output board does not match selected puzzle")

        structural: list[str] = []
        parse_error: Exception | None = None
        try:
            height, width, row_targets, column_targets = parse_puzzle(puzzle.puzzle)
            structural = validate_structure(height, width, row_targets, column_targets)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parse_error = exc

        truth = puzzle.ground_truth
        if parse_error is not None and scenario.edge_case != "malformed_input":
            errors.append("selected puzzle cannot be parsed as canonical Kakurasu JSON")
        if scenario.edge_case == "none":
            if structural:
                errors.append("standard scenario has structurally invalid Kakurasu targets")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique Kakurasu puzzle")
        if truth.get("solution", puzzle.solution) != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_targets" and not structural:
            errors.append("invalid_targets scenario has no structural violation")
        if (
            scenario.edge_case == "unsolvable_puzzle"
            and truth.get("solvability_status") != "unsolvable"
        ):
            errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if (
            scenario.edge_case == "ambiguous_puzzle"
            and truth.get("solvability_status") != "ambiguous"
        ):
            errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class KakurasuDomainAdapter(VariedDomainSupport):
    name = "kakurasu"
    tool_catalog = make_tool_catalog(
        name,
        {
            "kakurasu_subset_analysis": (
                "Legal weighted row/column subsets, forced cells, and a next deduction."
            ),
            "kakurasu_constraint_validation": (
                "Dimensions, targets, weighted sums, structure, and solver validation."
            ),
            "kakurasu_solution_verification": (
                "Complete solver-backed selected-cell mask verification."
            ),
            "kakurasu_rules_reference": "Canonical Kakurasu positional-weight rules.",
            "kakurasu_puzzle_summary": "Dimensions, targets, fixed weights, and difficulty.",
            "kakurasu_row_pattern_analysis": (
                "Weighted subset counts and examples for every row target."
            ),
            "kakurasu_column_pattern_analysis": (
                "Weighted subset counts and examples for every column target."
            ),
            "kakurasu_weight_contribution_analysis": (
                "Per-cell row and column contributions under positional weights."
            ),
            "kakurasu_move_impact_analysis": (
                "Target residuals and subset eliminations caused by a selection decision."
            ),
            "kakurasu_solution_space_analysis": (
                "Capped solution count and ambiguous mask differences."
            ),
        },
    )

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = KakurasuScenarioGenerator(config)
        self.puzzle_manager = KakurasuPuzzleManager(config)
        self.validator = KakurasuValidator()
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

    def validate(
        self,
        output: dict[str, Any],
        scenario: Scenario,
        puzzle: PuzzleRecord,
    ) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def maybe_use_tool(
        self,
        scenario: Scenario,
        puzzle: PuzzleRecord,
    ) -> dict[str, Any]:
        return build_tool_usage(
            scenario=scenario,
            puzzle=puzzle,
            tool_names=self._tool_bundle(scenario),
            input_for=lambda name: {
                "puzzle_id": puzzle.puzzle_id,
                "task_category": scenario.task_category,
                "edge_case": scenario.edge_case,
                "height": puzzle.metadata.get("height"),
                "width": puzzle.metadata.get("width"),
            },
            output_for=lambda name: self._run_tool(name, puzzle),
        )

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        height, width, row_targets, column_targets = self._puzzle_parts(puzzle)
        return {
            "puzzle_id": puzzle.puzzle_id,
            "height": height,
            "width": width,
            "row_targets": row_targets,
            "column_targets": column_targets,
            "row_weights": list(range(1, height + 1)),
            "column_weights": list(range(1, width + 1)),
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "transformation": puzzle.transformation,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        row_patterns, column_patterns = self._pattern_maps(puzzle)
        return {
            "solution": truth.get("solution", puzzle.solution),
            "validity_status": truth.get("validity_status"),
            "solvability_status": truth.get("solvability_status"),
            "unique_solution_status": truth.get("unique_solution_status"),
            "solution_count": truth.get("solution_count"),
            "structural_violations": truth.get("structural_violations", [])[:16],
            "solution_violations": truth.get("solution_violations", [])[:16],
            "row_evaluations": truth.get("row_evaluations", [])[:12],
            "column_evaluations": truth.get("column_evaluations", [])[:12],
            "row_pattern_counts": self._pattern_counts(
                row_patterns,
                truth.get("row_pattern_counts"),
            ),
            "column_pattern_counts": self._pattern_counts(
                column_patterns,
                truth.get("column_pattern_counts"),
            ),
            "forced_selected": truth.get("forced_selected", [])[:20],
            "forced_unselected": truth.get("forced_unselected", [])[:20],
            "weighted_contributions": self._limited(
                truth.get("weighted_contributions", []),
                20,
            ),
            "suggested_move": self._suggested_move(truth),
        }

    def prompt_context(
        self,
        scenario: Scenario,
        puzzle: PuzzleRecord,
        tool_usage: dict[str, Any],
    ) -> dict[str, Any]:
        del scenario
        context = compact_tool_context(self.name, puzzle, tool_usage)
        for call in context.get("calls", []):
            output = dict(call.get("output") or {})
            row_patterns = output.pop("row_patterns", None)
            if isinstance(row_patterns, (dict, list)):
                output["row_pattern_summary"] = self._compact_pattern_map(
                    row_patterns,
                    "row",
                )
            column_patterns = output.pop("column_patterns", None)
            if isinstance(column_patterns, (dict, list)):
                output["column_pattern_summary"] = self._compact_pattern_map(
                    column_patterns,
                    "column",
                )
            for key in (
                "forced_selected",
                "forced_unselected",
                "contributions",
                "eliminated_patterns",
                "eliminations",
            ):
                if key in output:
                    output[key] = self._limited(output[key], 20)
            output.pop("solved_board", None)
            call["output"] = output
        return context

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Kakurasu targets exactly. Selecting rNcM contributes column weight M "
            "to row N and row weight N to column M; unselected cells contribute zero. Every row and "
            "column must reach its target simultaneously."
        )

    def flatten_sample_row(
        self,
        row: dict[str, Any],
        sample: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {})
        height = int(metadata.get("height") or 0)
        width = int(metadata.get("width") or 0)
        row.update(
            {
                "domain": sample.get("domain", self.name),
                "grid_height": height,
                "grid_width": width,
                "row_targets": metadata.get("row_targets", []),
                "column_targets": metadata.get("column_targets", []),
                "row_weights": list(range(1, height + 1)),
                "column_weights": list(range(1, width + 1)),
                "tool_used": sample.get("tool_used", False),
                "tool_name": sample.get("tool_usage_details", {}).get("tool_name"),
            }
        )
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input":
            return [
                "kakurasu_constraint_validation",
                "kakurasu_puzzle_summary",
                "kakurasu_rules_reference",
            ]
        if scenario.edge_case in {
            "invalid_targets",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return [
                "kakurasu_constraint_validation",
                "kakurasu_row_pattern_analysis",
                "kakurasu_solution_space_analysis",
            ]
        if scenario.task_category in {
            "rules_explanation",
            "beginner_question",
            "general_chat",
        }:
            return [
                "kakurasu_rules_reference",
                "kakurasu_puzzle_summary",
                "kakurasu_weight_contribution_analysis",
            ]
        if scenario.task_category in {"technique_discussion", "advanced_question"}:
            return [
                "kakurasu_row_pattern_analysis",
                "kakurasu_column_pattern_analysis",
                "kakurasu_weight_contribution_analysis",
                "kakurasu_move_impact_analysis",
            ]

        primary = self._tool_name_for_scenario(scenario)
        if primary == "kakurasu_solution_verification":
            return [
                "kakurasu_subset_analysis",
                "kakurasu_weight_contribution_analysis",
                "kakurasu_solution_space_analysis",
                primary,
            ]
        if primary == "kakurasu_subset_analysis":
            return [
                primary,
                "kakurasu_row_pattern_analysis",
                "kakurasu_column_pattern_analysis",
                "kakurasu_move_impact_analysis",
            ]
        if primary == "kakurasu_constraint_validation":
            return [
                primary,
                "kakurasu_weight_contribution_analysis",
                "kakurasu_solution_space_analysis",
            ]
        return [primary or "kakurasu_puzzle_summary", "kakurasu_constraint_validation"]

    @staticmethod
    def _tool_name_for_scenario(scenario: Scenario) -> str | None:
        if scenario.edge_case in {
            "invalid_targets",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return "kakurasu_constraint_validation"
        if scenario.tool_usage == "weighted_sum_scan" or scenario.task_category in {
            "hint",
            "next_best_move",
            "weighted_sum_analysis",
        }:
            return "kakurasu_subset_analysis"
        if scenario.tool_usage == "constraint_check" or scenario.task_category in {
            "validity_check",
            "mistake_correction",
        }:
            return "kakurasu_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "kakurasu_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        height, width, row_targets, column_targets = self._puzzle_parts(puzzle)
        row_patterns, column_patterns = self._pattern_maps(puzzle)
        suggested_move = self._suggested_move(truth)

        if tool_name == "kakurasu_subset_analysis":
            return {
                "row_patterns": row_patterns,
                "column_patterns": column_patterns,
                "forced_selected": truth.get(
                    "forced_selected",
                    self._forced_cells(row_patterns, column_patterns, height, width, True),
                ),
                "forced_unselected": truth.get(
                    "forced_unselected",
                    self._forced_cells(row_patterns, column_patterns, height, width, False),
                ),
                "suggested_move": suggested_move,
            }
        if tool_name == "kakurasu_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "solution_violations": truth.get("solution_violations", []),
                "row_evaluations": truth.get("row_evaluations", []),
                "column_evaluations": truth.get("column_evaluations", []),
            }
        if tool_name == "kakurasu_solution_verification":
            return {
                "solution": truth.get("solution", puzzle.solution),
                "solution_mask": truth.get("solution_mask", []),
                "selected_cells": truth.get("selected_cells", []),
                "unique_solution_status": truth.get(
                    "unique_solution_status", puzzle.unique_solution_status
                ),
            }
        if tool_name == "kakurasu_rules_reference":
            return {
                "rules": [
                    "A selected cell contributes its 1-based column weight to its row target.",
                    "A selected cell contributes its 1-based row weight to its column target.",
                    "Every row and column weighted sum must equal its target exactly.",
                ]
            }
        if tool_name == "kakurasu_puzzle_summary":
            return {
                "height": height,
                "width": width,
                "cell_count": height * width,
                "row_targets": row_targets,
                "column_targets": column_targets,
                "row_weights": list(range(1, height + 1)),
                "column_weights": list(range(1, width + 1)),
                "difficulty": puzzle.difficulty,
            }
        if tool_name == "kakurasu_row_pattern_analysis":
            return {
                "rows": self._line_analysis(
                    row_patterns,
                    row_targets,
                    width,
                    "row",
                )
            }
        if tool_name == "kakurasu_column_pattern_analysis":
            return {
                "columns": self._line_analysis(
                    column_patterns,
                    column_targets,
                    height,
                    "column",
                )
            }
        if tool_name == "kakurasu_weight_contribution_analysis":
            contributions = truth.get("weighted_contributions")
            if contributions is None:
                contributions = self._weighted_contributions(
                    height,
                    width,
                    truth.get("selected_cells", []),
                    truth.get("solution_mask"),
                )
            return {
                "contributions": contributions,
                "row_weights": list(range(1, height + 1)),
                "column_weights": list(range(1, width + 1)),
            }
        if tool_name == "kakurasu_move_impact_analysis":
            impact = self._move_impact(
                suggested_move,
                row_targets,
                column_targets,
                row_patterns,
                column_patterns,
            )
            return {
                "suggested_move": suggested_move,
                "target_updates": impact["target_updates"],
                "eliminated_patterns": impact["eliminated_patterns"],
                "eliminations": impact["eliminated_patterns"],
            }
        if tool_name == "kakurasu_solution_space_analysis":
            return self._solution_space(
                height,
                width,
                row_targets,
                column_targets,
                truth,
            )
        return {}

    def _puzzle_parts(
        self,
        puzzle: PuzzleRecord,
    ) -> tuple[int, int, list[int], list[int]]:
        try:
            height, width, row_targets, column_targets = parse_puzzle(puzzle.puzzle)
            return int(height), int(width), list(row_targets), list(column_targets)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            metadata = puzzle.metadata
            return (
                int(metadata.get("height") or 0),
                int(metadata.get("width") or 0),
                list(metadata.get("row_targets") or []),
                list(metadata.get("column_targets") or []),
            )

    def _pattern_maps(
        self,
        puzzle: PuzzleRecord,
    ) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
        height, width, row_targets, column_targets = self._puzzle_parts(puzzle)
        truth = puzzle.ground_truth
        row_patterns = self._coerce_pattern_map(
            truth.get("row_patterns"),
            "row",
            height,
        )
        column_patterns = self._coerce_pattern_map(
            truth.get("column_patterns"),
            "column",
            width,
        )
        if len(row_patterns) != height:
            row_patterns = {
                f"row_{index + 1}": self._patterns_for_target(width, target)
                for index, target in enumerate(row_targets)
            }
        if len(column_patterns) != width:
            column_patterns = {
                f"column_{index + 1}": self._patterns_for_target(height, target)
                for index, target in enumerate(column_targets)
            }
        return row_patterns, column_patterns

    @staticmethod
    def _coerce_pattern_map(
        raw: Any,
        prefix: str,
        count: int,
    ) -> dict[str, list[Any]]:
        if isinstance(raw, dict):
            result: dict[str, list[Any]] = {}
            for index, (key, value) in enumerate(raw.items()):
                if not isinstance(value, list):
                    continue
                label = str(key)
                if not label.startswith(f"{prefix}_"):
                    try:
                        label = f"{prefix}_{int(label)}"
                    except ValueError:
                        label = f"{prefix}_{index + 1}"
                result[label] = value
            return result
        if isinstance(raw, list) and len(raw) == count:
            if all(isinstance(value, list) for value in raw):
                return {f"{prefix}_{index + 1}": value for index, value in enumerate(raw)}
        return {}

    @staticmethod
    def _patterns_for_target(length: int, target: int) -> list[str]:
        return [
            "".join("#" if mask & (1 << index) else "." for index in range(length))
            for mask in range(1 << length)
            if sum(index + 1 for index in range(length) if mask & (1 << index)) == target
        ]

    @staticmethod
    def _pattern_counts(
        patterns: dict[str, list[Any]],
        recorded: Any,
    ) -> dict[str, int]:
        if isinstance(recorded, dict):
            result = {}
            for key, value in recorded.items():
                try:
                    result[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            if result:
                return result
        if isinstance(recorded, list) and len(recorded) == len(patterns):
            try:
                return {
                    key: int(value)
                    for key, value in zip(sorted(patterns), recorded)
                }
            except (TypeError, ValueError):
                pass
        return {key: len(value) for key, value in sorted(patterns.items())}

    @classmethod
    def _compact_pattern_map(
        cls,
        raw: dict[str, Any] | list[Any],
        prefix: str,
    ) -> list[dict[str, Any]]:
        count = len(raw)
        patterns = cls._coerce_pattern_map(raw, prefix, count)
        return [
            {"line": line, "count": len(values), "examples": values[:6]}
            for line, values in sorted(patterns.items(), key=cls._line_sort_key)
        ]

    @classmethod
    def _line_analysis(
        cls,
        patterns: dict[str, list[Any]],
        targets: list[int],
        line_length: int,
        prefix: str,
    ) -> list[dict[str, Any]]:
        result = []
        for index, target in enumerate(targets):
            line = f"{prefix}_{index + 1}"
            values = patterns.get(line, [])
            masks = [cls._pattern_bits(value, line_length) for value in values]
            valid_masks = [mask for mask in masks if mask is not None]
            forced_selected = [
                offset + 1
                for offset in range(line_length)
                if valid_masks and all(mask[offset] for mask in valid_masks)
            ]
            forced_unselected = [
                offset + 1
                for offset in range(line_length)
                if valid_masks and all(not mask[offset] for mask in valid_masks)
            ]
            result.append(
                {
                    "line": line,
                    "target": target,
                    "weights": list(range(1, line_length + 1)),
                    "pattern_count": len(values),
                    "pattern_examples": values[:8],
                    "forced_selected_positions": forced_selected,
                    "forced_unselected_positions": forced_unselected,
                    "feasible": bool(values),
                }
            )
        return result

    @classmethod
    def _forced_cells(
        cls,
        row_patterns: dict[str, list[Any]],
        column_patterns: dict[str, list[Any]],
        height: int,
        width: int,
        selected: bool,
    ) -> list[str]:
        forced: set[str] = set()
        for row in range(height):
            patterns = [cls._pattern_bits(value, width) for value in row_patterns.get(f"row_{row + 1}", [])]
            masks = [mask for mask in patterns if mask is not None]
            for column in range(width):
                if masks and all(mask[column] is selected for mask in masks):
                    forced.add(f"r{row + 1}c{column + 1}")
        for column in range(width):
            patterns = [cls._pattern_bits(value, height) for value in column_patterns.get(f"column_{column + 1}", [])]
            masks = [mask for mask in patterns if mask is not None]
            for row in range(height):
                if masks and all(mask[row] is selected for mask in masks):
                    forced.add(f"r{row + 1}c{column + 1}")
        return sorted(forced)

    @classmethod
    def _weighted_contributions(
        cls,
        height: int,
        width: int,
        selected_cells: Any,
        solution_mask: Any,
    ) -> list[dict[str, Any]]:
        selected = {str(cell) for cell in selected_cells} if isinstance(selected_cells, list) else set()
        if not selected and isinstance(solution_mask, list):
            for row in range(min(height, len(solution_mask))):
                values = solution_mask[row]
                if not isinstance(values, list):
                    continue
                for column in range(min(width, len(values))):
                    if bool(values[column]):
                        selected.add(f"r{row + 1}c{column + 1}")
        result = []
        for cell in sorted(selected, key=cls._cell_sort_key):
            position = cls._parse_cell(cell)
            if position is None:
                continue
            row, column = position
            result.append(
                {
                    "cell": cell,
                    "row": row,
                    "row_contribution": column,
                    "column": column,
                    "column_contribution": row,
                }
            )
        return result

    @classmethod
    def _move_impact(
        cls,
        move: Any,
        row_targets: list[int],
        column_targets: list[int],
        row_patterns: dict[str, list[Any]],
        column_patterns: dict[str, list[Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(move, dict):
            return {"target_updates": [], "eliminated_patterns": []}
        cell = move.get("cell")
        position = cls._parse_cell(cell)
        if position is None:
            return {"target_updates": [], "eliminated_patterns": []}
        row, column = position
        selected = cls._selected_state(move)
        if selected is None:
            return {"target_updates": [], "eliminated_patterns": []}

        row_target = row_targets[row - 1] if row <= len(row_targets) else None
        column_target = column_targets[column - 1] if column <= len(column_targets) else None
        target_updates = [
            {
                "line": f"row_{row}",
                "target": row_target,
                "cell_weight": column,
                "selected": selected,
                "remaining_after_move": (
                    row_target - column if selected and row_target is not None else row_target
                ),
            },
            {
                "line": f"column_{column}",
                "target": column_target,
                "cell_weight": row,
                "selected": selected,
                "remaining_after_move": (
                    column_target - row if selected and column_target is not None else column_target
                ),
            },
        ]
        eliminated = []
        for line, patterns, offset, length in (
            (f"row_{row}", row_patterns.get(f"row_{row}", []), column - 1, len(column_targets)),
            (
                f"column_{column}",
                column_patterns.get(f"column_{column}", []),
                row - 1,
                len(row_targets),
            ),
        ):
            for pattern in patterns:
                bits = cls._pattern_bits(pattern, length)
                if bits is not None and offset < len(bits) and bits[offset] is not selected:
                    eliminated.append(
                        {
                            "line": line,
                            "pattern": pattern,
                            "reason": f"{cell}_must_be_{'selected' if selected else 'unselected'}",
                        }
                    )
        return {"target_updates": target_updates, "eliminated_patterns": eliminated}

    def _solution_space(
        self,
        height: int,
        width: int,
        row_targets: list[int],
        column_targets: list[int],
        truth: dict[str, Any],
    ) -> dict[str, Any]:
        solutions: list[list[list[int]]] = []
        if height > 0 and width > 0:
            try:
                if not validate_structure(height, width, row_targets, column_targets):
                    solutions = solve_kakurasu(
                        height,
                        width,
                        row_targets,
                        column_targets,
                        limit=2,
                    )
            except (TypeError, ValueError, KeyError):
                solutions = []
        differences = [
            f"r{row + 1}c{column + 1}"
            for row in range(height)
            for column in range(width)
            if len(solutions) > 1
            and solutions[0][row][column] != solutions[1][row][column]
        ]
        status = truth.get("solvability_status")
        if status is None:
            status = "unsolvable" if not solutions else "unique" if len(solutions) == 1 else "ambiguous"
        return {
            "solution_count_capped": len(solutions),
            "cap": 2,
            "status": status,
            "witness_differences": differences,
        }

    @staticmethod
    def _pattern_bits(pattern: Any, length: int) -> list[bool] | None:
        if isinstance(pattern, str):
            if len(pattern) != length:
                return None
            if set(pattern) <= {"#", "."}:
                return [value == "#" for value in pattern]
            if set(pattern) <= {"0", "1"}:
                return [value == "1" for value in pattern]
        if isinstance(pattern, (list, tuple)) and len(pattern) == length:
            return [bool(value) for value in pattern]
        if isinstance(pattern, dict):
            return KakurasuDomainAdapter._pattern_bits(
                pattern.get("mask", pattern.get("pattern")),
                length,
            )
        return None

    @staticmethod
    def _selected_state(move: dict[str, Any]) -> bool | None:
        if isinstance(move.get("selected"), bool):
            return move["selected"]
        state = move.get("state", move.get("action"))
        if state in {"selected", "select", "keep", "#", 1, True}:
            return True
        if state in {"unselected", "deselect", "remove", ".", 0, False}:
            return False
        return None

    @staticmethod
    def _parse_cell(value: Any) -> tuple[int, int] | None:
        if not isinstance(value, str) or not value.startswith("r") or "c" not in value:
            return None
        try:
            row, column = value[1:].split("c", 1)
            return int(row), int(column)
        except ValueError:
            return None

    @staticmethod
    def _line_sort_key(item: tuple[str, Any]) -> tuple[str, int]:
        label = item[0]
        try:
            prefix, number = label.rsplit("_", 1)
            return prefix, int(number)
        except ValueError:
            return label, 0

    @classmethod
    def _cell_sort_key(cls, cell: str) -> tuple[int, int]:
        return cls._parse_cell(cell) or (0, 0)

    @staticmethod
    def _suggested_move(truth: dict[str, Any]) -> Any:
        return truth.get("suggested_move", truth.get("suggested_selection"))

    @staticmethod
    def _limited(value: Any, limit: int) -> Any:
        if isinstance(value, list):
            return value[:limit]
        if isinstance(value, dict):
            return dict(list(value.items())[:limit])
        return value

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "kakurasu_subset_analysis": (
                "The scenario needs weighted subset patterns or a forced selection."
            ),
            "kakurasu_constraint_validation": (
                "The scenario needs deterministic target and weighted-sum checks."
            ),
            "kakurasu_solution_verification": (
                "The scenario needs solver-backed selected-mask verification."
            ),
        }
        return reasons.get(
            tool_name,
            f"The {scenario.task_category} scenario requested Kakurasu tool support.",
        )
