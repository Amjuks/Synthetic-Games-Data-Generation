from __future__ import annotations

import hashlib
import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


def clues_for_line(line: list[int]) -> list[int]:
    clues: list[int] = []
    run = 0
    for value in line:
        if value:
            run += 1
        elif run:
            clues.append(run)
            run = 0
    if run:
        clues.append(run)
    return clues


def line_patterns(length: int, clues: list[int]) -> list[list[int]]:
    if not clues:
        return [[0] * length]
    patterns: list[list[int]] = []

    def place(index: int, start: int, values: list[int]) -> None:
        if index == len(clues):
            patterns.append(values + [0] * (length - len(values)))
            return
        remaining = sum(clues[index:]) + (len(clues) - index - 1)
        for offset in range(start, length - remaining + 1):
            prefix = values + [0] * (offset - len(values)) + [1] * clues[index]
            next_start = len(prefix) + (1 if index < len(clues) - 1 else 0)
            if index < len(clues) - 1:
                prefix.append(0)
            place(index + 1, next_start, prefix)

    place(0, 0, [])
    return patterns


def validate_structure(height: int, width: int, row_clues: list[list[int]], column_clues: list[list[int]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not 3 <= height <= 15 or not 3 <= width <= 15:
        violations.append({"type": "grid_size", "height": height, "width": width})
    for label, expected, line_length, clues in (
        ("row", height, width, row_clues),
        ("column", width, height, column_clues),
    ):
        if len(clues) != expected:
            violations.append({"type": f"{label}_clue_count", "expected": expected, "actual": len(clues)})
            continue
        for index, clue in enumerate(clues):
            if not isinstance(clue, list) or any(not isinstance(value, int) or value <= 0 for value in clue):
                violations.append({"type": "invalid_clue_values", "axis": label, "index": index + 1, "clue": clue})
            elif sum(clue) + max(0, len(clue) - 1) > line_length:
                violations.append({"type": "infeasible_clue", "axis": label, "index": index + 1, "clue": clue})
    return violations


def canonical_puzzle(height: int, width: int, row_clues: list[list[int]], column_clues: list[list[int]]) -> str:
    return json.dumps({"height": height, "width": width, "row_clues": row_clues, "column_clues": column_clues}, separators=(",", ":"), sort_keys=True)


def parse_puzzle(puzzle: str) -> tuple[int, int, list[list[int]], list[list[int]]]:
    payload = json.loads(puzzle)
    return int(payload["height"]), int(payload["width"]), list(payload["row_clues"]), list(payload["column_clues"])


def solve_nonogram(height: int, width: int, row_clues: list[list[int]], column_clues: list[list[int]], limit: int = 2) -> list[list[list[int]]]:
    if validate_structure(height, width, row_clues, column_clues):
        return []
    row_options = [line_patterns(width, clues) for clues in row_clues]
    column_options = [line_patterns(height, clues) for clues in column_clues]
    solutions: list[list[list[int]]] = []
    rows: list[list[int]] = []

    def search(row: int) -> None:
        if len(solutions) >= limit:
            return
        if row == height:
            solutions.append(deepcopy(rows))
            return
        for candidate in row_options[row]:
            if any(not any(pattern[:row + 1] == [*([saved[col] for saved in rows]), candidate[col]] for pattern in column_options[col]) for col in range(width)):
                continue
            rows.append(candidate)
            search(row + 1)
            rows.pop()

    search(0)
    return solutions


def render_nonogram(height: int, width: int, row_clues: list[list[int]], column_clues: list[list[int]], grid: list[list[int]] | None = None) -> str:
    grid = grid or [[0] * width for _ in range(height)]
    lines = [f"Nonogram {height}x{width}", "Column clues:"]
    lines.extend(f"C{index + 1}: {' '.join(map(str, clue)) if clue else '0'}" for index, clue in enumerate(column_clues))
    lines.append("Row clues and grid:")
    for index, clue in enumerate(row_clues):
        cells = " ".join("#" if value else "." for value in grid[index])
        lines.append(f"R{index + 1} ({' '.join(map(str, clue)) if clue else '0'}): {cells}")
    lines.append("Rule: each clue lists consecutive filled-cell runs; separate runs need at least one empty cell.")
    return "\n".join(lines)


def _grid_string(grid: list[list[int]]) -> str:
    return "".join("#" if value else "." for row in grid for value in row)


def _grid_from_string(value: str, height: int, width: int) -> list[list[int]]:
    return [[1 if cell == "#" else 0 for cell in value[offset:offset + width]] for offset in range(0, height * width, width)]


def _clues_from_grid(grid: list[list[int]]) -> tuple[list[list[int]], list[list[int]]]:
    return [clues_for_line(row) for row in grid], [clues_for_line([row[col] for row in grid]) for col in range(len(grid[0]))]


class NonogramPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "nonogram_puzzle_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "nonogram_puzzle_usage.json")
        self.seed = int(config.get("generation", {}).get("random_seed", 17))
        self.base_bank = _build_base_puzzle_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        candidates = [puzzle for puzzle in self.base_bank if puzzle.difficulty == scenario.difficulty] or self.base_bank
        parent = min(candidates, key=lambda puzzle: (self.usage_stats.get(puzzle.puzzle_id, 0), puzzle.num_clues))
        if scenario.edge_case == "malformed_input":
            malformed = ("Nonogram clues: R1 (2, ???) | C1 = [", "Nonogram rows: [3], [x]; columns missing", "Nonogram ?x?: R=[1,1], C={broken}")[sample_index % 3]
            return self._make_variant(parent, f"malformed_{sample_index}", rendered_board=malformed, edge_case_kind="malformed_input")
        if scenario.edge_case == "invalid_clues":
            height, width, rows, columns = parse_puzzle(parent.puzzle)
            rows = deepcopy(rows)
            row = sample_index % height
            rows[row] = [width + 1 + (sample_index % 3)]
            return self._make_variant(parent, f"invalid_clues_row_{row}_{rows[row][0]}", row_clues=rows, column_clues=columns, edge_case_kind="invalid_clues")
        if scenario.edge_case == "unsolvable_puzzle":
            height, width, _, _ = parse_puzzle(parent.puzzle)
            contradiction = 1 + sample_index % max(1, height - 1)
            rows, columns = [[width] for _ in range(height)], [[contradiction] for _ in range(width)]
            return self._make_variant(parent, f"unsolvable_columns_{contradiction}", row_clues=rows, column_clues=columns, edge_case_kind="unsolvable_puzzle")
        if scenario.edge_case == "ambiguous_puzzle":
            height, width, _, _ = parse_puzzle(parent.puzzle)
            rows, columns = [[1] for _ in range(height)], [[1] for _ in range(width)]
            return self._make_variant(parent, "ambiguous", row_clues=rows, column_clues=columns, edge_case_kind="ambiguous_puzzle")
        operations = ("identity", "reflect_horizontal", "reflect_vertical", "rotate_90", "rotate_180", "rotate_270", "transpose", "anti_transpose")
        operation = operations[sample_index % len(operations)]
        return self._make_transformed_variant(parent, operation)

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        for key in {puzzle.puzzle_id, puzzle.parent_puzzle_id or puzzle.puzzle_id}:
            self.usage_stats[key] = self.usage_stats.get(key, 0) + 1
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _make_transformed_variant(self, parent: PuzzleRecord, operation: str) -> PuzzleRecord:
        height, width = parent.metadata["height"], parent.metadata["width"]
        grid = _grid_from_string(parent.solution, height, width)
        if operation == "reflect_horizontal":
            grid = list(reversed(grid))
        elif operation == "reflect_vertical":
            grid = [list(reversed(row)) for row in grid]
        elif operation == "rotate_90":
            grid = [list(row) for row in zip(*grid[::-1])]
        elif operation == "rotate_180":
            grid = [list(reversed(row)) for row in reversed(grid)]
        elif operation == "rotate_270":
            grid = [list(row) for row in zip(*grid)][::-1]
        elif operation == "transpose":
            grid = [list(row) for row in zip(*grid)]
        elif operation == "anti_transpose":
            grid = [list(row) for row in zip(*[list(reversed(row)) for row in reversed(grid)])]
        rows, columns = _clues_from_grid(grid)
        return self._make_variant(parent, operation, row_clues=rows, column_clues=columns, solution_grid=grid)

    def _make_variant(self, parent: PuzzleRecord, transformation: str, *, row_clues: list[list[int]] | None = None, column_clues: list[list[int]] | None = None, solution_grid: list[list[int]] | None = None, rendered_board: str | None = None, edge_case_kind: str | None = None) -> PuzzleRecord:
        height, width, parent_rows, parent_columns = parse_puzzle(parent.puzzle)
        row_clues, column_clues = row_clues or parent_rows, column_clues or parent_columns
        structural = validate_structure(height, width, row_clues, column_clues)
        solutions = solve_nonogram(height, width, row_clues, column_clues, limit=2) if not structural else []
        selected_grid = solution_grid or (solutions[0] if solutions else _grid_from_string(parent.solution, height, width))
        solution = _grid_string(selected_grid)
        validity = "malformed" if edge_case_kind == "malformed_input" else ("invalid" if structural else "valid")
        solvability = "unknown" if validity != "valid" else ("unique" if len(solutions) == 1 else "ambiguous" if len(solutions) > 1 else "unsolvable")
        board = rendered_board or render_nonogram(height, width, row_clues, column_clues)
        puzzle = canonical_puzzle(height, width, row_clues, column_clues)
        variant_id = hashlib.sha1(f"{parent.puzzle_id}|{transformation}|{puzzle}".encode()).hexdigest()[:16]
        truth = _build_ground_truth(height, width, row_clues, column_clues, solutions, solution, validity, solvability, structural)
        return PuzzleRecord(puzzle_id=variant_id, puzzle=puzzle, solution=solution, difficulty=parent.difficulty, num_clues=sum(sum(clue) for clue in row_clues), required_strategies=list(parent.required_strategies), unique_solution_status=solvability == "unique", source=parent.source, canonical_signature=parent.canonical_signature, usage_count=self.usage_stats.get(variant_id, 0), parent_puzzle_id=parent.puzzle_id, transformation=transformation, rendered_board=board, ground_truth=truth, metadata={"height": height, "width": width, "row_clues": row_clues, "column_clues": column_clues, **({"edge_case_kind": edge_case_kind} if edge_case_kind else {})})

    def _ensure_bank(self) -> None:
        if not self.bank_path.exists():
            self.bank_path.parent.mkdir(parents=True, exist_ok=True)
            self.bank_path.write_text("".join(json.dumps(puzzle.to_dict(), ensure_ascii=False) + "\n" for puzzle in self.base_bank), encoding="utf-8")

    def _load_usage_stats(self) -> dict[str, int]:
        return json.loads(self.usage_path.read_text(encoding="utf-8")) if self.usage_path.exists() else {}


def _build_ground_truth(height: int, width: int, rows: list[list[int]], columns: list[list[int]], solutions: list[list[list[int]]], solution: str, validity: str, solvability: str, structural: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _grid_from_string(solution, height, width)
    forced_filled: list[str] = []
    forced_empty: list[str] = []
    if solutions:
        for row in range(height):
            for col in range(width):
                values = {grid[row][col] for grid in solutions}
                if values == {1}:
                    forced_filled.append(f"r{row + 1}c{col + 1}")
                elif values == {0}:
                    forced_empty.append(f"r{row + 1}c{col + 1}")
    suggested = ({"cell": forced_filled[0], "state": "filled"} if forced_filled else {"cell": forced_empty[0], "state": "empty"} if forced_empty else None)
    return {"solution": solution, "solution_grid": selected, "solved_board": render_nonogram(height, width, rows, columns, selected), "validity_status": validity, "solvability_status": solvability, "unique_solution_status": solvability == "unique", "solution_count": len(solutions), "structural_violations": structural, "row_evaluations": [{"row": index + 1, "clue": clue, "actual": clues_for_line(selected[index]), "satisfied": clue == clues_for_line(selected[index])} for index, clue in enumerate(rows)], "column_evaluations": [{"column": index + 1, "clue": clue, "actual": clues_for_line([selected[row][index] for row in range(height)]), "satisfied": clue == clues_for_line([selected[row][index] for row in range(height)])} for index, clue in enumerate(columns)], "forced_filled": forced_filled, "forced_empty": forced_empty, "suggested_move": suggested}


def _build_base_puzzle_bank() -> list[PuzzleRecord]:
    specifications = [("easy", 5, 0.42, ["single_line_completion"]), ("medium", 6, 0.46, ["line_overlap", "cross_reference"]), ("hard", 8, 0.48, ["edge_forcing", "cross_reference"]), ("expert", 10, 0.50, ["probing", "multi_line_interaction"])]
    bank: list[PuzzleRecord] = []
    for offset, (difficulty, size, density, strategies) in enumerate(specifications):
        rng = random.Random(913 + offset)
        for _ in range(500):
            grid = [[1 if rng.random() < density else 0 for _ in range(size)] for _ in range(size)]
            if not all(any(row) for row in grid) or not all(any(row[col] for row in grid) for col in range(size)):
                continue
            rows, columns = _clues_from_grid(grid)
            if len(solve_nonogram(size, size, rows, columns, limit=2)) == 1:
                puzzle = canonical_puzzle(size, size, rows, columns)
                solution = _grid_string(grid)
                parent = PuzzleRecord(puzzle_id=f"nonogram_{difficulty}_1", puzzle=puzzle, solution=solution, difficulty=difficulty, num_clues=sum(sum(clue) for clue in rows), required_strategies=strategies, unique_solution_status=True, source="builtin", canonical_signature=f"nonogram_{difficulty}_1", usage_count=0, rendered_board=render_nonogram(size, size, rows, columns), metadata={"height": size, "width": size, "row_clues": rows, "column_clues": columns})
                parent.ground_truth = _build_ground_truth(size, size, rows, columns, [grid], solution, "valid", "unique", [])
                bank.append(parent)
                break
        else:
            raise RuntimeError(f"Unable to build a unique {difficulty} Nonogram.")
    return bank
