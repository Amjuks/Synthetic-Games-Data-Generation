from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Callable

from .models import PuzzleRecord, Scenario


Cell = tuple[int, int]
Grid = list[list[int]]

_CELL_PATTERN = re.compile(r"^r([1-9][0-9]*)c([1-9][0-9]*)$", re.IGNORECASE)
_D4_OPERATIONS = (
    "identity",
    "rotate_90",
    "rotate_180",
    "rotate_270",
    "reflect_horizontal",
    "reflect_vertical",
    "transpose",
    "anti_transpose",
)
_TRANSFORMATIONS = _D4_OPERATIONS + tuple(
    f"complement_{operation}" for operation in _D4_OPERATIONS
)
_DIFFICULTY_SPECS = {
    "easy": (4, 6, 6, ["latin_single", "direct_inequality"]),
    "medium": (5, 7, 9, ["candidate_elimination", "inequality_bounds"]),
    "hard": (6, 8, 12, ["inequality_chain", "latin_intersection"]),
    "expert": (7, 9, 16, ["arc_consistency", "multi_unit_reasoning"]),
}


def format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def parse_cell(cell: str) -> Cell:
    match = _CELL_PATTERN.fullmatch(str(cell))
    if not match:
        raise ValueError(f"Invalid cell label: {cell!r}")
    return int(match.group(1)) - 1, int(match.group(2)) - 1


def _cell_key(cell: str) -> tuple[int, int, str]:
    try:
        row, col = parse_cell(cell)
        return row, col, ""
    except (TypeError, ValueError):
        return 10**9, 10**9, str(cell)


def _invert_relation(relation: str) -> str:
    return ">" if relation == "<" else "<" if relation == ">" else relation


def _normalize_cell(raw_cell: Any) -> str:
    try:
        return format_cell(*parse_cell(str(raw_cell)))
    except (TypeError, ValueError):
        return str(raw_cell) if raw_cell is not None else ""


