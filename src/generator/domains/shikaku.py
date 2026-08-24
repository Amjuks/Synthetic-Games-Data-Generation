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
from ..shikaku import ShikakuPuzzleManager, parse_puzzle, solve_shikaku, validate_structure


class ShikakuScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {
            "next_best_move": "ask_for_next_rectangle_deduction",
            "validity_check": "verify_shikaku_rectangle",
            "rules_explanation": "learn_shikaku_rules",
            "solve_puzzle": "request_shikaku_solution_guidance",
            "rectangle_analysis": "analyze_rectangle_candidates",
            "hint": "ask_for_shikaku_hint",
            "mistake_correction": "debug_rectangle_partition_mistake",
            "technique_discussion": "discuss_shikaku_strategy",
            "beginner_question": "learn_shikaku_basics",
            "advanced_question": "analyze_exact_cover_interactions",
            "general_chat": "general_shikaku_help",
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
            "Use the supplied Shikaku dimensions and clues exactly unless malformed input is explicitly required.",
            "Ground every claim in rectangular area, one-clue-per-rectangle, and exact-cover constraints.",
        ]
        if task_category == "hint":
            constraints.append(
                "Prefer one forced rectangle or cell-ownership deduction over revealing the full partition."
            )
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Shikaku assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed clue grid before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class ShikakuValidator:
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
            height, width, clues = parse_puzzle(puzzle.puzzle)
            structural = validate_structure(height, width, clues)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parse_error = exc

        truth = puzzle.ground_truth
        if parse_error is not None and scenario.edge_case != "malformed_input":
            errors.append("selected puzzle cannot be parsed as canonical Shikaku JSON")
        if scenario.edge_case == "none":
            if structural:
                errors.append("standard scenario has structurally invalid Shikaku clues")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique Shikaku puzzle")
        if truth.get("solution", puzzle.solution) != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_clues" and not structural:
            errors.append("invalid_clues scenario has no structural violation")
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


