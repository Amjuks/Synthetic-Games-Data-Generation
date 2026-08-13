from __future__ import annotations

import hashlib
import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


def canonical_puzzle(grid: list[list[int]]) -> str:
    return json.dumps({"size": len(grid), "grid": grid}, separators=(",", ":"), sort_keys=True)


def parse_puzzle(puzzle: str) -> tuple[int, list[list[int]]]:
    payload = json.loads(puzzle)
    return int(payload["size"]), list(payload["grid"])


def validate_structure(size: int, grid: list[list[int]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not 4 <= size <= 10:
        violations.append({"type": "grid_size", "size": size})
    if len(grid) != size or any(not isinstance(row, list) or len(row) != size for row in grid):
        violations.append({"type": "grid_shape", "size": size})
        return violations
    for row, values in enumerate(grid):
        for col, value in enumerate(values):
            if not isinstance(value, int) or value < 1 or value > size:
                violations.append({"type": "cell_value", "cell": f"r{row + 1}c{col + 1}", "value": value})
    return violations


def solve_hitori(size: int, grid: list[list[int]], limit: int = 2) -> list[list[list[int]]]:
    """Return shade masks (1 means shaded) up to ``limit`` solutions."""
    if validate_structure(size, grid):
        return []
    mask = [[-1] * size for _ in range(size)]
    row_white = [set() for _ in range(size)]
    col_white = [set() for _ in range(size)]
    solutions: list[list[list[int]]] = []
    shade_candidates = [
        [
            any(other != col and grid[row][other] == grid[row][col] for other in range(size))
            or any(other != row and grid[other][col] == grid[row][col] for other in range(size))
            for col in range(size)
        ]
        for row in range(size)
    ]

    def connected() -> bool:
        white = {(row, col) for row in range(size) for col in range(size) if mask[row][col] == 0}
        if not white:
            return False
        frontier = [white.pop()]
        while frontier:
            row, col = frontier.pop()
            for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if neighbor in white:
                    white.remove(neighbor)
                    frontier.append(neighbor)
        return not white

    def search(index: int) -> None:
        if len(solutions) >= limit:
            return
        if index == size * size:
            if connected():
                solutions.append([row[:] for row in mask])
            return
        row, col = divmod(index, size)
        value = grid[row][col]
        # Prefer white so duplicate-free boards resolve immediately.
        if value not in row_white[row] and value not in col_white[col]:
            mask[row][col] = 0
            row_white[row].add(value); col_white[col].add(value)
            search(index + 1)
            row_white[row].remove(value); col_white[col].remove(value)
        # Shading a value that is unique in both of its lines can never be part
        # of a minimal valid Hitori solution: making it white only relaxes rules.
        if shade_candidates[row][col] and not ((row and mask[row - 1][col] == 1) or (col and mask[row][col -1] == 1)):
            mask[row][col] = 1
            search(index + 1)
        mask[row][col] = -1

    search(0)
    return solutions


def solution_violations(size: int, grid: list[list[int]], mask: list[list[int]]) -> list[dict[str, Any]]:
    if len(mask) != size or any(len(row) != size for row in mask):
        return [{"type": "solution_shape"}]
    violations: list[dict[str, Any]] = []
    for row in range(size):
        values = [grid[row][col] for col in range(size) if not mask[row][col]]
        if len(values) != len(set(values)):
            violations.append({"type": "row_duplicate", "row": row + 1})
    for col in range(size):
        values = [grid[row][col] for row in range(size) if not mask[row][col]]
        if len(values) != len(set(values)):
            violations.append({"type": "column_duplicate", "column": col + 1})
    for row in range(size):
        for col in range(size):
            if mask[row][col] and ((row + 1 < size and mask[row + 1][col]) or (col + 1 < size and mask[row][col + 1])):
                violations.append({"type": "adjacent_shaded_cells", "cell": f"r{row + 1}c{col + 1}"})
    white = {(row, col) for row in range(size) for col in range(size) if not mask[row][col]}
    if white:
        frontier = [white.pop()]
        while frontier:
            row, col = frontier.pop()
            for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if neighbor in white:
                    white.remove(neighbor); frontier.append(neighbor)
    if white:
        violations.append({"type": "disconnected_unshaded_area"})
    return violations


def render_hitori(size: int, grid: list[list[int]], mask: list[list[int]] | None = None) -> str:
    lines = [f"Hitori {size}x{size}", "Grid (# = shaded when a solution mask is shown):"]
    for row in range(size):
        lines.append(" ".join("#" if mask and mask[row][col] else str(grid[row][col]) for col in range(size)))
    lines.append("Rules: unshaded values cannot repeat in a row or column; shaded cells cannot touch; unshaded cells stay connected.")
    return "\n".join(lines)


def _mask_string(mask: list[list[int]]) -> str:
    return "".join("#" if value else "." for row in mask for value in row)


def _mask_from_string(value: str, size: int) -> list[list[int]]:
    return [[1 if item == "#" else 0 for item in value[offset:offset + size]] for offset in range(0, size * size, size)]


class HitoriPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "hitori_puzzle_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "hitori_puzzle_usage.json")
        self.base_bank = _build_base_puzzle_bank()
        self._ensure_bank()
        self.usage_stats = json.loads(self.usage_path.read_text(encoding="utf-8")) if self.usage_path.exists() else {}

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        choices = [puzzle for puzzle in self.base_bank if puzzle.difficulty == scenario.difficulty] or self.base_bank
        parent = min(choices, key=lambda puzzle: (self.usage_stats.get(puzzle.puzzle_id, 0), puzzle.num_clues))
        size, grid = parse_puzzle(parent.puzzle)
        if scenario.edge_case == "malformed_input":
            malformed = ("Hitori: 1 2 # invalid grid [", "Hitori rows: [1, 2], [missing]", "Hitori size: ?; grid: 1|2|x")[sample_index % 3]
            return self._make_variant(parent, f"malformed_{sample_index}", rendered_board=malformed)
        if scenario.edge_case == "invalid_grid":
            row, column = sample_index % size, (sample_index // size) % size
            altered = deepcopy(grid); altered[row][column] = 0
            return self._make_variant(parent, f"invalid_grid_{row}_{column}", grid=altered)
        if scenario.edge_case == "unsolvable_puzzle":
            value = 1 + sample_index % size
            return self._make_variant(parent, f"unsolvable_{value}", grid=[[value] * size for _ in range(size)])
        if scenario.edge_case == "ambiguous_puzzle":
            return self._make_variant(parent, "ambiguous", grid=_ambiguous_grid(size))
        operations = ("identity", "reflect_horizontal", "reflect_vertical", "rotate_90", "rotate_180", "rotate_270", "transpose", "anti_transpose")
        operation = operations[sample_index % len(operations)]
        transformed = deepcopy(grid)
        if operation == "reflect_horizontal": transformed = list(reversed(transformed))
        elif operation == "reflect_vertical": transformed = [list(reversed(row)) for row in transformed]
        elif operation == "rotate_90": transformed = [list(row) for row in zip(*transformed[::-1])]
        elif operation == "rotate_180": transformed = [list(reversed(row)) for row in reversed(transformed)]
        elif operation == "rotate_270": transformed = [list(row) for row in zip(*transformed)][::-1]
        elif operation == "transpose": transformed = [list(row) for row in zip(*transformed)]
        elif operation == "anti_transpose": transformed = [list(row) for row in zip(*[list(reversed(row)) for row in reversed(transformed)])]
        return self._make_variant(parent, operation, grid=transformed)

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        for key in {puzzle.puzzle_id, puzzle.parent_puzzle_id or puzzle.puzzle_id}:
            self.usage_stats[key] = self.usage_stats.get(key, 0) + 1
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _make_variant(self, parent: PuzzleRecord, transformation: str, *, grid: list[list[int]] | None = None, rendered_board: str | None = None) -> PuzzleRecord:
        size, parent_grid = parse_puzzle(parent.puzzle)
        grid = grid or parent_grid
        structural = validate_structure(size, grid)
        solutions = solve_hitori(size, grid, 2) if not structural else []
        selected_mask = solutions[0] if solutions else _mask_from_string(parent.solution, size)
        validity = "malformed" if transformation.startswith("malformed") else "invalid" if structural else "valid"
        solvability = "unknown" if validity != "valid" else "unique" if len(solutions) == 1 else "ambiguous" if len(solutions) > 1 else "unsolvable"
        puzzle = canonical_puzzle(grid)
        variant_id = hashlib.sha1(f"{parent.puzzle_id}|{transformation}|{puzzle}".encode()).hexdigest()[:16]
        edge_kind = next((kind for kind in ("malformed", "invalid_grid", "unsolvable", "ambiguous") if transformation.startswith(kind)), None)
        return PuzzleRecord(puzzle_id=variant_id, puzzle=puzzle, solution=_mask_string(selected_mask), difficulty=parent.difficulty, num_clues=size * size, required_strategies=list(parent.required_strategies), unique_solution_status=solvability == "unique", source=parent.source, canonical_signature=parent.canonical_signature, usage_count=self.usage_stats.get(variant_id, 0), parent_puzzle_id=parent.puzzle_id, transformation=transformation, rendered_board=rendered_board or render_hitori(size, grid), ground_truth=_build_ground_truth(size, grid, solutions, selected_mask, validity, solvability, structural), metadata={"size": size, "grid": grid, **({"edge_case_kind": edge_kind} if edge_kind else {})})

    def _ensure_bank(self) -> None:
        if not self.bank_path.exists():
            self.bank_path.parent.mkdir(parents=True, exist_ok=True)
            self.bank_path.write_text("".join(json.dumps(puzzle.to_dict(), ensure_ascii=False) + "\n" for puzzle in self.base_bank), encoding="utf-8")


def _build_ground_truth(size: int, grid: list[list[int]], solutions: list[list[list[int]]], mask: list[list[int]], validity: str, solvability: str, structural: list[dict[str, Any]]) -> dict[str, Any]:
    forced_shaded, forced_unshaded = [], []
    if solutions:
        for row in range(size):
            for col in range(size):
                states = {solution[row][col] for solution in solutions}
                cell = f"r{row + 1}c{col + 1}"
                if states == {1}: forced_shaded.append(cell)
                elif states == {0}: forced_unshaded.append(cell)
    suggested = {"cell": forced_shaded[0], "state": "shaded"} if forced_shaded else None
    return {"solution": _mask_string(mask), "solution_mask": mask, "solved_board": render_hitori(size, grid, mask), "validity_status": validity, "solvability_status": solvability, "unique_solution_status": solvability == "unique", "solution_count": len(solutions), "structural_violations": structural, "solution_violations": solution_violations(size, grid, mask), "forced_shaded": forced_shaded, "forced_unshaded": forced_unshaded, "suggested_move": suggested}


def _random_grid(size: int, rng: random.Random) -> list[list[int]]:
    # Latin-like grids with controlled repeats are solvable quickly and remain recognizably Hitori.
    grid = [[(row + col) % size + 1 for col in range(size)] for row in range(size)]
    for row in range(size):
        for col in range(size):
            if rng.random() < 0.33:
                grid[row][col] = rng.randint(1, size)
    return grid


def _build_base_puzzle_bank() -> list[PuzzleRecord]:
    specifications = [("easy", 4, ["duplicate_pair_elimination"]), ("medium", 5, ["line_reduction", "connectivity_check"]), ("hard", 6, ["connectivity_check", "contradiction_avoidance"]), ("expert", 7, ["multi_line_interaction", "connectivity_forcing"])]
    bank: list[PuzzleRecord] = []
    for offset, (difficulty, size, strategies) in enumerate(specifications):
        rng = random.Random(2048 + offset)
        for _ in range(2000):
            grid = _random_grid(size, rng)
            solutions = solve_hitori(size, grid, 2)
            if len(solutions) == 1 and any(value for row in solutions[0] for value in row):
                mask = solutions[0]
                puzzle = PuzzleRecord(puzzle_id=f"hitori_{difficulty}_1", puzzle=canonical_puzzle(grid), solution=_mask_string(mask), difficulty=difficulty, num_clues=size * size, required_strategies=strategies, unique_solution_status=True, source="builtin", canonical_signature=f"hitori_{difficulty}_1", usage_count=0, rendered_board=render_hitori(size, grid), metadata={"size": size, "grid": grid})
                puzzle.ground_truth = _build_ground_truth(size, grid, solutions, mask, "valid", "unique", [])
                bank.append(puzzle); break
        else:
            raise RuntimeError(f"Unable to build a unique {difficulty} Hitori puzzle.")
    return bank


def _ambiguous_grid(size: int) -> list[list[int]]:
    grid = [[(row + col) % size + 1 for col in range(size)] for row in range(size)]
    for row in range(size):
        for col in range(size):
            original = grid[row][col]
            for value in range(1, size + 1):
                if value == original: continue
                candidate = deepcopy(grid); candidate[row][col] = value
                if len(solve_hitori(size, candidate, 2)) == 2:
                    return candidate
    raise RuntimeError("Unable to construct an ambiguous Hitori puzzle.")