def _normalized_givens(givens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [
        {
            "cell": _normalize_cell(given.get("cell")),
            "value": given.get("value"),
        }
        if isinstance(given, dict)
        else {"cell": "", "value": None}
        for given in givens
    ]
    return sorted(
        normalized,
        key=lambda item: (*_cell_key(str(item.get("cell", ""))), repr(item.get("value"))),
    )


def _normalize_inequality(inequality: dict[str, Any], index: int = 0) -> dict[str, Any]:
    if not isinstance(inequality, dict):
        return {"id": "", "left": "", "relation": "", "right": ""}
    left = _normalize_cell(inequality.get("left"))
    right = _normalize_cell(inequality.get("right"))
    relation = str(inequality.get("relation", ""))
    if _cell_key(right) < _cell_key(left):
        left, right = right, left
        relation = _invert_relation(relation)
    raw_id = inequality.get("id")
    return {
        "id": str(raw_id) if raw_id is not None else "",
        "left": left,
        "relation": relation,
        "right": right,
    }


def _id_key(identifier: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"(.*?)([0-9]+)", str(identifier))
    if match:
        return match.group(1), int(match.group(2)), ""
    return str(identifier), 10**9, str(identifier)


def _normalized_inequalities(
    inequalities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = [
        _normalize_inequality(inequality, index)
        for index, inequality in enumerate(inequalities)
    ]
    return sorted(
        normalized,
        key=lambda item: (
            _id_key(str(item.get("id", ""))),
            _cell_key(str(item.get("left", ""))),
            _cell_key(str(item.get("right", ""))),
            str(item.get("relation", "")),
        ),
    )


def canonical_puzzle(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
) -> str:
    payload = {
        "size": size,
        "givens": _normalized_givens(givens),
        "inequalities": _normalized_inequalities(inequalities),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def parse_puzzle(
    puzzle: str,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(puzzle)
    return int(payload["size"]), list(payload["givens"]), list(payload["inequalities"])


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_structure(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate sparse givens and explicit adjacent inequality declarations."""

    violations: list[dict[str, Any]] = []
    size_valid = _is_plain_int(size) and 2 <= size <= 9
    if not size_valid:
        violations.append({"type": "grid_size", "size": size})
    if not isinstance(givens, list):
        violations.append({"type": "givens_type", "actual": type(givens).__name__})
        givens = []
    if not isinstance(inequalities, list):
        violations.append(
            {"type": "inequalities_type", "actual": type(inequalities).__name__}
        )
        inequalities = []

    seen_givens: dict[str, int] = {}
    unit_values: dict[tuple[str, int, int], str] = {}
    for index, given in enumerate(givens):
        if not isinstance(given, dict):
            violations.append({"type": "given_type", "index": index + 1})
            continue
        raw_cell = given.get("cell")
        try:
            row, col = parse_cell(str(raw_cell))
            cell = format_cell(row, col)
        except (TypeError, ValueError):
            violations.append({"type": "cell_format", "kind": "given", "index": index + 1, "cell": raw_cell})
            row = col = -1
            cell = None
        if cell is not None:
            if size_valid and not (0 <= row < size and 0 <= col < size):
                violations.append({"type": "cell_bounds", "kind": "given", "index": index + 1, "cell": cell})
            if cell in seen_givens:
                violations.append(
                    {
                        "type": "duplicate_given_cell",
                        "cell": cell,
                        "indices": [seen_givens[cell], index + 1],
                    }
                )
            else:
                seen_givens[cell] = index + 1
        value = given.get("value")
        if not _is_plain_int(value) or not size_valid or not 1 <= value <= size:
            violations.append(
                {"type": "given_value", "index": index + 1, "cell": cell, "value": value}
            )
        elif cell is not None and 0 <= row < size and 0 <= col < size:
            for axis, unit in (("row", row), ("column", col)):
                key = (axis, unit, value)
                if key in unit_values:
                    violations.append(
                        {
                            "type": "given_unit_conflict",
                            "axis": axis,
                            "unit": unit + 1,
                            "value": value,
                            "cells": [unit_values[key], cell],
                        }
                    )
                else:
                    unit_values[key] = cell

    seen_ids: dict[str, int] = {}
    seen_pairs: dict[tuple[str, str], tuple[str, str, int]] = {}
    for index, inequality in enumerate(inequalities):
        if not isinstance(inequality, dict):
            violations.append({"type": "inequality_type", "index": index + 1})
            continue
        normalized = _normalize_inequality(inequality, index)
        identifier = normalized["id"]
        if not identifier:
            violations.append({"type": "inequality_id", "index": index + 1})
        elif identifier in seen_ids:
            violations.append(
                {
                    "type": "duplicate_inequality_id",
                    "id": identifier,
                    "indices": [seen_ids[identifier], index + 1],
                }
            )
        else:
            seen_ids[identifier] = index + 1
        relation = normalized["relation"]
        if relation not in {"<", ">"}:
            violations.append(
                {"type": "inequality_relation", "id": identifier, "relation": relation}
            )
        parsed: list[Cell] = []
        for endpoint in ("left", "right"):
            raw_cell = normalized[endpoint]
            try:
                row, col = parse_cell(raw_cell)
                parsed.append((row, col))
                if size_valid and not (0 <= row < size and 0 <= col < size):
                    violations.append(
                        {
                            "type": "cell_bounds",
                            "kind": "inequality",
                            "id": identifier,
                            "endpoint": endpoint,
                            "cell": raw_cell,
                        }
                    )
            except (TypeError, ValueError):
                violations.append(
                    {
                        "type": "cell_format",
                        "kind": "inequality",
                        "id": identifier,
                        "endpoint": endpoint,
                        "cell": raw_cell,
                    }
                )
        if len(parsed) == 2 and sum(abs(a - b) for a, b in zip(parsed[0], parsed[1])) != 1:
            violations.append(
                {
                    "type": "inequality_adjacency",
                    "id": identifier,
                    "cells": [normalized["left"], normalized["right"]],
                }
            )
        pair = (normalized["left"], normalized["right"])
        if all(_cell_key(cell)[0] < 10**9 for cell in pair) and relation in {"<", ">"}:
            if pair in seen_pairs:
                previous_relation, previous_id, previous_index = seen_pairs[pair]
                violations.append(
                    {
                        "type": "duplicate_inequality" if relation == previous_relation else "contradictory_inequality",
                        "cells": list(pair),
                        "ids": [previous_id, identifier],
                        "indices": [previous_index, index + 1],
                    }
                )
            else:
                seen_pairs[pair] = (relation, identifier, index + 1)
    return violations


def _constraint_edges(
    inequalities: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Return ``(lesser, greater, id)`` for each strict relation."""

    edges = []
    for inequality in inequalities:
        normalized = _normalize_inequality(inequality)
        if normalized["relation"] == "<":
            edges.append((normalized["left"], normalized["right"], normalized["id"]))
        elif normalized["relation"] == ">":
            edges.append((normalized["right"], normalized["left"], normalized["id"]))
    return edges


def _propagate_domains(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
    initial: list[set[int]] | None = None,
) -> list[set[int]] | None:
    domains = (
        [set(domain) for domain in initial]
        if initial is not None
        else [set(range(1, size + 1)) for _ in range(size * size)]
    )
    if initial is None:
        for given in givens:
            row, col = parse_cell(str(given["cell"]))
            domains[row * size + col] = {int(given["value"])}

    units = [
        [row * size + col for col in range(size)] for row in range(size)
    ] + [
        [row * size + col for row in range(size)] for col in range(size)
    ]
    edges = [
        (parse_cell(lesser), parse_cell(greater))
        for lesser, greater, _ in _constraint_edges(inequalities)
    ]

    changed = True
    while changed:
        changed = False
        if any(not domain for domain in domains):
            return None
        for unit in units:
            singleton_values = [
                next(iter(domains[index])) for index in unit if len(domains[index]) == 1
            ]
            if len(singleton_values) != len(set(singleton_values)):
                return None
            fixed = set(singleton_values)
            for index in unit:
                if len(domains[index]) == 1:
                    continue
                reduced = domains[index] - fixed
                if not reduced:
                    return None
                if reduced != domains[index]:
                    domains[index] = reduced
                    changed = True
            for value in range(1, size + 1):
                positions = [index for index in unit if value in domains[index]]
                if not positions:
                    return None
                if len(positions) == 1 and domains[positions[0]] != {value}:
                    domains[positions[0]] = {value}
                    changed = True

        for lesser_cell, greater_cell in edges:
            lesser_index = lesser_cell[0] * size + lesser_cell[1]
            greater_index = greater_cell[0] * size + greater_cell[1]
            lesser_domain = domains[lesser_index]
            greater_domain = domains[greater_index]
            allowed_lesser = {value for value in lesser_domain if any(value < other for other in greater_domain)}
            allowed_greater = {value for value in greater_domain if any(other < value for other in lesser_domain)}
            if not allowed_lesser or not allowed_greater:
                return None
            if allowed_lesser != lesser_domain:
                domains[lesser_index] = allowed_lesser
                changed = True
            if allowed_greater != greater_domain:
                domains[greater_index] = allowed_greater
                changed = True
    return domains


def solve_futoshiki(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
    limit: int = 2,
) -> list[Grid]:
    """Return at most ``limit`` Latin grids using MRV and arc consistency."""

    if limit <= 0 or validate_structure(size, givens, inequalities):
        return []
    initial = _propagate_domains(size, givens, inequalities)
    if initial is None:
        return []
    solutions: list[Grid] = []

    def search(domains: list[set[int]]) -> None:
        if len(solutions) >= limit:
            return
        propagated = _propagate_domains(size, givens, inequalities, domains)
        if propagated is None:
            return
        unresolved = [index for index, domain in enumerate(propagated) if len(domain) > 1]
        if not unresolved:
            grid = [
                [next(iter(propagated[row * size + col])) for col in range(size)]
                for row in range(size)
            ]
            if not solution_violations(size, givens, inequalities, grid):
                solutions.append(grid)
            return
        selected = min(unresolved, key=lambda index: (len(propagated[index]), index))
        for value in sorted(propagated[selected]):
            branch = [set(domain) for domain in propagated]
            branch[selected] = {value}
            search(branch)
            if len(solutions) >= limit:
                return

    search(initial)
    return solutions


def _coerce_grid(grid: Grid | str, size: int) -> Grid | None:
    if isinstance(grid, str):
        if len(grid) != size * size or any(character not in "123456789" for character in grid):
            return None
        values = [int(character) for character in grid]
        return [values[index : index + size] for index in range(0, size * size, size)]
    if not isinstance(grid, list) or len(grid) != size:
        return None
    if any(not isinstance(row, list) or len(row) != size for row in grid):
        return None
    return grid


def solution_violations(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
    grid: Grid | str,
) -> list[dict[str, Any]]:
    parsed = _coerce_grid(grid, size)
    if parsed is None:
        return [{"type": "solution_shape"}]
    violations: list[dict[str, Any]] = []
    required = set(range(1, size + 1))
    for row in range(size):
        values = parsed[row]
        if any(not _is_plain_int(value) or value not in required for value in values):
            violations.append({"type": "solution_value", "row": row + 1, "values": values})
        elif set(values) != required:
            violations.append({"type": "row_not_latin", "row": row + 1, "values": values})
    for col in range(size):
        values = [parsed[row][col] for row in range(size)]
        if all(_is_plain_int(value) and value in required for value in values) and set(values) != required:
            violations.append({"type": "column_not_latin", "column": col + 1, "values": values})
    for given in givens:
        try:
            row, col = parse_cell(str(given["cell"]))
            expected = int(given["value"])
            actual = parsed[row][col]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if actual != expected:
            violations.append(
                {
                    "type": "given_mismatch",
                    "cell": format_cell(row, col),
                    "expected": expected,
                    "actual": actual,
                }
            )
    for inequality in inequalities:
        normalized = _normalize_inequality(inequality)
        try:
            left_row, left_col = parse_cell(normalized["left"])
            right_row, right_col = parse_cell(normalized["right"])
            left_value = parsed[left_row][left_col]
            right_value = parsed[right_row][right_col]
        except (ValueError, IndexError):
            continue
        relation = normalized["relation"]
        satisfied = left_value < right_value if relation == "<" else left_value > right_value if relation == ">" else False
        if not satisfied:
            violations.append(
                {
                    "type": "inequality_violation",
                    "id": normalized["id"],
                    "left": normalized["left"],
                    "left_value": left_value,
                    "relation": relation,
                    "right": normalized["right"],
                    "right_value": right_value,
                }
            )
    return violations


def render_futoshiki(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
    grid: Grid | str | None = None,
) -> str:
    parsed = _coerce_grid(grid, size) if grid is not None else None
    given_map: dict[Cell, Any] = {}
    for given in givens:
        try:
            given_map[parse_cell(str(given.get("cell")))] = given.get("value")
        except (AttributeError, ValueError):
            continue
    lines = [f"Futoshiki {size}x{size}", "Grid (. = empty):"]
    for row in range(size):
        lines.append(
            " ".join(
                str(parsed[row][col])
                if parsed is not None
                else str(given_map[(row, col)])
                if (row, col) in given_map
                else "."
                for col in range(size)
            )
        )
    lines.append("Inequalities:")
    if inequalities:
        for inequality in _normalized_inequalities(inequalities):
            lines.append(
                f"{inequality['id']}: {inequality['left']} {inequality['relation']} {inequality['right']}"
            )
    else:
        lines.append("(none)")
    lines.append(
        f"Rules: place 1-{size} exactly once in every row and column (no boxes), "
        "and satisfy every listed inequality."
    )
    return "\n".join(lines)


def _grid_to_string(grid: Grid) -> str:
    return "".join(str(value) for row in grid for value in row)


def _inequality_analysis(
    size: int,
    inequalities: list[dict[str, Any]],
) -> tuple[list[list[str]], dict[str, dict[str, int]]]:
    edges = [(lesser, greater) for lesser, greater, _ in _constraint_edges(inequalities)]
    successors: dict[str, list[str]] = {}
    predecessors: dict[str, list[str]] = {}
    for lesser, greater in edges:
        successors.setdefault(lesser, []).append(greater)
        predecessors.setdefault(greater, []).append(lesser)
        successors.setdefault(greater, [])
        predecessors.setdefault(lesser, [])

    chains: list[list[str]] = []

    def walk(node: str, path: list[str]) -> None:
        if len(chains) >= 100:
            return
        next_nodes = sorted(successors.get(node, []), key=_cell_key)
        progressed = False
        for next_node in next_nodes:
            if next_node in path:
                chains.append(path + [next_node])
            else:
                progressed = True
                walk(next_node, path + [next_node])
        if not progressed and len(path) > 1:
            chains.append(path)

    starts = sorted(
        (node for node in successors if not predecessors.get(node)), key=_cell_key
    )
    for start in starts:
        walk(start, [start])
    if not starts:
        for start in sorted(successors, key=_cell_key):
            walk(start, [start])
            if chains:
                break

    cells = [format_cell(row, col) for row in range(size) for col in range(size)]
    minimum = {cell: 1 for cell in cells}
    maximum = {cell: size for cell in cells}
    # Repeated relaxation yields longest-path bounds on acyclic graphs and
    # exposes impossible cycles by moving a bound outside 1..N.
    for _ in range(size * size):
        changed = False
        for lesser, greater in edges:
            new_minimum = max(minimum[greater], minimum[lesser] + 1)
            new_maximum = min(maximum[lesser], maximum[greater] - 1)
            if new_minimum != minimum[greater]:
                minimum[greater] = new_minimum
                changed = True
            if new_maximum != maximum[lesser]:
                maximum[lesser] = new_maximum
                changed = True
        if not changed:
            break
    bounds = {
        cell: {"minimum": minimum[cell], "maximum": maximum[cell]}
        for cell in cells
        if minimum[cell] != 1 or maximum[cell] != size
    }
    return chains, bounds


def build_ground_truth(
    size: int,
    givens: list[dict[str, Any]],
    inequalities: list[dict[str, Any]],
    solution: Grid | str,
    *,
    edge_case_kind: str | None = None,
    _solutions: list[Grid] | None = None,
) -> dict[str, Any]:
    structural = validate_structure(size, givens, inequalities)
    solutions = (
        _solutions
        if _solutions is not None
        else solve_futoshiki(size, givens, inequalities, limit=2) if not structural else []
    )
    if structural:
        validity_status, solvability_status = "invalid", "unknown"
    elif not solutions:
        validity_status, solvability_status = "valid", "unsolvable"
    elif len(solutions) == 1:
        validity_status, solvability_status = "valid", "unique"
    else:
        validity_status, solvability_status = "valid", "ambiguous"
    if edge_case_kind == "malformed_input":
        validity_status, solvability_status = "malformed", "unknown"

    selected = _coerce_grid(solution, size)
    if selected is None:
        selected = solutions[0] if solutions else [[0] * size for _ in range(size)]
    solution_text = _grid_to_string(selected)
    propagated = (
        _propagate_domains(size, givens, inequalities) if not structural else None
    )
    candidates = {
        format_cell(row, col): sorted(propagated[row * size + col])
        for row in range(size)
        for col in range(size)
    } if propagated is not None else {}
    given_cells = {str(given.get("cell")) for given in givens if isinstance(given, dict)}
    forced_values = {
        cell: values[0]
        for cell, values in candidates.items()
        if len(values) == 1 and cell not in given_cells
    }

    given_evaluations = []
    for given in givens:
        if not isinstance(given, dict):
            continue
        cell = str(given.get("cell"))
        try:
            row, col = parse_cell(cell)
            actual = selected[row][col]
        except (ValueError, IndexError):
            actual = None
        given_evaluations.append(
            {
                "cell": cell,
                "expected": given.get("value"),
                "actual": actual,
                "satisfied": actual == given.get("value"),
            }
        )
    inequality_evaluations = []
    for inequality in inequalities:
        if not isinstance(inequality, dict):
            continue
        normalized = _normalize_inequality(inequality)
        try:
            left_row, left_col = parse_cell(normalized["left"])
            right_row, right_col = parse_cell(normalized["right"])
            left_value = selected[left_row][left_col]
            right_value = selected[right_row][right_col]
        except (ValueError, IndexError):
            left_value = right_value = None
        relation = normalized["relation"]
        satisfied = (
            left_value is not None
            and right_value is not None
            and (left_value < right_value if relation == "<" else left_value > right_value if relation == ">" else False)
        )
        inequality_evaluations.append(
            {
                **normalized,
                "left_value": left_value,
                "right_value": right_value,
                "satisfied": satisfied,
            }
        )
    required = set(range(1, size + 1))
    row_evaluations = [
        {"row": row + 1, "values": selected[row], "satisfied": set(selected[row]) == required}
        for row in range(size)
    ]
    column_evaluations = [
        {
            "column": col + 1,
            "values": [selected[row][col] for row in range(size)],
            "satisfied": {selected[row][col] for row in range(size)} == required,
        }
        for col in range(size)
    ]
    chains, bounds = _inequality_analysis(size, inequalities) if not structural else ([], {})
    differences = []
    if len(solutions) > 1:
        differences = [
            {
                "cell": format_cell(row, col),
                "values": [solutions[0][row][col], solutions[1][row][col]],
            }
            for row in range(size)
            for col in range(size)
            if solutions[0][row][col] != solutions[1][row][col]
        ]

    suggested_move: dict[str, Any] | None = None
    if solvability_status == "unique":
        if forced_values:
            cell = min(forced_values, key=_cell_key)
            suggested_move = {
                "cell": cell,
                "value": forced_values[cell],
                "reason": "forced_by_propagation",
            }
        else:
            open_cells = [cell for cell in candidates if cell not in given_cells]
            if open_cells:
                cell = min(open_cells, key=lambda item: (len(candidates[item]), _cell_key(item)))
                row, col = parse_cell(cell)
                suggested_move = {
                    "cell": cell,
                    "value": selected[row][col],
                    "candidates": candidates[cell],
                    "reason": "most_constrained_in_unique_solution",
                }

    return {
        "size": size,
        "givens": givens,
        "inequalities": inequalities,
        "solution": solution_text,
        "solution_grid": selected,
        "solved_board": render_futoshiki(size, givens, inequalities, selected),
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": solvability_status == "unique",
        "solution_count": 0 if edge_case_kind == "malformed_input" else len(solutions),
        "structural_violations": structural,
        "solution_violations": solution_violations(size, givens, inequalities, selected),
        "given_evaluations": given_evaluations,
        "inequality_evaluations": inequality_evaluations,
        "row_evaluations": row_evaluations,
        "column_evaluations": column_evaluations,
        "propagated_candidates": candidates,
        "forced_values": forced_values,
        "inequality_chains": chains,
        "inequality_bounds": bounds,
        "solution_differences": differences,
        "suggested_move": suggested_move,
    }


class FutoshikiPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get(
            "persistent_bank_filename", "futoshiki_puzzle_bank.jsonl"
        )
        self.usage_path = output_path / puzzle_config.get(
            "usage_stats_filename", "futoshiki_puzzle_usage.json"
        )
        self.seed = int(config.get("generation", {}).get("random_seed", 17))
        self._lineage_cache: dict[tuple[str, int], PuzzleRecord] = {}
        self.base_bank = self._build_base_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        difficulty = scenario.difficulty if scenario.difficulty in _DIFFICULTY_SPECS else "easy"
        lineage = max(0, sample_index) // len(_TRANSFORMATIONS)
        parent = self._lineage_record(difficulty, lineage)
        if scenario.edge_case == "malformed_input":
            return self._malformed_variant(parent, sample_index)
        if scenario.edge_case == "invalid_constraints":
            inequalities = [
                {"id": "I1", "left": "r1c1", "relation": "<", "right": "r1c2"},
                {"id": "I1", "left": "r1c1", "relation": ">", "right": "r1c2"},
            ]
            return self._fixture_variant(parent, sample_index, [], inequalities, "invalid_constraints")
        if scenario.edge_case == "unsolvable_puzzle":
            # r1c1 < r1c2 < r2c2 < r2c1 < r1c1: a strict cycle
            # around one 2x2 block, while every declaration remains adjacent.
            inequalities = [
                {"id": "I1", "left": "r1c1", "relation": "<", "right": "r1c2"},
                {"id": "I2", "left": "r1c2", "relation": "<", "right": "r2c2"},
                {"id": "I3", "left": "r2c1", "relation": ">", "right": "r2c2"},
                {"id": "I4", "left": "r1c1", "relation": ">", "right": "r2c1"},
            ]
            return self._fixture_variant(parent, sample_index, [], inequalities, "unsolvable_puzzle")
        if scenario.edge_case == "ambiguous_puzzle":
            return self._fixture_variant(parent, sample_index, [], [], "ambiguous_puzzle")

        position = sample_index % len(_TRANSFORMATIONS)
        operation = _D4_OPERATIONS[position % len(_D4_OPERATIONS)]
        complement = position >= len(_D4_OPERATIONS)
        name = f"complement_{operation}" if complement else operation
        return self._transformed_variant(parent, operation, complement, name)

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        for key in {puzzle.puzzle_id, puzzle.parent_puzzle_id or puzzle.puzzle_id}:
            self.usage_stats[key] = self.usage_stats.get(key, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(
            json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _build_base_bank(self) -> list[PuzzleRecord]:
        records = []
        for difficulty in _DIFFICULTY_SPECS:
            record = self._generate_lineage(difficulty, 0)
            self._lineage_cache[(difficulty, 0)] = record
            records.append(record)
        return records

    def _lineage_record(self, difficulty: str, lineage: int) -> PuzzleRecord:
        key = (difficulty, lineage)
        if key not in self._lineage_cache:
            self._lineage_cache[key] = self._generate_lineage(difficulty, lineage)
        return self._lineage_cache[key]

    def _generate_lineage(self, difficulty: str, lineage: int) -> PuzzleRecord:
        size, given_count, inequality_count, strategies = _DIFFICULTY_SPECS[difficulty]
        difficulty_offset = list(_DIFFICULTY_SPECS).index(difficulty) + 1
        rng = random.Random(
            self.seed * 1_000_003 + difficulty_offset * 100_019 + lineage * 7_919
        )
        for _ in range(100):
            target = _latin_solution(size, rng)
            cells = [(row, col) for row in range(size) for col in range(size)]
            rng.shuffle(cells)
            givens = [
                {"cell": format_cell(row, col), "value": target[row][col]}
                for row, col in cells[:given_count]
            ]
            adjacent_pairs = [
                ((row, col), (row, col + 1))
                for row in range(size)
                for col in range(size - 1)
            ] + [
                ((row, col), (row + 1, col))
                for row in range(size - 1)
                for col in range(size)
            ]
            rng.shuffle(adjacent_pairs)
            chosen_pairs = adjacent_pairs[:inequality_count]
            inequalities = [
                _target_inequality(index + 1, first, second, target)
                for index, (first, second) in enumerate(chosen_pairs)
            ]
            unused_pairs = adjacent_pairs[inequality_count:]
            solutions = solve_futoshiki(size, givens, inequalities, limit=2)
            while len(solutions) > 1:
                witness = next(
                    (solution for solution in solutions if solution != target), solutions[-1]
                )
                separating_pair = next(
                    (
                        pair
                        for pair in unused_pairs
                        if _pair_relation(pair, target) != _pair_relation(pair, witness)
                    ),
                    None,
                )
                if separating_pair is not None:
                    unused_pairs.remove(separating_pair)
                    inequalities.append(
                        _target_inequality(len(inequalities) + 1, *separating_pair, target)
                    )
                else:
                    given_cells = {str(given["cell"]) for given in givens}
                    separating_cell = next(
                        (
                            (row, col)
                            for row in range(size)
                            for col in range(size)
                            if format_cell(row, col) not in given_cells
                            and witness[row][col] != target[row][col]
                        ),
                        None,
                    )
                    if separating_cell is None:
                        break
                    row, col = separating_cell
                    givens.append({"cell": format_cell(row, col), "value": target[row][col]})
                solutions = solve_futoshiki(size, givens, inequalities, limit=2)
            if len(solutions) != 1 or solutions[0] != target:
                continue
            givens = _normalized_givens(givens)
            inequalities = _normalized_inequalities(inequalities)
            lineage_id = f"futoshiki_{difficulty}_{size}_lineage_{lineage}"
            signature = hashlib.sha1(
                canonical_puzzle(size, givens, inequalities).encode()
            ).hexdigest()[:16]
            return self._record(
                puzzle_id=lineage_id,
                size=size,
                givens=givens,
                inequalities=inequalities,
                solution_grid=target,
                difficulty=difficulty,
                strategies=strategies,
                parent_id=None,
                transformation="identity",
                source="generated",
                canonical_signature=signature,
                _solutions=solutions,
            )
        raise RuntimeError(
            f"Unable to generate a unique {difficulty} Futoshiki lineage {lineage}."
        )

    def _transformed_variant(
        self,
        parent: PuzzleRecord,
        operation: str,
        complement: bool,
        transformation: str,
    ) -> PuzzleRecord:
        size, givens, inequalities = parse_puzzle(parent.puzzle)
        transform = _coordinate_transform(size, operation)
        transformed_givens = []
        for given in givens:
            row, col = transform(*parse_cell(str(given["cell"])))
            value = size + 1 - int(given["value"]) if complement else int(given["value"])
            transformed_givens.append({"cell": format_cell(row, col), "value": value})
        transformed_inequalities = []
        for inequality in inequalities:
            normalized = _normalize_inequality(inequality)
            left = format_cell(*transform(*parse_cell(normalized["left"])))
            right = format_cell(*transform(*parse_cell(normalized["right"])))
            relation = _invert_relation(normalized["relation"]) if complement else normalized["relation"]
            transformed_inequalities.append(
                _normalize_inequality(
                    {
                        "id": normalized["id"],
                        "left": left,
                        "relation": relation,
                        "right": right,
                    }
                )
            )
        parent_grid = _coerce_grid(parent.solution, size)
        assert parent_grid is not None
        transformed_grid = [[0] * size for _ in range(size)]
        for row in range(size):
            for col in range(size):
                new_row, new_col = transform(row, col)
                value = parent_grid[row][col]
                transformed_grid[new_row][new_col] = size + 1 - value if complement else value
        puzzle = canonical_puzzle(size, transformed_givens, transformed_inequalities)
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, puzzle),
            size=size,
            givens=transformed_givens,
            inequalities=transformed_inequalities,
            solution_grid=transformed_grid,
            difficulty=parent.difficulty,
            strategies=list(parent.required_strategies),
            parent_id=parent.puzzle_id,
            transformation=transformation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
        )

    def _fixture_variant(
        self,
        parent: PuzzleRecord,
        sample_index: int,
        givens: list[dict[str, Any]],
        inequalities: list[dict[str, Any]],
        kind: str,
    ) -> PuzzleRecord:
        size = 4
        structural = validate_structure(size, givens, inequalities)
        solutions = solve_futoshiki(size, givens, inequalities, limit=2) if not structural else []
        fallback = [[(row + col) % size + 1 for col in range(size)] for row in range(size)]
        transformation = f"{kind}_{sample_index}"
        puzzle = canonical_puzzle(size, givens, inequalities)
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, puzzle),
            size=size,
            givens=givens,
            inequalities=inequalities,
            solution_grid=solutions[0] if solutions else fallback,
            difficulty=parent.difficulty,
            strategies=list(parent.required_strategies),
            parent_id=parent.puzzle_id,
            transformation=transformation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
            edge_case_kind=kind,
            _solutions=solutions,
        )

    def _malformed_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, givens, inequalities = parse_puzzle(parent.puzzle)
        grid = _coerce_grid(parent.solution, size)
        assert grid is not None
        broken = (
            "Futoshiki ?x?: [1 < . > ??? | constraints missing",
            "Futoshiki grid: r1=(., 3, x); inequalities={broken",
            "Futoshiki: 1<2, r2c? > ]; givens unavailable",
        )[sample_index % 3]
        transformation = f"malformed_{sample_index}"
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, parent.puzzle),
            size=size,
            givens=givens,
            inequalities=inequalities,
            solution_grid=grid,
            difficulty=parent.difficulty,
            strategies=list(parent.required_strategies),
            parent_id=parent.puzzle_id,
            transformation=transformation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
            edge_case_kind="malformed_input",
            rendered_board=broken,
        )

    def _record(
        self,
        *,
        puzzle_id: str,
        size: int,
        givens: list[dict[str, Any]],
        inequalities: list[dict[str, Any]],
        solution_grid: Grid,
        difficulty: str,
        strategies: list[str],
        parent_id: str | None,
        transformation: str,
        source: str,
        canonical_signature: str,
        edge_case_kind: str | None = None,
        rendered_board: str | None = None,
        _solutions: list[Grid] | None = None,
    ) -> PuzzleRecord:
        givens = _normalized_givens(givens)
        inequalities = _normalized_inequalities(inequalities)
        puzzle = canonical_puzzle(size, givens, inequalities)
        solution = _grid_to_string(solution_grid)
        truth = build_ground_truth(
            size,
            givens,
            inequalities,
            solution,
            edge_case_kind=edge_case_kind,
            _solutions=_solutions,
        )
        usage_stats = getattr(self, "usage_stats", {})
        return PuzzleRecord(
            puzzle_id=puzzle_id,
            puzzle=puzzle,
            solution=solution,
            difficulty=difficulty,
            num_clues=len(givens) + len(inequalities),
            required_strategies=list(strategies),
            unique_solution_status=truth["unique_solution_status"],
            source=source,
            canonical_signature=canonical_signature,
            usage_count=usage_stats.get(puzzle_id, 0),
            parent_puzzle_id=parent_id,
            transformation=transformation,
            rendered_board=rendered_board or render_futoshiki(size, givens, inequalities),
            ground_truth=truth,
            metadata={
                "size": size,
                "givens": givens,
                "inequalities": inequalities,
                **({"edge_case_kind": edge_case_kind} if edge_case_kind else {}),
            },
        )

    @staticmethod
    def _variant_id(parent: PuzzleRecord, transformation: str, puzzle: str) -> str:
        return hashlib.sha1(
            f"{parent.puzzle_id}|{transformation}|{puzzle}".encode()
        ).hexdigest()[:16]

    def _ensure_bank(self) -> None:
        if self.bank_path.exists():
            return
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        with self.bank_path.open("w", encoding="utf-8") as handle:
            for puzzle in self.base_bank:
                handle.write(json.dumps(puzzle.to_dict(), ensure_ascii=False) + "\n")

    def _load_usage_stats(self) -> dict[str, int]:
        if not self.usage_path.exists():
            return {}
        return json.loads(self.usage_path.read_text(encoding="utf-8"))


def _latin_solution(size: int, rng: random.Random) -> Grid:
    symbols = rng.sample(range(1, size + 1), size)
    row_order = rng.sample(range(size), size)
    col_order = rng.sample(range(size), size)
    return [
        [symbols[(row_order[row] + col_order[col]) % size] for col in range(size)]
        for row in range(size)
    ]


def _pair_relation(pair: tuple[Cell, Cell], grid: Grid) -> str:
    first, second = pair
    return "<" if grid[first[0]][first[1]] < grid[second[0]][second[1]] else ">"


def _target_inequality(
    identifier: int,
    first: Cell,
    second: Cell,
    target: Grid,
) -> dict[str, Any]:
    return _normalize_inequality(
        {
            "id": f"I{identifier}",
            "left": format_cell(*first),
            "relation": _pair_relation((first, second), target),
            "right": format_cell(*second),
        }
    )


def _coordinate_transform(size: int, operation: str) -> Callable[[int, int], Cell]:
    transforms: dict[str, Callable[[int, int], Cell]] = {
        "identity": lambda row, col: (row, col),
        "rotate_90": lambda row, col: (col, size - 1 - row),
        "rotate_180": lambda row, col: (size - 1 - row, size - 1 - col),
        "rotate_270": lambda row, col: (size - 1 - col, row),
        "reflect_horizontal": lambda row, col: (size - 1 - row, col),
        "reflect_vertical": lambda row, col: (row, size - 1 - col),
        "transpose": lambda row, col: (col, row),
        "anti_transpose": lambda row, col: (size - 1 - col, size - 1 - row),
    }
    if operation not in transforms:
        raise ValueError(f"Unknown Futoshiki transformation: {operation}")
    return transforms[operation]
