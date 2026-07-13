from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .models import PuzzleRecord, Scenario


Cell = tuple[int, int]


def canonical_puzzle(size: int, cages: list[dict[str, Any]]) -> str:
    return json.dumps({"size": size, "cages": cages}, separators=(",", ":"), sort_keys=True)


def parse_puzzle(puzzle: str) -> tuple[int, list[dict[str, Any]]]:
    payload = json.loads(puzzle)
    return int(payload["size"]), list(payload["cages"])


def format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def parse_cell(cell: str) -> Cell:
    row_text, col_text = cell.lower().split("c", 1)
    return int(row_text[1:]) - 1, int(col_text) - 1


def cage_satisfied(values: tuple[int, ...] | list[int], operation: str, target: int) -> bool:
    values = tuple(values)
    if operation == "=":
        return len(values) == 1 and values[0] == target
    if operation == "+":
        return sum(values) == target
    if operation == "*":
        return math.prod(values) == target
    if operation == "-":
        return len(values) == 2 and abs(values[0] - values[1]) == target
    if operation == "/":
        if len(values) != 2 or min(values) == 0:
            return False
        high, low = max(values), min(values)
        return high % low == 0 and high // low == target
    return False


def validate_structure(size: int, cages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if size < 3 or size > 9:
        violations.append({"type": "size", "message": "Grid size must be between 3 and 9."})
    seen: dict[str, str] = {}
    valid_operations = {"=", "+", "-", "*", "/"}
    for cage_index, cage in enumerate(cages):
        cage_id = str(cage.get("id", f"cage_{cage_index + 1}"))
        cells = cage.get("cells", [])
        operation = cage.get("operation")
        target = cage.get("target")
        if not isinstance(cells, list) or not cells:
            violations.append({"type": "empty_cage", "cage_id": cage_id})
            continue
        if operation not in valid_operations:
            violations.append({"type": "operation", "cage_id": cage_id, "operation": operation})
        if not isinstance(target, int) or target < 1:
            violations.append({"type": "target", "cage_id": cage_id, "target": target})
        if (len(cells) == 1) != (operation == "="):
            violations.append({"type": "arity", "cage_id": cage_id, "operation": operation})
        if operation in {"-", "/"} and len(cells) != 2:
            violations.append({"type": "arity", "cage_id": cage_id, "operation": operation})
        parsed: list[Cell] = []
        for cell in cells:
            try:
                row, col = parse_cell(str(cell))
            except (TypeError, ValueError):
                violations.append({"type": "cell_format", "cage_id": cage_id, "cell": cell})
                continue
            if not (0 <= row < size and 0 <= col < size):
                violations.append({"type": "cell_bounds", "cage_id": cage_id, "cell": cell})
                continue
            normalized = format_cell(row, col)
            if normalized in seen:
                violations.append({"type": "overlap", "cell": normalized, "cages": [seen[normalized], cage_id]})
            else:
                seen[normalized] = cage_id
            parsed.append((row, col))
        if parsed and not _is_connected(parsed):
            violations.append({"type": "disconnected", "cage_id": cage_id})
    expected = {format_cell(row, col) for row in range(size) for col in range(size)}
    missing = sorted(expected - set(seen))
    if missing:
        violations.append({"type": "missing_cells", "cells": missing})
    return violations


def _is_connected(cells: list[Cell]) -> bool:
    remaining = set(cells)
    frontier = [remaining.pop()]
    reached = set(frontier)
    while frontier:
        row, col = frontier.pop()
        for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if neighbor in remaining:
                remaining.remove(neighbor)
                reached.add(neighbor)
                frontier.append(neighbor)
    return len(reached) == len(cells)


def cage_candidate_tuples(size: int, cage: dict[str, Any]) -> list[tuple[int, ...]]:
    cells = [parse_cell(cell) for cell in cage["cells"]]
    result: list[tuple[int, ...]] = []
    for values in itertools.product(range(1, size + 1), repeat=len(cells)):
        if any(
            values[left] == values[right]
            and (cells[left][0] == cells[right][0] or cells[left][1] == cells[right][1])
            for left in range(len(cells))
            for right in range(left + 1, len(cells))
        ):
            continue
        if cage_satisfied(values, str(cage["operation"]), int(cage["target"])):
            result.append(values)
    return result


def solve_kenken(size: int, cages: list[dict[str, Any]], limit: int = 2) -> list[list[list[int]]]:
    if validate_structure(size, cages):
        return []
    prepared: list[tuple[list[Cell], list[tuple[int, ...]]]] = []
    for cage in cages:
        cells = [parse_cell(cell) for cell in cage["cells"]]
        tuples = cage_candidate_tuples(size, cage)
        if not tuples:
            return []
        prepared.append((cells, tuples))

    grid = [[0] * size for _ in range(size)]
    row_values = [set() for _ in range(size)]
    col_values = [set() for _ in range(size)]
    solutions: list[list[list[int]]] = []

    def compatible(cells: list[Cell], values: tuple[int, ...]) -> bool:
        return all(value not in row_values[row] and value not in col_values[col] for (row, col), value in zip(cells, values))

    def search(remaining: list[int]) -> None:
        if len(solutions) >= limit:
            return
        if not remaining:
            solutions.append([row[:] for row in grid])
            return
        choices: list[tuple[int, int, list[tuple[int, ...]]]] = []
        for cage_index in remaining:
            cells, tuples = prepared[cage_index]
            viable = [values for values in tuples if compatible(cells, values)]
            if not viable:
                return
            choices.append((len(viable), cage_index, viable))
        _, cage_index, viable = min(choices, key=lambda item: (item[0], item[1]))
        cells, _ = prepared[cage_index]
        next_remaining = [index for index in remaining if index != cage_index]
        for values in viable:
            for (row, col), value in zip(cells, values):
                grid[row][col] = value
                row_values[row].add(value)
                col_values[col].add(value)
            search(next_remaining)
            for (row, col), value in zip(cells, values):
                grid[row][col] = 0
                row_values[row].remove(value)
                col_values[col].remove(value)
            if len(solutions) >= limit:
                return

    search(list(range(len(prepared))))
    return solutions


def render_kenken(size: int, cages: list[dict[str, Any]], grid: list[list[int]] | None = None) -> str:
    grid = grid or [[0] * size for _ in range(size)]
    cell_to_cage = {cell: cage for cage in cages for cell in cage.get("cells", [])}
    lines = [f"KenKen {size}x{size}", "Grid:"]
    for row in range(size):
        values = [str(grid[row][col]) if grid[row][col] else "." for col in range(size)]
        lines.append(" ".join(values))
    lines.append("Cage layout:")
    for row in range(size):
        lines.append(" ".join(str(cell_to_cage.get(format_cell(row, col), {}).get("id", "?")) for col in range(size)))
    lines.append("Cage clues:")
    for cage in cages:
        lines.append(f"{cage['id']}: {cage['target']}{cage['operation']} [{', '.join(cage['cells'])}]")
    return "\n".join(lines)


def build_ground_truth(
    size: int,
    cages: list[dict[str, Any]],
    solution: str,
    *,
    edge_case_kind: str | None = None,
) -> dict[str, Any]:
    violations = validate_structure(size, cages)
    solutions = solve_kenken(size, cages, limit=2) if not violations else []
    if violations:
        validity_status, solvability_status = "invalid", "unknown"
    elif not solutions:
        validity_status, solvability_status = "valid", "unsolvable"
    elif len(solutions) == 1:
        validity_status, solvability_status = "valid", "unique"
    else:
        validity_status, solvability_status = "valid", "ambiguous"
    if edge_case_kind == "malformed_input":
        validity_status, solvability_status = "malformed", "unknown"

    tuple_map: dict[str, list[list[int]]] = {}
    forced_values: dict[str, int] = {}
    cage_evaluations: list[dict[str, Any]] = []
    solution_grid = _string_to_grid(solution, size)
    for cage in cages:
        candidates = cage_candidate_tuples(size, cage) if not violations else []
        tuple_map[cage["id"]] = [list(values) for values in candidates]
        for offset, cell in enumerate(cage.get("cells", [])):
            values = {candidate[offset] for candidate in candidates}
            if len(values) == 1:
                forced_values[cell] = next(iter(values))
        solved_values = [solution_grid[row][col] for row, col in map(parse_cell, cage.get("cells", []))]
        cage_evaluations.append({
            "cage_id": cage.get("id"),
            "values": solved_values,
            "satisfied": cage_satisfied(solved_values, cage.get("operation"), cage.get("target")),
        })
    suggested = None
    if forced_values:
        cell = sorted(forced_values)[0]
        suggested = {"cell": cell, "value": forced_values[cell], "reason": "forced_by_cage"}
    elif tuple_map:
        cage_id = min(tuple_map, key=lambda key: (len(tuple_map[key]) or 10**9, key))
        suggested = {"cage_id": cage_id, "candidate_tuples": tuple_map[cage_id], "reason": "most_constrained_cage"}

    return {
        "size": size,
        "cages": cages,
        "solution": solution,
        "solution_grid": solution_grid,
        "solved_board": render_kenken(size, cages, solution_grid),
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": len(solutions) == 1,
        "solution_count": len(solutions),
        "structural_violations": violations,
        "cage_evaluations": cage_evaluations,
        "cage_candidate_tuples": tuple_map,
        "forced_values": forced_values,
        "suggested_move": suggested,
    }


class KenKenPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "kenken_puzzle_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "kenken_puzzle_usage.json")
        self.seed = int(config.get("generation", {}).get("random_seed", 17))
        self.base_bank = self._build_base_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        candidates = [puzzle for puzzle in self.base_bank if puzzle.difficulty == scenario.difficulty] or self.base_bank
        parent = min(candidates, key=lambda puzzle: (self.usage_stats.get(puzzle.puzzle_id, 0), puzzle.puzzle_id))
        if scenario.edge_case == "malformed_input":
            return self._malformed_variant(parent, sample_index)
        if scenario.edge_case == "invalid_cages":
            return self._invalid_variant(parent, sample_index)
        if scenario.edge_case == "unsolvable_puzzle":
            return self._unsolvable_variant(parent, sample_index)
        if scenario.edge_case == "ambiguous_puzzle":
            return self._ambiguous_variant(parent, sample_index)
        transforms = ["identity", "rotate_90", "rotate_180", "rotate_270", "reflect_horizontal", "reflect_vertical", "transpose", "anti_transpose"]
        return self._transformed_variant(parent, transforms[sample_index % len(transforms)])

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        self.usage_stats[puzzle.puzzle_id] = self.usage_stats.get(puzzle.puzzle_id, 0) + 1
        parent_id = puzzle.parent_puzzle_id or puzzle.puzzle_id
        self.usage_stats[parent_id] = self.usage_stats.get(parent_id, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _build_base_bank(self) -> list[PuzzleRecord]:
        specs = [(4, "easy", 0), (5, "medium", 1), (6, "hard", 2), (6, "expert", 3)]
        return [self._generate_base(size, difficulty, offset) for size, difficulty, offset in specs]

    def _generate_base(self, size: int, difficulty: str, offset: int) -> PuzzleRecord:
        rng = random.Random(self.seed + offset * 1009)
        for attempt in range(200):
            solution_grid = _latin_solution(size, rng)
            cage_cells = _partition_cells(size, difficulty, rng)
            cages = _build_cages(cage_cells, solution_grid, rng)
            solutions = solve_kenken(size, cages, limit=2)
            if len(solutions) == 1:
                solution = _grid_to_string(solution_grid)
                puzzle_id = f"kenken_{difficulty}_{size}_{offset + 1}"
                return self._record(
                    puzzle_id=puzzle_id,
                    size=size,
                    cages=cages,
                    solution=solution,
                    difficulty=difficulty,
                    parent_id=None,
                    transformation="identity",
                    source="generated",
                    canonical_signature=puzzle_id,
                )
        raise RuntimeError(f"Unable to generate a unique {size}x{size} {difficulty} KenKen puzzle after 200 attempts.")

    def _transformed_variant(self, parent: PuzzleRecord, operation: str) -> PuzzleRecord:
        size, cages = parse_puzzle(parent.puzzle)
        solution_grid = _string_to_grid(parent.solution, size)
        transform = _coordinate_transform(size, operation)
        transformed_cages = []
        for cage in cages:
            transformed = deepcopy(cage)
            transformed["cells"] = sorted(format_cell(*transform(*parse_cell(cell))) for cell in cage["cells"])
            transformed_cages.append(transformed)
        transformed_grid = [[0] * size for _ in range(size)]
        for row in range(size):
            for col in range(size):
                new_row, new_col = transform(row, col)
                transformed_grid[new_row][new_col] = solution_grid[row][col]
        return self._record(
            puzzle_id=self._variant_id(parent, operation, transformed_cages),
            size=size,
            cages=transformed_cages,
            solution=_grid_to_string(transformed_grid),
            difficulty=parent.difficulty,
            parent_id=parent.puzzle_id,
            transformation=operation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
        )

    def _malformed_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        variant = self._transformed_variant(parent, "identity")
        variant.puzzle_id = self._variant_id(parent, f"malformed_{sample_index}", [])
        variant.transformation = f"malformed_{sample_index}"
        variant.rendered_board = "\n".join(variant.rendered_board.splitlines()[:3])
        size, cages = parse_puzzle(variant.puzzle)
        variant.ground_truth = build_ground_truth(size, cages, variant.solution, edge_case_kind="malformed_input")
        variant.metadata = {"size": size, "cages": cages, "edge_case_kind": "malformed_input"}
        return variant

    def _invalid_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, cages = parse_puzzle(parent.puzzle)
        cages = deepcopy(cages)
        cages[1]["cells"].append(cages[0]["cells"][0])
        return self._record_edge(parent, size, cages, f"invalid_{sample_index}", "invalid_cages")

    def _unsolvable_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, cages = parse_puzzle(parent.puzzle)
        cages = deepcopy(cages)
        cages[0]["target"] = size ** (len(cages[0]["cells"]) + 2) + 1
        return self._record_edge(parent, size, cages, f"unsolvable_{sample_index}", "unsolvable_puzzle")

    def _ambiguous_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, _ = parse_puzzle(parent.puzzle)
        target = size * (size + 1) // 2
        cages = [
            {"id": f"C{row + 1}", "target": target, "operation": "+", "cells": [format_cell(row, col) for col in range(size)]}
            for row in range(size)
        ]
        return self._record_edge(parent, size, cages, f"ambiguous_{sample_index}", "ambiguous_puzzle")

    def _record_edge(self, parent: PuzzleRecord, size: int, cages: list[dict[str, Any]], transformation: str, kind: str) -> PuzzleRecord:
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, cages),
            size=size,
            cages=cages,
            solution=parent.solution,
            difficulty=parent.difficulty,
            parent_id=parent.puzzle_id,
            transformation=transformation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
            edge_case_kind=kind,
        )

    def _record(
        self,
        *,
        puzzle_id: str,
        size: int,
        cages: list[dict[str, Any]],
        solution: str,
        difficulty: str,
        parent_id: str | None,
        transformation: str,
        source: str,
        canonical_signature: str,
        edge_case_kind: str | None = None,
    ) -> PuzzleRecord:
        puzzle = canonical_puzzle(size, cages)
        truth = build_ground_truth(size, cages, solution, edge_case_kind=edge_case_kind)
        return PuzzleRecord(
            puzzle_id=puzzle_id,
            puzzle=puzzle,
            solution=solution,
            difficulty=difficulty,
            num_clues=len(cages),
            required_strategies=["cage_arithmetic", "latin_elimination"],
            unique_solution_status=truth["unique_solution_status"],
            source=source,
            canonical_signature=canonical_signature,
            usage_count=0,
            parent_puzzle_id=parent_id,
            transformation=transformation,
            rendered_board=render_kenken(size, cages),
            ground_truth=truth,
            metadata={"size": size, "cages": cages, **({"edge_case_kind": edge_case_kind} if edge_case_kind else {})},
        )

    def _variant_id(self, parent: PuzzleRecord, transformation: str, cages: list[dict[str, Any]]) -> str:
        return hashlib.sha1(f"{parent.puzzle_id}|{transformation}|{json.dumps(cages, sort_keys=True)}".encode()).hexdigest()[:16]

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


