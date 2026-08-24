from __future__ import annotations

import json
from typing import Any

from ..domain_support import (
    VariedDomainSupport,
    build_tool_usage,
    compact_tool_context,
    make_tool_catalog,
)
from ..futoshiki import (
    FutoshikiPuzzleManager,
    parse_puzzle,
    solve_futoshiki,
    validate_structure,
)
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class FutoshikiScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {
            "next_best_move": "ask_for_next_inequality_deduction",
            "validity_check": "verify_futoshiki_value_or_inequality",
            "rules_explanation": "learn_futoshiki_rules",
            "solve_puzzle": "request_futoshiki_solution_guidance",
            "inequality_analysis": "analyze_inequality_candidates",
            "hint": "ask_for_futoshiki_hint",
            "mistake_correction": "debug_latin_or_inequality_mistake",
            "technique_discussion": "discuss_futoshiki_strategy",
            "beginner_question": "learn_futoshiki_basics",
            "advanced_question": "analyze_inequality_chains",
            "general_chat": "general_futoshiki_help",
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
            "Use the supplied Futoshiki size, givens, and inequalities exactly unless malformed input is explicitly required.",
            "Ground every claim in row/column Latin-square uniqueness and the directed inequality constraints.",
        ]
        if task_category == "hint":
            constraints.append(
                "Prefer one forced value, bound, or candidate elimination over revealing the full grid."
            )
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Futoshiki assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed grid before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class FutoshikiValidator:
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
            size, givens, inequalities = parse_puzzle(puzzle.puzzle)
            structural = validate_structure(size, givens, inequalities)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parse_error = exc

        truth = puzzle.ground_truth
        if parse_error is not None and scenario.edge_case != "malformed_input":
            errors.append("selected puzzle cannot be parsed as canonical Futoshiki JSON")
        if scenario.edge_case == "none":
            if structural:
                errors.append("standard scenario has structurally invalid Futoshiki constraints")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique Futoshiki puzzle")
        if truth.get("solution", puzzle.solution) != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_constraints" and not structural:
            errors.append("invalid_constraints scenario has no structural violation")
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