class ShikakuDomainAdapter(VariedDomainSupport):
    name = "shikaku"
    tool_catalog = make_tool_catalog(
        name,
        {
            "shikaku_rectangle_candidate_scan": (
                "Legal clue rectangles, forced ownership, and a solver-backed deduction."
            ),
            "shikaku_constraint_validation": (
                "Dimensions, clues, areas, overlap, coverage, and solver validation."
            ),
            "shikaku_solution_verification": (
                "Complete solver-backed rectangle-partition verification."
            ),
            "shikaku_rules_reference": "Canonical Shikaku rectangle-partition rules.",
            "shikaku_puzzle_summary": "Grid dimensions, clue count, area total, and difficulty.",
            "shikaku_clue_area_analysis": (
                "Per-clue area shapes and legal rectangle counts."
            ),
            "shikaku_exact_cover_analysis": (
                "Most-constrained clue and exact-cover candidate statistics."
            ),
            "shikaku_cell_ownership_analysis": (
                "Cells that are forced to belong to a particular clue rectangle."
            ),
            "shikaku_move_impact_analysis": (
                "Rectangle candidates eliminated by the suggested placement."
            ),
            "shikaku_solution_space_analysis": (
                "Capped solution count and ambiguous ownership differences."
            ),
        },
    )

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = ShikakuScenarioGenerator(config)
        self.puzzle_manager = ShikakuPuzzleManager(config)
        self.validator = ShikakuValidator()
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
                "clue_count": len(puzzle.metadata.get("clues", [])) or puzzle.num_clues,
            },
            output_for=lambda name: self._run_tool(name, puzzle),
        )

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        height, width, clues = self._puzzle_parts(puzzle)
        return {
            "puzzle_id": puzzle.puzzle_id,
            "height": height,
            "width": width,
            "clues": clues,
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "transformation": puzzle.transformation,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        candidates = self._candidate_map(puzzle)
        return {
            "solution": truth.get("solution", puzzle.solution),
            "solution_rectangles": truth.get("solution_rectangles", []),
            "validity_status": truth.get("validity_status"),
            "solvability_status": truth.get("solvability_status"),
            "unique_solution_status": truth.get("unique_solution_status"),
            "solution_count": truth.get("solution_count"),
            "structural_violations": truth.get("structural_violations", [])[:12],
            "solution_violations": truth.get("solution_violations", [])[:12],
            "clue_evaluations": truth.get("clue_evaluations", [])[:16],
            "rectangle_evaluations": truth.get("rectangle_evaluations", [])[:16],
            "forced_ownership": self._limited(truth.get("forced_ownership", []), 20),
            "candidate_rectangle_summary": self._candidate_summary(candidates),
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
            if "candidate_rectangles" in output:
                output["candidate_rectangles"] = self._candidate_summary(
                    self._coerce_candidate_map(output["candidate_rectangles"])
                )
            for key in ("solution_rectangles", "region_grid", "solved_board"):
                output.pop(key, None)
            for key in (
                "clue_evaluations",
                "rectangle_evaluations",
                "eliminated_candidates",
                "forced_ownership",
            ):
                if key in output:
                    output[key] = self._limited(output[key], 20)
            call["output"] = output
        return context

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Shikaku dimensions and clues exactly. Partition every grid cell into "
            "non-overlapping axis-aligned rectangles; each rectangle must contain exactly one clue "
            "and its area must equal that clue."
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
                "clues": metadata.get("clues", []),
                "tool_used": sample.get("tool_used", False),
                "tool_name": sample.get("tool_usage_details", {}).get("tool_name"),
            }
        )
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input":
            return [
                "shikaku_constraint_validation",
                "shikaku_puzzle_summary",
                "shikaku_rules_reference",
            ]
        if scenario.edge_case in {
            "invalid_clues",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return [
                "shikaku_constraint_validation",
                "shikaku_clue_area_analysis",
                "shikaku_solution_space_analysis",
            ]
        if scenario.task_category in {
            "rules_explanation",
            "beginner_question",
            "general_chat",
        }:
            return [
                "shikaku_rules_reference",
                "shikaku_puzzle_summary",
                "shikaku_clue_area_analysis",
            ]
        if scenario.task_category in {"technique_discussion", "advanced_question"}:
            return [
                "shikaku_clue_area_analysis",
                "shikaku_exact_cover_analysis",
                "shikaku_cell_ownership_analysis",
                "shikaku_move_impact_analysis",
            ]

        primary = self._tool_name_for_scenario(scenario)
        if primary == "shikaku_solution_verification":
            return [
                "shikaku_rectangle_candidate_scan",
                "shikaku_exact_cover_analysis",
                "shikaku_solution_space_analysis",
                primary,
            ]
        if primary == "shikaku_rectangle_candidate_scan":
            return [
                primary,
                "shikaku_clue_area_analysis",
                "shikaku_cell_ownership_analysis",
                "shikaku_move_impact_analysis",
            ]
        if primary == "shikaku_constraint_validation":
            return [
                primary,
                "shikaku_exact_cover_analysis",
                "shikaku_solution_space_analysis",
            ]
        return [primary or "shikaku_puzzle_summary", "shikaku_constraint_validation"]

    @staticmethod
    def _tool_name_for_scenario(scenario: Scenario) -> str | None:
        if scenario.edge_case in {
            "invalid_clues",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return "shikaku_constraint_validation"
        if scenario.tool_usage == "rectangle_candidate_scan" or scenario.task_category in {
            "hint",
            "next_best_move",
            "rectangle_analysis",
        }:
            return "shikaku_rectangle_candidate_scan"
        if scenario.tool_usage == "constraint_check" or scenario.task_category in {
            "validity_check",
            "mistake_correction",
        }:
            return "shikaku_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "shikaku_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        height, width, clues = self._puzzle_parts(puzzle)
        candidates = self._candidate_map(puzzle)
        suggested_move = self._suggested_move(truth)

        if tool_name == "shikaku_rectangle_candidate_scan":
            return {
                "candidate_rectangles": candidates,
                "forced_ownership": truth.get(
                    "forced_ownership",
                    self._infer_forced_ownership(candidates),
                ),
                "suggested_move": suggested_move,
            }
        if tool_name == "shikaku_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "solution_violations": truth.get("solution_violations", []),
                "clue_evaluations": truth.get("clue_evaluations", []),
                "rectangle_evaluations": truth.get("rectangle_evaluations", []),
            }
        if tool_name == "shikaku_solution_verification":
            return {
                "solution": truth.get("solution", puzzle.solution),
                "solution_rectangles": truth.get("solution_rectangles", []),
                "region_grid": truth.get("region_grid", []),
                "unique_solution_status": truth.get(
                    "unique_solution_status", puzzle.unique_solution_status
                ),
            }
        if tool_name == "shikaku_rules_reference":
            return {
                "rules": [
                    "Partition the entire grid into non-overlapping axis-aligned rectangles.",
                    "Each rectangle contains exactly one clue and has area equal to that clue.",
                ]
            }
        if tool_name == "shikaku_puzzle_summary":
            return {
                "height": height,
                "width": width,
                "cell_count": height * width,
                "clue_count": len(clues),
                "clue_area_total": sum(self._clue_value(clue) for clue in clues),
                "difficulty": puzzle.difficulty,
            }
        if tool_name == "shikaku_clue_area_analysis":
            return {
                "clues": self._clue_area_analysis(
                    height,
                    width,
                    clues,
                    candidates,
                    truth.get("clue_evaluations", []),
                )
            }
        if tool_name == "shikaku_exact_cover_analysis":
            constrained = self._most_constrained_clue(candidates)
            return {
                "most_constrained_clue": constrained,
                "clue_candidate_counts": {
                    clue: len(rectangles) for clue, rectangles in candidates.items()
                },
                "total_candidate_rectangles": sum(len(values) for values in candidates.values()),
                "forced_rectangles": [
                    {"clue": clue, "rectangle": rectangles[0]}
                    for clue, rectangles in candidates.items()
                    if len(rectangles) == 1
                ],
            }
        if tool_name == "shikaku_cell_ownership_analysis":
            forced = truth.get("forced_ownership")
            if forced is None:
                forced = self._infer_forced_ownership(candidates)
            return {
                "forced_ownership": forced,
                "forced_cell_count": len(forced) if hasattr(forced, "__len__") else 0,
            }
        if tool_name == "shikaku_move_impact_analysis":
            return {
                "suggested_move": suggested_move,
                "eliminated_candidates": self._move_impact(candidates, suggested_move),
            }
        if tool_name == "shikaku_solution_space_analysis":
            return self._solution_space(height, width, clues, truth)
        return {}

    def _puzzle_parts(
        self,
        puzzle: PuzzleRecord,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        try:
            height, width, clues = parse_puzzle(puzzle.puzzle)
            return int(height), int(width), list(clues)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            metadata = puzzle.metadata
            return (
                int(metadata.get("height") or 0),
                int(metadata.get("width") or 0),
                list(metadata.get("clues") or []),
            )

    def _candidate_map(self, puzzle: PuzzleRecord) -> dict[str, list[dict[str, Any]]]:
        truth = puzzle.ground_truth
        raw = truth.get("candidate_rectangles")
        if raw is None:
            raw = truth.get("rectangle_candidates")
        candidates = self._coerce_candidate_map(raw)
        if candidates:
            return candidates
        height, width, clues = self._puzzle_parts(puzzle)
        return self._enumerate_candidates(height, width, clues)

    @staticmethod
    def _coerce_candidate_map(raw: Any) -> dict[str, list[dict[str, Any]]]:
        if isinstance(raw, dict):
            result: dict[str, list[dict[str, Any]]] = {}
            for clue, rectangles in raw.items():
                if isinstance(rectangles, list):
                    result[str(clue)] = [item for item in rectangles if isinstance(item, dict)]
                elif isinstance(rectangles, dict):
                    nested = rectangles.get("rectangles", rectangles.get("candidates", []))
                    result[str(clue)] = [item for item in nested if isinstance(item, dict)]
            return result
        if isinstance(raw, list):
            result = {}
            for item in raw:
                if not isinstance(item, dict):
                    continue
                clue = item.get("clue") or item.get("cell") or item.get("clue_cell")
                rectangles = item.get("rectangles", item.get("candidates", []))
                if clue is not None and isinstance(rectangles, list):
                    result[str(clue)] = [value for value in rectangles if isinstance(value, dict)]
            return result
        return {}

    @classmethod
    def _candidate_summary(
        cls,
        candidates: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "clue": clue,
                "count": len(rectangles),
                "examples": rectangles[:3],
            }
            for clue, rectangles in sorted(candidates.items())
        ]

    @classmethod
    def _clue_area_analysis(
        cls,
        height: int,
        width: int,
        clues: list[dict[str, Any]],
        candidates: dict[str, list[dict[str, Any]]],
        evaluations: Any,
    ) -> list[dict[str, Any]]:
        evaluation_map: dict[str, dict[str, Any]] = {}
        if isinstance(evaluations, list):
            for evaluation in evaluations:
                if isinstance(evaluation, dict):
                    cell = evaluation.get("clue") or evaluation.get("cell")
                    if cell is not None:
                        evaluation_map[str(cell)] = evaluation
        result = []
        for clue in clues:
            cell = cls._clue_cell(clue)
            area = cls._clue_value(clue)
            shapes = [
                {"height": rectangle_height, "width": area // rectangle_height}
                for rectangle_height in range(1, max(0, height) + 1)
                if area > 0
                and area % rectangle_height == 0
                and area // rectangle_height <= width
            ]
            analysis = {
                "cell": cell,
                "area": area,
                "factor_shapes": shapes,
                "candidate_count": len(candidates.get(cell, [])),
            }
            for key, value in evaluation_map.get(cell, {}).items():
                analysis.setdefault(key, value)
            result.append(analysis)
        return result

    @staticmethod
    def _most_constrained_clue(
        candidates: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        clue, rectangles = min(candidates.items(), key=lambda item: (len(item[1]), item[0]))
        return {
            "clue": clue,
            "candidate_count": len(rectangles),
            "candidate_examples": rectangles[:5],
        }

    def _solution_space(
        self,
        height: int,
        width: int,
        clues: list[dict[str, Any]],
        truth: dict[str, Any],
    ) -> dict[str, Any]:
        solutions: list[list[dict[str, Any]]] = []
        if height > 0 and width > 0:
            try:
                if not validate_structure(height, width, clues):
                    solutions = solve_shikaku(height, width, clues, limit=2)
            except (TypeError, ValueError, KeyError):
                solutions = []
        differences: list[str] = []
        if len(solutions) > 1:
            first = self._solution_ownership(solutions[0])
            second = self._solution_ownership(solutions[1])
            differences = sorted(
                cell
                for cell in set(first) | set(second)
                if first.get(cell) != second.get(cell)
            )
        status = truth.get("solvability_status")
        if status is None:
            status = "unsolvable" if not solutions else "unique" if len(solutions) == 1 else "ambiguous"
        return {
            "solution_count_capped": len(solutions),
            "cap": 2,
            "status": status,
            "witness_differences": differences,
        }

    @classmethod
    def _move_impact(
        cls,
        candidates: dict[str, list[dict[str, Any]]],
        move: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(move, dict):
            return []
        clue = move.get("clue") or move.get("cell") or move.get("clue_cell")
        rectangle = move.get("rectangle") or move.get("candidate")
        if not isinstance(rectangle, dict) and cls._looks_like_rectangle(move):
            rectangle = move
        if not isinstance(rectangle, dict):
            return []
        chosen_cells = cls._rectangle_cells(rectangle)
        chosen_signature = cls._rectangle_signature(rectangle)
        eliminated: list[dict[str, Any]] = []
        for owner, rectangles in sorted(candidates.items()):
            for candidate in rectangles:
                signature = cls._rectangle_signature(candidate)
                if str(owner) == str(clue) and signature != chosen_signature:
                    eliminated.append(
                        {
                            "clue": owner,
                            "rectangle": candidate,
                            "reason": "alternative_for_placed_clue",
                        }
                    )
                    continue
                if str(owner) == str(clue) or signature == chosen_signature:
                    continue
                candidate_cells = cls._rectangle_cells(candidate)
                if chosen_cells and candidate_cells and chosen_cells & candidate_cells:
                    eliminated.append(
                        {
                            "clue": owner,
                            "rectangle": candidate,
                            "reason": "overlaps_placed_rectangle",
                        }
                    )
        return eliminated

    @classmethod
    def _infer_forced_ownership(
        cls,
        candidates: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, str]]:
        ownership: dict[str, set[str]] = {}
        for clue, rectangles in candidates.items():
            if not rectangles:
                continue
            common = cls._rectangle_cells(rectangles[0])
            for rectangle in rectangles[1:]:
                common &= cls._rectangle_cells(rectangle)
            for cell in common:
                ownership.setdefault(cell, set()).add(clue)
        return [
            {"cell": cell, "clue": next(iter(owners))}
            for cell, owners in sorted(ownership.items())
            if len(owners) == 1
        ]

    @classmethod
    def _solution_ownership(
        cls,
        rectangles: list[dict[str, Any]],
    ) -> dict[str, str]:
        ownership: dict[str, str] = {}
        for index, rectangle in enumerate(rectangles):
            clue = str(
                rectangle.get("clue")
                or rectangle.get("cell")
                or rectangle.get("clue_cell")
                or f"rectangle_{index + 1}"
            )
            for cell in cls._rectangle_cells(rectangle):
                ownership[cell] = clue
        return ownership

    @classmethod
    def _rectangle_cells(cls, rectangle: dict[str, Any]) -> set[str]:
        explicit = rectangle.get("cells")
        if isinstance(explicit, list):
            return {str(cell) for cell in explicit}

        bounds: tuple[int, int, int, int] | None = None
        for keys in (
            ("top", "left", "bottom", "right"),
            ("r1", "c1", "r2", "c2"),
            ("start_row", "start_col", "end_row", "end_col"),
        ):
            if all(key in rectangle for key in keys):
                try:
                    bounds = tuple(int(rectangle[key]) for key in keys)  # type: ignore[assignment]
                except (TypeError, ValueError):
                    bounds = None
                break
        if bounds is None:
            top_left = rectangle.get("top_left")
            bottom_right = rectangle.get("bottom_right")
            start = cls._parse_cell(top_left)
            end = cls._parse_cell(bottom_right)
            if start is not None and end is not None:
                bounds = (start[0], start[1], end[0], end[1])
        if bounds is None:
            return set()

        top, left, bottom, right = bounds
        row_offset = 1 if min(top, bottom) == 0 else 0
        col_offset = 1 if min(left, right) == 0 else 0
        return {
            f"r{row + row_offset}c{column + col_offset}"
            for row in range(min(top, bottom), max(top, bottom) + 1)
            for column in range(min(left, right), max(left, right) + 1)
        }

    @staticmethod
    def _looks_like_rectangle(value: dict[str, Any]) -> bool:
        return any(
            all(key in value for key in keys)
            for keys in (
                ("top", "left", "bottom", "right"),
                ("r1", "c1", "r2", "c2"),
                ("start_row", "start_col", "end_row", "end_col"),
                ("top_left", "bottom_right"),
            )
        )

    @classmethod
    def _rectangle_signature(cls, rectangle: dict[str, Any]) -> str:
        clue = str(
            rectangle.get("clue")
            or rectangle.get("cell")
            or rectangle.get("clue_cell")
            or ""
        )
        return cls._stable_signature(
            {"clue": clue, "cells": sorted(cls._rectangle_cells(rectangle))}
        )

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
    def _clue_cell(clue: dict[str, Any]) -> str:
        return str(clue.get("cell") or clue.get("clue") or clue.get("id") or "")

    @staticmethod
    def _clue_value(clue: dict[str, Any]) -> int:
        try:
            return int(clue.get("value", clue.get("area", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _suggested_move(truth: dict[str, Any]) -> Any:
        return truth.get("suggested_move", truth.get("suggested_rectangle"))

    @staticmethod
    def _stable_signature(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _limited(value: Any, limit: int) -> Any:
        if isinstance(value, list):
            return value[:limit]
        if isinstance(value, dict):
            return dict(list(value.items())[:limit])
        return value

    @classmethod
    def _enumerate_candidates(
        cls,
        height: int,
        width: int,
        clues: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        clue_positions = {
            position
            for clue in clues
            if (position := cls._parse_cell(cls._clue_cell(clue))) is not None
        }
        result: dict[str, list[dict[str, Any]]] = {}
        for clue in clues:
            cell = cls._clue_cell(clue)
            position = cls._parse_cell(cell)
            area = cls._clue_value(clue)
            rectangles: list[dict[str, Any]] = []
            if position is None or area <= 0:
                result[cell] = rectangles
                continue
            clue_row, clue_column = position
            for rectangle_height in range(1, height + 1):
                if area % rectangle_height:
                    continue
                rectangle_width = area // rectangle_height
                if rectangle_width > width:
                    continue
                for top in range(1, height - rectangle_height + 2):
                    bottom = top + rectangle_height - 1
                    if not top <= clue_row <= bottom:
                        continue
                    for left in range(1, width - rectangle_width + 2):
                        right = left + rectangle_width - 1
                        if not left <= clue_column <= right:
                            continue
                        contained_clues = {
                            position
                            for position in clue_positions
                            if top <= position[0] <= bottom and left <= position[1] <= right
                        }
                        if contained_clues != {position}:
                            continue
                        rectangles.append(
                            {
                                "clue": cell,
                                "top": top,
                                "left": left,
                                "bottom": bottom,
                                "right": right,
                                "area": area,
                            }
                        )
            result[cell] = rectangles
        return result

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "shikaku_rectangle_candidate_scan": (
                "The scenario needs legal rectangle candidates or a forced ownership deduction."
            ),
            "shikaku_constraint_validation": (
                "The scenario needs deterministic clue, area, coverage, and overlap checks."
            ),
            "shikaku_solution_verification": (
                "The scenario needs solver-backed partition verification."
            ),
        }
        return reasons.get(
            tool_name,
            f"The {scenario.task_category} scenario requested Shikaku tool support.",
        )