def _latin_solution(size: int, rng: random.Random) -> list[list[int]]:
    symbols = rng.sample(range(1, size + 1), size)
    row_order = rng.sample(range(size), size)
    col_order = rng.sample(range(size), size)
    return [[symbols[(row_order[row] + col_order[col]) % size] for col in range(size)] for row in range(size)]


def _partition_cells(size: int, difficulty: str, rng: random.Random) -> list[list[Cell]]:
    max_size = {"easy": 2, "medium": 3, "hard": 3, "expert": 4}[difficulty]
    unassigned = {(row, col) for row in range(size) for col in range(size)}
    cages: list[list[Cell]] = []
    while unassigned:
        start = rng.choice(sorted(unassigned))
        unassigned.remove(start)
        cage = [start]
        desired = rng.randint(1 if difficulty == "easy" else 2, max_size)
        while len(cage) < desired:
            neighbors = sorted({
                neighbor
                for row, col in cage
                for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))
                if neighbor in unassigned
            })
            if not neighbors:
                break
            selected = rng.choice(neighbors)
            unassigned.remove(selected)
            cage.append(selected)
        cages.append(sorted(cage))
    return cages


def _build_cages(cage_cells: list[list[Cell]], solution: list[list[int]], rng: random.Random) -> list[dict[str, Any]]:
    cages: list[dict[str, Any]] = []
    for index, cells in enumerate(cage_cells):
        values = [solution[row][col] for row, col in cells]
        if len(cells) == 1:
            operation, target = "=", values[0]
        elif len(cells) == 2:
            options: list[tuple[str, int]] = [("+", sum(values)), ("*", math.prod(values)), ("-", abs(values[0] - values[1]))]
            high, low = max(values), min(values)
            if high % low == 0:
                options.append(("/", high // low))
            operation, target = rng.choice(options)
        else:
            operation = rng.choice(["+", "*"])
            target = sum(values) if operation == "+" else math.prod(values)
        cages.append({
            "id": f"C{index + 1}",
            "target": target,
            "operation": operation,
            "cells": [format_cell(row, col) for row, col in cells],
        })
    return cages


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
    return transforms[operation]


def _grid_to_string(grid: list[list[int]]) -> str:
    return "".join(str(value) for row in grid for value in row)


def _string_to_grid(solution: str, size: int) -> list[list[int]]:
    values = [int(value) for value in solution]
    return [values[index:index + size] for index in range(0, size * size, size)]