class FutoshikiDomainAdapter(VariedDomainSupport):
    name = "futoshiki"
    tool_catalog = make_tool_catalog(
        name,
        {
            "futoshiki_candidate_scan": (
                "Propagated cell candidates, forced values, and a next deduction."
            ),
            "futoshiki_constraint_validation": (
                "Given, Latin-unit, inequality, structure, and solver validation."
            ),
            "futoshiki_solution_verification": (
                "Complete solver-backed Latin-grid and inequality verification."
            ),
            "futoshiki_rules_reference": "Canonical Futoshiki Latin-square and inequality rules.",
            "futoshiki_puzzle_summary": "Grid size, givens, inequalities, and difficulty.",
            "futoshiki_row_unit_analysis": (
                "Placed values, missing digits, and candidates for every row."
            ),
            "futoshiki_column_unit_analysis": (
                "Placed values, missing digits, and candidates for every column."
            ),
            "futoshiki_inequality_chain_analysis": (
                "Directed inequality chains and propagated numeric bounds."
            ),
            "futoshiki_move_impact_analysis": (
                "Latin-unit and inequality candidate eliminations caused by a value."
            ),
            "futoshiki_solution_space_analysis": (
                "Capped solution count and ambiguous witness differences."
            ),
        },
    )

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = FutoshikiScenarioGenerator(config)
        self.puzzle_manager = FutoshikiPuzzleManager(config)
        self.validator = FutoshikiValidator()
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
                "size": puzzle.metadata.get("size"),
                "given_count": len(puzzle.metadata.get("givens", [])),
                "inequality_count": len(puzzle.metadata.get("inequalities", [])),
            },
            output_for=lambda name: self._run_tool(name, puzzle),
        )

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        size, givens, inequalities = self._puzzle_parts(puzzle)
        return {
            "puzzle_id": puzzle.puzzle_id,
            "size": size,
            "givens": givens,
            "inequalities": inequalities,
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "transformation": puzzle.transformation,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        candidates = self._candidate_map(puzzle)
        return {
            "solution": truth.get("solution", puzzle.solution),
            "validity_status": truth.get("validity_status"),
            "solvability_status": truth.get("solvability_status"),
            "unique_solution_status": truth.get("unique_solution_status"),
            "solution_count": truth.get("solution_count"),
            "structural_violations": truth.get("structural_violations", [])[:16],
            "solution_violations": truth.get("solution_violations", [])[:16],
            "given_evaluations": truth.get("given_evaluations", [])[:16],
            "inequality_evaluations": truth.get("inequality_evaluations", [])[:20],
            "candidate_summary": self._candidate_summary(candidates),
            "inequality_bounds": self._limited(truth.get("inequality_bounds", {}), 20),
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
            raw_candidates = output.pop("cell_candidates", None)
            if raw_candidates is None:
                raw_candidates = output.pop("propagated_candidates", None)
            if isinstance(raw_candidates, dict):
                output["candidate_summary"] = self._candidate_summary(raw_candidates)
            for key in ("given_evaluations", "inequality_evaluations", "eliminations"):
                if key in output:
                    output[key] = self._limited(output[key], 20)
            for key in ("inequality_bounds", "forced_values"):
                if key in output:
                    output[key] = self._limited(output[key], 20)
            output.pop("solved_board", None)
            call["output"] = output
        return context

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Futoshiki givens and inequality directions exactly. Fill each row and "
            "column with every digit from 1 through N exactly once; there are no Sudoku boxes, and "
            "every < or > relation must hold between its stated endpoints."
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
                "grid_size": metadata.get("size"),
                "givens": metadata.get("givens", []),
                "inequalities": metadata.get("inequalities", []),
                "tool_used": sample.get("tool_used", False),
                "tool_name": sample.get("tool_usage_details", {}).get("tool_name"),
            }
        )
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input":
            return [
                "futoshiki_constraint_validation",
                "futoshiki_puzzle_summary",
                "futoshiki_rules_reference",
            ]
        if scenario.edge_case in {
            "invalid_constraints",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return [
                "futoshiki_constraint_validation",
                "futoshiki_inequality_chain_analysis",
                "futoshiki_solution_space_analysis",
            ]
        if scenario.task_category in {
            "rules_explanation",
            "beginner_question",
            "general_chat",
        }:
            return [
                "futoshiki_rules_reference",
                "futoshiki_puzzle_summary",
                "futoshiki_inequality_chain_analysis",
            ]
        if scenario.task_category in {"technique_discussion", "advanced_question"}:
            return [
                "futoshiki_row_unit_analysis",
                "futoshiki_column_unit_analysis",
                "futoshiki_inequality_chain_analysis",
                "futoshiki_move_impact_analysis",
            ]

        primary = self._tool_name_for_scenario(scenario)
        if primary == "futoshiki_solution_verification":
            return [
                "futoshiki_candidate_scan",
                "futoshiki_inequality_chain_analysis",
                "futoshiki_solution_space_analysis",
                primary,
            ]
        if primary == "futoshiki_candidate_scan":
            return [
                primary,
                "futoshiki_row_unit_analysis",
                "futoshiki_column_unit_analysis",
                "futoshiki_move_impact_analysis",
            ]
        if primary == "futoshiki_constraint_validation":
            return [
                primary,
                "futoshiki_inequality_chain_analysis",
                "futoshiki_solution_space_analysis",
            ]
        return [primary or "futoshiki_puzzle_summary", "futoshiki_constraint_validation"]

    @staticmethod
    def _tool_name_for_scenario(scenario: Scenario) -> str | None:
        if scenario.edge_case in {
            "invalid_constraints",
            "unsolvable_puzzle",
            "ambiguous_puzzle",
        }:
            return "futoshiki_constraint_validation"
        if scenario.tool_usage == "inequality_scan" or scenario.task_category in {
            "hint",
            "next_best_move",
            "inequality_analysis",
        }:
            return "futoshiki_candidate_scan"
        if scenario.tool_usage == "constraint_check" or scenario.task_category in {
            "validity_check",
            "mistake_correction",
        }:
            return "futoshiki_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "futoshiki_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        size, givens, inequalities = self._puzzle_parts(puzzle)
        candidates = self._candidate_map(puzzle)
        forced_values = truth.get("forced_values")
        if not isinstance(forced_values, dict):
            given_cells = {self._given_cell(given) for given in givens}
            forced_values = {
                cell: values[0]
                for cell, values in candidates.items()
                if len(values) == 1 and cell not in given_cells
            }
        suggested_move = self._suggested_move(truth)

        if tool_name == "futoshiki_candidate_scan":
            return {
                "cell_candidates": candidates,
                "forced_values": forced_values,
                "suggested_move": suggested_move,
            }
        if tool_name == "futoshiki_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "solution_violations": truth.get("solution_violations", []),
                "given_evaluations": truth.get("given_evaluations", []),
                "inequality_evaluations": truth.get("inequality_evaluations", []),
            }
        if tool_name == "futoshiki_solution_verification":
            return {
                "solution": truth.get("solution", puzzle.solution),
                "solution_grid": truth.get("solution_grid", []),
                "unique_solution_status": truth.get(
                    "unique_solution_status", puzzle.unique_solution_status
                ),
            }
        if tool_name == "futoshiki_rules_reference":
            return {
                "rules": [
                    "Each row and column contains every digit from 1 through N exactly once.",
                    "Each stated < or > inequality must hold from its left endpoint to its right endpoint.",
                    "Futoshiki has no Sudoku-style box constraints.",
                ]
            }
        if tool_name == "futoshiki_puzzle_summary":
            return {
                "size": size,
                "cell_count": size * size,
                "given_count": len(givens),
                "inequality_count": len(inequalities),
                "difficulty": puzzle.difficulty,
                "strategies": puzzle.required_strategies,
            }
        if tool_name == "futoshiki_row_unit_analysis":
            return {
                "rows": self._unit_analysis(size, givens, candidates, by_row=True)
            }
        if tool_name == "futoshiki_column_unit_analysis":
            return {
                "columns": self._unit_analysis(size, givens, candidates, by_row=False)
            }
        if tool_name == "futoshiki_inequality_chain_analysis":
            chains = truth.get("inequality_chains")
            if not isinstance(chains, list):
                chains = self._inequality_chains(inequalities)
            bounds = truth.get("inequality_bounds")
            if not isinstance(bounds, dict):
                bounds = {
                    cell: {
                        "minimum": min(values) if values else None,
                        "maximum": max(values) if values else None,
                    }
                    for cell, values in candidates.items()
                }
            return {
                "chains": chains,
                "bounds": bounds,
                "inequality_evaluations": truth.get("inequality_evaluations", []),
            }
        if tool_name == "futoshiki_move_impact_analysis":
            eliminations = self._move_impact(
                size,
                inequalities,
                candidates,
                suggested_move,
            )
            return {
                "suggested_move": suggested_move,
                "eliminations": eliminations,
                "candidate_eliminations": eliminations,
            }
        if tool_name == "futoshiki_solution_space_analysis":
            return self._solution_space(size, givens, inequalities, truth)
        return {}

    def _puzzle_parts(
        self,
        puzzle: PuzzleRecord,
    ) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            size, givens, inequalities = parse_puzzle(puzzle.puzzle)
            return int(size), list(givens), list(inequalities)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            metadata = puzzle.metadata
            return (
                int(metadata.get("size") or 0),
                list(metadata.get("givens") or []),
                list(metadata.get("inequalities") or []),
            )

    def _candidate_map(self, puzzle: PuzzleRecord) -> dict[str, list[int]]:
        truth = puzzle.ground_truth
        for key in ("cell_candidates", "propagated_candidates", "candidates"):
            raw = truth.get(key)
            if isinstance(raw, dict):
                result: dict[str, list[int]] = {}
                for cell, values in raw.items():
                    if isinstance(values, (list, tuple, set)):
                        result[str(cell)] = sorted(
                            int(value) for value in values if self._is_int(value)
                        )
                if result:
                    return result
        size, givens, inequalities = self._puzzle_parts(puzzle)
        return self._propagate_candidates(size, givens, inequalities)

    @staticmethod
    def _candidate_summary(candidates: dict[str, Any]) -> list[dict[str, Any]]:
        normalized = [
            (str(cell), list(values))
            for cell, values in candidates.items()
            if isinstance(values, (list, tuple, set))
        ]
        forced = [item for item in normalized if len(item[1]) <= 1]
        unresolved = sorted(
            (item for item in normalized if len(item[1]) > 1),
            key=lambda item: (len(item[1]), item[0]),
        )
        selected = (sorted(forced) + unresolved)[:20]
        return [
            {
                "cell": cell,
                "candidate_count": len(values),
                "candidates": sorted(values)[:7],
            }
            for cell, values in selected
        ]

    @classmethod
    def _propagate_candidates(
        cls,
        size: int,
        givens: list[dict[str, Any]],
        inequalities: list[dict[str, Any]],
    ) -> dict[str, list[int]]:
        domains = {
            f"r{row + 1}c{column + 1}": set(range(1, size + 1))
            for row in range(size)
            for column in range(size)
        }
        for given in givens:
            cell = cls._given_cell(given)
            value = cls._given_value(given)
            if cell in domains and 1 <= value <= size:
                domains[cell] = {value}

        changed = True
        while changed:
            changed = False
            singleton_values = {
                cell: next(iter(values))
                for cell, values in domains.items()
                if len(values) == 1
            }
            for cell, value in singleton_values.items():
                position = cls._parse_cell(cell)
                if position is None:
                    continue
                row, column = position
                for peer, values in domains.items():
                    peer_position = cls._parse_cell(peer)
                    if peer == cell or peer_position is None or len(values) <= 1:
                        continue
                    if peer_position[0] == row or peer_position[1] == column:
                        if value in values:
                            values.remove(value)
                            changed = True

            for inequality in inequalities:
                left = str(inequality.get("left", ""))
                right = str(inequality.get("right", ""))
                relation = inequality.get("relation")
                if left not in domains or right not in domains or relation not in {"<", ">"}:
                    continue
                left_values = domains[left]
                right_values = domains[right]
                allowed_left = {
                    left_value
                    for left_value in left_values
                    if any(
                        left_value < right_value if relation == "<" else left_value > right_value
                        for right_value in right_values
                    )
                }
                allowed_right = {
                    right_value
                    for right_value in right_values
                    if any(
                        left_value < right_value if relation == "<" else left_value > right_value
                        for left_value in left_values
                    )
                }
                if allowed_left != left_values:
                    domains[left] = allowed_left
                    changed = True
                if allowed_right != right_values:
                    domains[right] = allowed_right
                    changed = True
        return {cell: sorted(values) for cell, values in sorted(domains.items())}

    @classmethod
    def _unit_analysis(
        cls,
        size: int,
        givens: list[dict[str, Any]],
        candidates: dict[str, list[int]],
        *,
        by_row: bool,
    ) -> list[dict[str, Any]]:
        given_map = {cls._given_cell(given): cls._given_value(given) for given in givens}
        result = []
        for unit in range(1, size + 1):
            cells = [
                f"r{unit}c{offset}" if by_row else f"r{offset}c{unit}"
                for offset in range(1, size + 1)
            ]
            fixed = {
                cell: values[0]
                for cell in cells
                for values in [candidates.get(cell, [])]
                if len(values) == 1
            }
            result.append(
                {
                    "unit": f"{'row' if by_row else 'column'}_{unit}",
                    "givens": {
                        cell: given_map[cell] for cell in cells if cell in given_map
                    },
                    "fixed_values": fixed,
                    "remaining_digits": sorted(set(range(1, size + 1)) - set(fixed.values())),
                    "unresolved": {
                        cell: candidates.get(cell, [])
                        for cell in cells
                        if len(candidates.get(cell, [])) != 1
                    },
                }
            )
        return result

    @classmethod
    def _inequality_chains(
        cls,
        inequalities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        edges: dict[str, set[str]] = {}
        incoming: dict[str, set[str]] = {}
        identifiers: dict[tuple[str, str], list[str]] = {}
        for inequality in inequalities:
            left = str(inequality.get("left", ""))
            right = str(inequality.get("right", ""))
            relation = inequality.get("relation")
            if not left or not right or relation not in {"<", ">"}:
                continue
            lower, higher = (left, right) if relation == "<" else (right, left)
            edges.setdefault(lower, set()).add(higher)
            incoming.setdefault(higher, set()).add(lower)
            identifier = inequality.get("id")
            if identifier is not None:
                identifiers.setdefault((lower, higher), []).append(str(identifier))

        nodes = set(edges) | set(incoming)
        starts = sorted(node for node in nodes if not incoming.get(node)) or sorted(nodes)
        paths: set[tuple[str, ...]] = set()

        def walk(node: str, path: tuple[str, ...]) -> None:
            successors = sorted(edges.get(node, set()) - set(path))
            if not successors:
                if len(path) > 1:
                    paths.add(path)
                return
            for successor in successors:
                walk(successor, path + (successor,))

        for start in starts:
            walk(start, (start,))
        if not paths:
            paths = {(lower, higher) for lower, values in edges.items() for higher in values}
        return [
            {
                "cells_low_to_high": list(path),
                "length": len(path),
                "inequality_ids": [
                    identifier
                    for pair in zip(path, path[1:])
                    for identifier in identifiers.get(pair, [])
                ],
            }
            for path in sorted(paths)
        ]

    @classmethod
    def _move_impact(
        cls,
        size: int,
        inequalities: list[dict[str, Any]],
        candidates: dict[str, list[int]],
        move: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(move, dict):
            return []
        cell = move.get("cell")
        value = move.get("value")
        position = cls._parse_cell(cell)
        if position is None or not cls._is_int(value):
            return []
        value = int(value)
        row, column = position
        eliminations: dict[tuple[str, int], str] = {}
        for peer, values in candidates.items():
            peer_position = cls._parse_cell(peer)
            if peer == cell or peer_position is None:
                continue
            if (peer_position[0] == row or peer_position[1] == column) and value in values:
                eliminations[(peer, value)] = "latin_unit"

        for inequality in inequalities:
            left = str(inequality.get("left", ""))
            right = str(inequality.get("right", ""))
            relation = inequality.get("relation")
            if relation not in {"<", ">"} or cell not in {left, right}:
                continue
            peer = right if cell == left else left
            for candidate in candidates.get(peer, []):
                left_value = value if cell == left else candidate
                right_value = candidate if cell == left else value
                satisfied = left_value < right_value if relation == "<" else left_value > right_value
                if not satisfied:
                    eliminations[(peer, candidate)] = f"inequality_{left}{relation}{right}"
        return [
            {"cell": cell, "removed_value": value, "reason": reason}
            for (cell, value), reason in sorted(eliminations.items())
        ]

    def _solution_space(
        self,
        size: int,
        givens: list[dict[str, Any]],
        inequalities: list[dict[str, Any]],
        truth: dict[str, Any],
    ) -> dict[str, Any]:
        solutions: list[list[list[int]]] = []
        if size > 0:
            try:
                if not validate_structure(size, givens, inequalities):
                    solutions = solve_futoshiki(size, givens, inequalities, limit=2)
            except (TypeError, ValueError, KeyError):
                solutions = []
        differences = [
            f"r{row + 1}c{column + 1}"
            for row in range(size)
            for column in range(size)
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
    def _given_cell(given: dict[str, Any]) -> str:
        return str(given.get("cell") or "")

    @staticmethod
    def _given_value(given: dict[str, Any]) -> int:
        try:
            return int(given.get("value", 0))
        except (TypeError, ValueError):
            return 0

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
    def _is_int(value: Any) -> bool:
        try:
            int(value)
            return not isinstance(value, bool)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _suggested_move(truth: dict[str, Any]) -> Any:
        return truth.get("suggested_move", truth.get("suggested_value"))

    @staticmethod
    def _limited(value: Any, limit: int) -> Any:
        if isinstance(value, list):
            return value[:limit]
        if isinstance(value, dict):
            return dict(list(value.items())[:limit])
        return value

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "futoshiki_candidate_scan": (
                "The scenario needs propagated Latin-square and inequality candidates."
            ),
            "futoshiki_constraint_validation": (
                "The scenario needs deterministic given, unit, and inequality checks."
            ),
            "futoshiki_solution_verification": (
                "The scenario needs solver-backed Futoshiki solution verification."
            ),
        }
        return reasons.get(
            tool_name,
            f"The {scenario.task_category} scenario requested Futoshiki tool support.",
        )
