from __future__ import annotations

import json
from typing import Any

from ..domain_support import (
    VariedDomainSupport,
    build_tool_usage,
    compact_tool_context,
    make_tool_catalog,
)
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator
from ..sumplete import (
    SumpletePuzzleManager,
    parse_puzzle,
    solve_sumplete,
    validate_structure,
)


class SumpleteScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {
            "next_best_move": "ask_for_next_keep_or_remove_deduction",
            "validity_check": "verify_sumplete_decision_or_target",
            "rules_explanation": "learn_sumplete_rules",
            "solve_puzzle": "request_sumplete_solution_guidance",
            "subset_analysis": "analyze_sumplete_subsets",
            "hint": "ask_for_sumplete_hint",
            "mistake_correction": "debug_subset_sum_mistake",
            "technique_discussion": "discuss_sumplete_strategy",
            "beginner_question": "learn_sumplete_basics",
            "advanced_question": "analyze_cross_line_subset_constraints",
            "general_chat": "general_sumplete_help",
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
            "Use the supplied Sumplete grid and row/column targets exactly unless malformed input is explicitly required.",
            "Ground every claim in subsets of the actual cell values; # means kept and . means removed.",
        ]
        if task_category == "hint":
            constraints.append(
                "Prefer one forced keep or removal over revealing the complete keep mask."
            )
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Sumplete assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed number grid before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class SumpleteValidator:
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
            height, width, grid, row_targets, column_targets = parse_puzzle(puzzle.puzzle)
            structural = validate_structure(
                height,
                width,
                grid,
                row_targets,
                column_targets,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parse_error = exc

        truth = puzzle.ground_truth
        if parse_error is not None and scenario.edge_case != "malformed_input":
            errors.append("selected puzzle cannot be parsed as canonical Sumplete JSON")
        if scenario.edge_case == "none":
            if structural:
                errors.append("standard scenario has structurally invalid Sumplete targets")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique Sumplete puzzle")
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


class SumpleteDomainAdapter(VariedDomainSupport):
    name = "sumplete"
    tool_catalog = make_tool_catalog(
        name,
        {
            "sumplete_subset_analysis": (
                "Legal row/column value subsets, forced states, and a next deduction."
            ),
            "sumplete_constraint_validation": (
                "Grid, targets, kept sums, structure, and solver validation."
            ),
            "sumplete_solution_verification": (
                "Complete solver-backed kept-cell mask verification."
            ),
            "sumplete_rules_reference": "Canonical Sumplete keep/remove and target-sum rules.",
            "sumplete_puzzle_summary": "Dimensions, number grid, targets, and difficulty.",
            "sumplete_row_subset_analysis": (
                "Value-subset counts and examples for every row target."
            ),
            "sumplete_column_subset_analysis": (
                "Value-subset counts and examples for every column target."
            ),
            "sumplete_target_balance_analysis": (
                "Kept and removed totals required by every row and column."
            ),
            "sumplete_move_impact_analysis": (
                "Target residuals and subset eliminations caused by a keep/remove decision."
            ),
            "sumplete_solution_space_analysis": (
                "Capped solution count and ambiguous keep-mask differences."
            ),
        },
    )

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = SumpleteScenarioGenerator(config)
        self.puzzle_manager = SumpletePuzzleManager(config)
        self.validator = SumpleteValidator()
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
        height, width, grid, row_targets, column_targets = self._puzzle_parts(puzzle)
        return {
            "puzzle_id": puzzle.puzzle_id,
            "height": height,
            "width": width,
            "grid": grid,
            "row_targets": row_targets,
            "column_targets": column_targets,
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "transformation": puzzle.transformation,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        row_subsets, column_subsets = self._subset_maps(puzzle)
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
            "row_subset_counts": self._subset_counts(
                row_subsets,
                truth.get("row_subset_counts"),
            ),
            "column_subset_counts": self._subset_counts(
                column_subsets,
                truth.get("column_subset_counts"),
            ),
            "forced_kept": truth.get("forced_kept", [])[:20],
            "forced_removed": truth.get("forced_removed", [])[:20],
            "kept_contributions": truth.get("kept_contributions", [])[:20],
            "target_balance": self._limited(truth.get("target_balance", []), 20),
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
            row_subsets = output.pop("row_subsets", None)
            if isinstance(row_subsets, (dict, list)):
                output["row_subset_summary"] = self._compact_subset_map(
                    row_subsets,
                    "r",
                )
            column_subsets = output.pop("column_subsets", None)
            if isinstance(column_subsets, (dict, list)):
                output["column_subset_summary"] = self._compact_subset_map(
                    column_subsets,
                    "c",
                )
            for key in (
                "forced_kept",
                "forced_removed",
                "balances",
                "eliminated_subsets",
                "eliminations",
            ):
                if key in output:
                    output[key] = self._limited(output[key], 20)
            output.pop("solved_board", None)
            call["output"] = output
        return context

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Sumplete number grid and targets exactly. A # cell is kept and contributes "
            "its printed value; a . cell is removed and contributes zero. The kept values in every row "
            "and column must sum to the corresponding target."
        )

    def flatten_sample_row(
        self,
        row: dict[str, Any],
        sample: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {})
        row.update(
            {
                "domain": sample.get("domain", self.name),
                "grid_height": metadata.get("height"),
                "grid_width": metadata.get("width"),
                "grid": metadata.get("grid", []),
                "row_targets": metadata.get("row_targets", []),
                "column_targets": metadata.get("column_targets", []),
                "tool_used": sample.get("tool_used", False),
                "tool_name": sample.get("tool_usage_details", {}).get("tool_name"),
            }
        )
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input":
            return [
                "sumplete_constraint_validation",
                "sumplete_puzzle_summary",
                "sumplete_rules_reference",
            ]
        if scenario.edge_case in {
            "invalid_targets",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return [
                "sumplete_constraint_validation",
                "sumplete_row_subset_analysis",
                "sumplete_solution_space_analysis",
            ]
        if scenario.task_category in {
            "rules_explanation",
            "beginner_question",
            "general_chat",
        }:
            return [
                "sumplete_rules_reference",
                "sumplete_puzzle_summary",
                "sumplete_target_balance_analysis",
            ]
        if scenario.task_category in {"technique_discussion", "advanced_question"}:
            return [
                "sumplete_row_subset_analysis",
                "sumplete_column_subset_analysis",
                "sumplete_target_balance_analysis",
                "sumplete_move_impact_analysis",
            ]

        primary = self._tool_name_for_scenario(scenario)
        if primary == "sumplete_solution_verification":
            return [
                "sumplete_subset_analysis",
                "sumplete_target_balance_analysis",
                "sumplete_solution_space_analysis",
                primary,
            ]
        if primary == "sumplete_subset_analysis":
            return [
                primary,
                "sumplete_row_subset_analysis",
                "sumplete_column_subset_analysis",
                "sumplete_move_impact_analysis",
            ]
        if primary == "sumplete_constraint_validation":
            return [
                primary,
                "sumplete_target_balance_analysis",
                "sumplete_solution_space_analysis",
            ]
        return [primary or "sumplete_puzzle_summary", "sumplete_constraint_validation"]

    @staticmethod
    def _tool_name_for_scenario(scenario: Scenario) -> str | None:
        if scenario.edge_case in {
            "invalid_targets",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return "sumplete_constraint_validation"
        if scenario.tool_usage == "subset_sum_scan" or scenario.task_category in {
            "hint",
            "next_best_move",
            "subset_analysis",
        }:
            return "sumplete_subset_analysis"
        if scenario.tool_usage == "constraint_check" or scenario.task_category in {
            "validity_check",
            "mistake_correction",
        }:
            return "sumplete_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "sumplete_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        height, width, grid, row_targets, column_targets = self._puzzle_parts(puzzle)
        row_subsets, column_subsets = self._subset_maps(puzzle)
        suggested_move = self._suggested_move(truth)

        if tool_name == "sumplete_subset_analysis":
            return {
                "row_subsets": row_subsets,
                "column_subsets": column_subsets,
                "forced_kept": truth.get(
                    "forced_kept",
                    self._forced_cells(row_subsets, column_subsets, height, width, True),
                ),
                "forced_removed": truth.get(
                    "forced_removed",
                    self._forced_cells(row_subsets, column_subsets, height, width, False),
                ),
                "suggested_move": suggested_move,
            }
        if tool_name == "sumplete_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "solution_violations": truth.get("solution_violations", []),
                "row_evaluations": truth.get("row_evaluations", []),
                "column_evaluations": truth.get("column_evaluations", []),
            }
        if tool_name == "sumplete_solution_verification":
            return {
                "solution": truth.get("solution", puzzle.solution),
                "solution_mask": truth.get("solution_mask", []),
                "kept_cells": truth.get("kept_cells", []),
                "removed_cells": truth.get("removed_cells", []),
                "unique_solution_status": truth.get(
                    "unique_solution_status", puzzle.unique_solution_status
                ),
            }
        if tool_name == "sumplete_rules_reference":
            return {
                "rules": [
                    "Keep or remove each printed number; # denotes kept and . denotes removed.",
                    "Kept values in every row sum exactly to its row target.",
                    "Kept values in every column sum exactly to its column target.",
                ]
            }
        if tool_name == "sumplete_puzzle_summary":
            return {
                "height": height,
                "width": width,
                "cell_count": height * width,
                "grid_total": sum(sum(row) for row in grid),
                "row_targets": row_targets,
                "column_targets": column_targets,
                "difficulty": puzzle.difficulty,
            }
        if tool_name == "sumplete_row_subset_analysis":
            return {
                "rows": self._line_analysis(
                    row_subsets,
                    grid,
                    row_targets,
                    "r",
                )
            }
        if tool_name == "sumplete_column_subset_analysis":
            columns = [
                [grid[row][column] for row in range(height)]
                for column in range(width)
            ]
            return {
                "columns": self._line_analysis(
                    column_subsets,
                    columns,
                    column_targets,
                    "c",
                )
            }
        # Keep the unregistered legacy executor spelling as a read-only alias;
        # the public catalog and all routes use the approved target_balance name.
        if tool_name == "sumplete_target_balance_analysis":
            return {
                "balances": self._balances(
                    grid,
                    row_targets,
                    column_targets,
                    truth.get("solution_mask"),
                ),
                "recorded_target_balance": truth.get("target_balance"),
                "kept_contributions": truth.get("kept_contributions", []),
            }
        if tool_name == "sumplete_move_impact_analysis":
            impact = self._move_impact(
                suggested_move,
                grid,
                row_targets,
                column_targets,
                row_subsets,
                column_subsets,
            )
            return {
                "suggested_move": suggested_move,
                "target_updates": impact["target_updates"],
                "eliminated_subsets": impact["eliminated_subsets"],
                "eliminations": impact["eliminated_subsets"],
            }
        if tool_name == "sumplete_solution_space_analysis":
            return self._solution_space(
                height,
                width,
                grid,
                row_targets,
                column_targets,
                truth,
            )
        return {}

    def _puzzle_parts(
        self,
        puzzle: PuzzleRecord,
    ) -> tuple[int, int, list[list[int]], list[int], list[int]]:
        try:
            height, width, grid, row_targets, column_targets = parse_puzzle(puzzle.puzzle)
            return (
                int(height),
                int(width),
                [list(row) for row in grid],
                list(row_targets),
                list(column_targets),
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            metadata = puzzle.metadata
            return (
                int(metadata.get("height") or 0),
                int(metadata.get("width") or 0),
                [list(row) for row in metadata.get("grid") or []],
                list(metadata.get("row_targets") or []),
                list(metadata.get("column_targets") or []),
            )

    def _subset_maps(
        self,
        puzzle: PuzzleRecord,
    ) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
        height, width, grid, row_targets, column_targets = self._puzzle_parts(puzzle)
        truth = puzzle.ground_truth
        row_subsets = self._coerce_subset_map(truth.get("row_subsets"), "r", height)
        column_subsets = self._coerce_subset_map(
            truth.get("column_subsets"),
            "c",
            width,
        )
        if len(row_subsets) != height:
            row_subsets = {
                f"r{index + 1}": self._subsets_for_values(values, row_targets[index])
                for index, values in enumerate(grid)
                if index < len(row_targets)
            }
        if len(column_subsets) != width:
            column_subsets = {
                f"c{column + 1}": self._subsets_for_values(
                    [grid[row][column] for row in range(height)],
                    column_targets[column],
                )
                for column in range(width)
                if column < len(column_targets)
            }
        return row_subsets, column_subsets

    @staticmethod
    def _coerce_subset_map(raw: Any, prefix: str, count: int) -> dict[str, list[Any]]:
        if isinstance(raw, dict):
            result: dict[str, list[Any]] = {}
            for index, (key, value) in enumerate(raw.items()):
                if not isinstance(value, list):
                    continue
                label = str(key)
                if not label.startswith(prefix):
                    try:
                        label = f"{prefix}{int(label)}"
                    except ValueError:
                        label = f"{prefix}{index + 1}"
                result[label] = value
            return result
        if isinstance(raw, list) and len(raw) == count and all(
            isinstance(value, list) for value in raw
        ):
            return {f"{prefix}{index + 1}": value for index, value in enumerate(raw)}
        return {}

    @staticmethod
    def _subsets_for_values(values: list[int], target: int) -> list[str]:
        length = len(values)
        return [
            "".join("#" if mask & (1 << index) else "." for index in range(length))
            for mask in range(1 << length)
            if sum(values[index] for index in range(length) if mask & (1 << index)) == target
        ]

    @staticmethod
    def _subset_counts(
        subsets: dict[str, list[Any]],
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
        if isinstance(recorded, list) and len(recorded) == len(subsets):
            try:
                return {
                    key: int(value)
                    for key, value in zip(sorted(subsets), recorded)
                }
            except (TypeError, ValueError):
                pass
        return {key: len(value) for key, value in sorted(subsets.items())}

    @classmethod
    def _compact_subset_map(
        cls,
        raw: dict[str, Any] | list[Any],
        prefix: str,
    ) -> list[dict[str, Any]]:
        subsets = cls._coerce_subset_map(raw, prefix, len(raw))
        return [
            {"line": line, "count": len(values), "examples": values[:6]}
            for line, values in sorted(subsets.items(), key=cls._line_sort_key)
        ]

    @classmethod
    def _line_analysis(
        cls,
        subsets: dict[str, list[Any]],
        line_values: list[list[int]],
        targets: list[int],
        prefix: str,
    ) -> list[dict[str, Any]]:
        result = []
        for index, values in enumerate(line_values):
            line = f"{prefix}{index + 1}"
            patterns = subsets.get(line, [])
            masks = [cls._pattern_bits(pattern, len(values)) for pattern in patterns]
            valid_masks = [mask for mask in masks if mask is not None]
            result.append(
                {
                    "line": line,
                    "values": values,
                    "line_total": sum(values),
                    "target": targets[index] if index < len(targets) else None,
                    "subset_count": len(patterns),
                    "subset_examples": patterns[:8],
                    "forced_kept_positions": [
                        offset + 1
                        for offset in range(len(values))
                        if valid_masks and all(mask[offset] for mask in valid_masks)
                    ],
                    "forced_removed_positions": [
                        offset + 1
                        for offset in range(len(values))
                        if valid_masks and all(not mask[offset] for mask in valid_masks)
                    ],
                    "feasible": bool(patterns),
                }
            )
        return result

    @classmethod
    def _forced_cells(
        cls,
        row_subsets: dict[str, list[Any]],
        column_subsets: dict[str, list[Any]],
        height: int,
        width: int,
        kept: bool,
    ) -> list[str]:
        forced: set[str] = set()
        for row in range(height):
            patterns = [
                cls._pattern_bits(pattern, width)
                for pattern in row_subsets.get(f"r{row + 1}", [])
            ]
            masks = [mask for mask in patterns if mask is not None]
            for column in range(width):
                if masks and all(mask[column] is kept for mask in masks):
                    forced.add(f"r{row + 1}c{column + 1}")
        for column in range(width):
            patterns = [
                cls._pattern_bits(pattern, height)
                for pattern in column_subsets.get(f"c{column + 1}", [])
            ]
            masks = [mask for mask in patterns if mask is not None]
            for row in range(height):
                if masks and all(mask[row] is kept for mask in masks):
                    forced.add(f"r{row + 1}c{column + 1}")
        return sorted(forced)

    @staticmethod
    def _balances(
        grid: list[list[int]],
        row_targets: list[int],
        column_targets: list[int],
        solution_mask: Any,
    ) -> list[dict[str, Any]]:
        height = len(grid)
        width = len(grid[0]) if grid else 0
        mask = solution_mask if isinstance(solution_mask, list) else []
        result = []
        for row in range(height):
            total = sum(grid[row])
            kept_sum = None
            if row < len(mask) and isinstance(mask[row], list):
                kept_sum = sum(
                    grid[row][column]
                    for column in range(min(width, len(mask[row])))
                    if mask[row][column]
                )
            target = row_targets[row] if row < len(row_targets) else None
            result.append(
                {
                    "line": f"r{row + 1}",
                    "line_total": total,
                    "kept_target": target,
                    "removed_target": total - target if target is not None else None,
                    "verified_kept_sum": kept_sum,
                    "balanced": kept_sum == target if kept_sum is not None else None,
                }
            )
        for column in range(width):
            total = sum(grid[row][column] for row in range(height))
            kept_sum = None
            if len(mask) >= height and all(isinstance(row, list) for row in mask[:height]):
                kept_sum = sum(
                    grid[row][column]
                    for row in range(height)
                    if column < len(mask[row]) and mask[row][column]
                )
            target = column_targets[column] if column < len(column_targets) else None
            result.append(
                {
                    "line": f"c{column + 1}",
                    "line_total": total,
                    "kept_target": target,
                    "removed_target": total - target if target is not None else None,
                    "verified_kept_sum": kept_sum,
                    "balanced": kept_sum == target if kept_sum is not None else None,
                }
            )
        return result

    @classmethod
    def _move_impact(
        cls,
        move: Any,
        grid: list[list[int]],
        row_targets: list[int],
        column_targets: list[int],
        row_subsets: dict[str, list[Any]],
        column_subsets: dict[str, list[Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(move, dict):
            return {"target_updates": [], "eliminated_subsets": []}
        cell = move.get("cell")
        position = cls._parse_cell(cell)
        kept = cls._kept_state(move)
        if position is None or kept is None:
            return {"target_updates": [], "eliminated_subsets": []}
        row, column = position
        if not (1 <= row <= len(grid)) or not (1 <= column <= len(grid[row - 1])):
            return {"target_updates": [], "eliminated_subsets": []}
        value = grid[row - 1][column - 1]
        row_target = row_targets[row - 1] if row <= len(row_targets) else None
        column_target = column_targets[column - 1] if column <= len(column_targets) else None
        target_updates = [
            {
                "line": f"r{row}",
                "target": row_target,
                "cell_value": value,
                "state": "kept" if kept else "removed",
                "remaining_after_move": (
                    row_target - value if kept and row_target is not None else row_target
                ),
            },
            {
                "line": f"c{column}",
                "target": column_target,
                "cell_value": value,
                "state": "kept" if kept else "removed",
                "remaining_after_move": (
                    column_target - value
                    if kept and column_target is not None
                    else column_target
                ),
            },
        ]
        eliminated = []
        for line, subsets, offset, length in (
            (f"r{row}", row_subsets.get(f"r{row}", []), column - 1, len(column_targets)),
            (
                f"c{column}",
                column_subsets.get(f"c{column}", []),
                row - 1,
                len(row_targets),
            ),
        ):
            for subset in subsets:
                bits = cls._pattern_bits(subset, length)
                if bits is not None and offset < len(bits) and bits[offset] is not kept:
                    eliminated.append(
                        {
                            "line": line,
                            "subset": subset,
                            "reason": f"{cell}_must_be_{'kept' if kept else 'removed'}",
                        }
                    )
        return {"target_updates": target_updates, "eliminated_subsets": eliminated}

    def _solution_space(
        self,
        height: int,
        width: int,
        grid: list[list[int]],
        row_targets: list[int],
        column_targets: list[int],
        truth: dict[str, Any],
    ) -> dict[str, Any]:
        solutions: list[list[list[int]]] = []
        if height > 0 and width > 0:
            try:
                if not validate_structure(
                    height,
                    width,
                    grid,
                    row_targets,
                    column_targets,
                ):
                    solutions = solve_sumplete(
                        height,
                        width,
                        grid,
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
            return SumpleteDomainAdapter._pattern_bits(
                pattern.get("mask", pattern.get("pattern")),
                length,
            )
        return None

    @staticmethod
    def _kept_state(move: dict[str, Any]) -> bool | None:
        if isinstance(move.get("kept"), bool):
            return move["kept"]
        state = move.get("state", move.get("action"))
        if state in {"kept", "keep", "selected", "#", 1, True}:
            return True
        if state in {"removed", "remove", "unselected", ".", 0, False}:
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
        prefix = label[:1]
        try:
            return prefix, int(label[1:])
        except ValueError:
            return label, 0

    @staticmethod
    def _suggested_move(truth: dict[str, Any]) -> Any:
        return truth.get("suggested_move", truth.get("suggested_state"))

    @staticmethod
    def _limited(value: Any, limit: int) -> Any:
        if isinstance(value, list):
            return value[:limit]
        if isinstance(value, dict):
            return dict(list(value.items())[:limit])
        return value

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "sumplete_subset_analysis": (
                "The scenario needs exact value subsets or a forced keep/remove decision."
            ),
            "sumplete_constraint_validation": (
                "The scenario needs deterministic grid, target, and kept-sum checks."
            ),
            "sumplete_solution_verification": (
                "The scenario needs solver-backed keep-mask verification."
            ),
        }
        return reasons.get(
            tool_name,
            f"The {scenario.task_category} scenario requested Sumplete tool support.",
        )
