from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


Coord = tuple[int, int]


def canonical_puzzle(size: int, clues: list[list[int]]) -> str:
    return json.dumps({"size": size, "clues": clues}, separators=(",", ":"), sort_keys=True)


def parse_puzzle(puzzle: str) -> tuple[int, list[list[int]]]:
    payload = json.loads(puzzle)
    return int(payload["size"]), list(payload["clues"])


def validate_structure(size: int, clues: list[list[int]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not 4 <= size <= 8:
        violations.append({"type": "grid_size", "size": size})
    if len(clues) != size or any(not isinstance(row, list) or len(row) != size for row in clues):
        return violations + [{"type": "grid_shape", "size": size}]
    for row, values in enumerate(clues):
        for col, value in enumerate(values):
            if not isinstance(value, int) or value < 0:
                violations.append({"type": "clue_value", "cell": _cell(row, col), "value": value})
    if not any(value for row in clues for value in row):
        violations.append({"type": "missing_clue"})
    return violations


def solve_nurikabe(size: int, clues: list[list[int]], limit: int = 2) -> list[list[list[int]]]:
    """Return sea masks (1 = sea, 0 = island) up to ``limit`` solutions.

    The intentionally small built-in boards keep this exhaustive, deterministic
    solver practical while providing real rule-backed ground truth.
    """
    if validate_structure(size, clues):
        return []
    clue_cells = {(r, c) for r in range(size) for c in range(size) if clues[r][c]}
    cells = [(r, c) for r in range(size) for c in range(size) if (r, c) not in clue_cells]
    mask = [[-1] * size for _ in range(size)]
    for row, col in clue_cells:
        mask[row][col] = 0
    solutions: list[list[list[int]]] = []

    def has_sea_square(row: int, col: int) -> bool:
        for top in (row - 1, row):
            for left in (col - 1, col):
                if 0 <= top < size - 1 and 0 <= left < size - 1:
                    if all(mask[r][c] == 1 for r in (top, top + 1) for c in (left, left + 1)):
                        return True
        return False

    def land_component_ok() -> bool:
        seen: set[Coord] = set()
        for start in [(r, c) for r in range(size) for c in range(size) if mask[r][c] == 0]:
            if start in seen:
                continue
            component, frontier = set(), [start]
            while frontier:
                cell = frontier.pop()
                if cell in component:
                    continue
                component.add(cell); seen.add(cell)
                frontier.extend(n for n in _neighbors(*cell, size) if mask[n[0]][n[1]] == 0 and n not in component)
            component_clues = [clues[r][c] for r, c in component if clues[r][c]]
            if len(component_clues) > 1 or (component_clues and len(component) > component_clues[0]):
                return False
        return True

    def complete_valid() -> bool:
        if any(mask[r][c] == -1 for r in range(size) for c in range(size)):
            return False
        islands: list[set[Coord]] = []
        seen: set[Coord] = set()
        for start in [(r, c) for r in range(size) for c in range(size) if mask[r][c] == 0]:
            if start in seen:
                continue
            island, frontier = set(), [start]
            while frontier:
                cell = frontier.pop()
                if cell in island: continue
                island.add(cell); seen.add(cell)
                frontier.extend(n for n in _neighbors(*cell, size) if mask[n[0]][n[1]] == 0 and n not in island)
            island_clues = [clues[r][c] for r, c in island if clues[r][c]]
            if len(island_clues) != 1 or len(island) != island_clues[0]: return False
            islands.append(island)
        sea = {(r, c) for r in range(size) for c in range(size) if mask[r][c] == 1}
        if not sea: return False
        reached, frontier = {next(iter(sea))}, [next(iter(sea))]
        while frontier:
            cell = frontier.pop()
            for neighbor in _neighbors(*cell, size):
                if neighbor in sea and neighbor not in reached:
                    reached.add(neighbor); frontier.append(neighbor)
        return reached == sea

    def search(index: int) -> None:
        if len(solutions) >= limit: return
        if index == len(cells):
            if complete_valid(): solutions.append([row[:] for row in mask])
            return
        row, col = cells[index]
        # Try sea first; typical Nurikabe boards have a dominant sea.
        for value in (1, 0):
            mask[row][col] = value
            if not has_sea_square(row, col) and land_component_ok(): search(index + 1)
        mask[row][col] = -1

    search(0)
    return solutions


def solution_violations(size: int, clues: list[list[int]], mask: list[list[int]]) -> list[dict[str, Any]]:
    if len(mask) != size or any(len(row) != size for row in mask): return [{"type": "solution_shape"}]
    solutions = solve_nurikabe(size, clues, limit=1000)
    return [] if mask in solutions else [{"type": "invalid_nurikabe_solution"}]


def render_nurikabe(size: int, clues: list[list[int]], mask: list[list[int]] | None = None) -> str:
    lines = [f"Nurikabe {size}x{size}", "Grid (. = undecided; # = sea when a solution is shown):"]
    for row in range(size):
        lines.append(" ".join(str(clues[row][col]) if clues[row][col] else "#" if mask and mask[row][col] else "." for col in range(size)))
    lines.append("Rules: each island contains one clue and has that size; islands do not touch orthogonally; the sea is connected and has no 2x2 block.")
    return "\n".join(lines)


class NurikabePuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config; puzzle_config = config.get("puzzles", {}); output_path = Path(config["output_path"])
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "nurikabe_puzzle_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "nurikabe_puzzle_usage.json")
        self.base_bank = _build_base_puzzle_bank(); self._ensure_bank()
        self.usage_stats = json.loads(self.usage_path.read_text(encoding="utf-8")) if self.usage_path.exists() else {}

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        choices = [p for p in self.base_bank if p.difficulty == scenario.difficulty] or self.base_bank
        parent = min(choices, key=lambda p: (self.usage_stats.get(p.puzzle_id, 0), p.num_clues))
        size, clues = parse_puzzle(parent.puzzle)
        if scenario.edge_case == "malformed_input": return self._variant(parent, "malformed", rendered_board="Nurikabe: [broken grid")
        if scenario.edge_case == "invalid_grid":
            altered = deepcopy(clues); altered[0][0] = -1; return self._variant(parent, "invalid_grid", clues=altered)
        if scenario.edge_case == "unsolvable_puzzle":
            altered = deepcopy(clues); altered[0][0] = size * size; return self._variant(parent, "unsolvable", clues=altered)
        if scenario.edge_case == "ambiguous_puzzle":
            # A verified two-solution 4x4 clue pattern; used only for the
            # explicit ambiguity edge case.
            altered = [[0, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 4, 0]]
            return self._variant(parent, "ambiguous", clues=altered)
        operations = ("identity", "reflect_horizontal", "reflect_vertical", "rotate_90", "rotate_180", "rotate_270", "transpose", "anti_transpose")
        operation = operations[sample_index % len(operations)]
        transformed = deepcopy(clues)
        if operation == "reflect_horizontal": transformed = list(reversed(transformed))
        elif operation == "reflect_vertical": transformed = [list(reversed(row)) for row in transformed]
        elif operation == "rotate_90": transformed = [list(row) for row in zip(*transformed[::-1])]
        elif operation == "rotate_180": transformed = [list(reversed(row)) for row in reversed(transformed)]
        elif operation == "rotate_270": transformed = [list(row) for row in zip(*transformed)][::-1]
        elif operation == "transpose": transformed = [list(row) for row in zip(*transformed)]
        elif operation == "anti_transpose": transformed = [list(row) for row in zip(*[list(reversed(row)) for row in reversed(transformed)])]
        return self._variant(parent, operation, clues=transformed)

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        for key in {puzzle.puzzle_id, puzzle.parent_puzzle_id or puzzle.puzzle_id}: self.usage_stats[key] = self.usage_stats.get(key, 0) + 1
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _variant(self, parent: PuzzleRecord, transformation: str, *, clues: list[list[int]] | None = None, rendered_board: str | None = None) -> PuzzleRecord:
        size, base_clues = parse_puzzle(parent.puzzle); clues = base_clues if clues is None else clues
        structural = validate_structure(size, clues); solutions = solve_nurikabe(size, clues, 2) if not structural else []
        mask = solutions[0] if solutions else [[0] * size for _ in range(size)]
        validity = "malformed" if transformation == "malformed" else "invalid" if structural else "valid"
        solvability = "unknown" if validity != "valid" else "unique" if len(solutions) == 1 else "ambiguous" if len(solutions) > 1 else "unsolvable"
        puzzle = canonical_puzzle(size, clues); variant_id = hashlib.sha1(f"{parent.puzzle_id}|{transformation}|{puzzle}".encode()).hexdigest()[:16]
        return PuzzleRecord(puzzle_id=variant_id, puzzle=puzzle, solution=_mask_string(mask), difficulty=parent.difficulty, num_clues=sum(bool(v) for row in clues for v in row), required_strategies=list(parent.required_strategies), unique_solution_status=solvability == "unique", source=parent.source, canonical_signature=parent.canonical_signature, usage_count=self.usage_stats.get(variant_id, 0), parent_puzzle_id=parent.puzzle_id, transformation=transformation, rendered_board=rendered_board or render_nurikabe(size, clues), ground_truth=_ground_truth(size, clues, solutions, mask, validity, solvability, structural), metadata={"size": size, "clues": clues, **({"edge_case_kind": transformation} if transformation in {"malformed", "invalid_grid", "unsolvable", "ambiguous"} else {})})

    def _ensure_bank(self) -> None:
        if not self.bank_path.exists():
            self.bank_path.parent.mkdir(parents=True, exist_ok=True)
            self.bank_path.write_text("".join(json.dumps(p.to_dict()) + "\n" for p in self.base_bank), encoding="utf-8")


def _build_base_puzzle_bank() -> list[PuzzleRecord]:
    # These compact boards are deliberately chosen for quick exhaustive verification.
    rows = [
        ("easy", [[0,1,0,0],[0,0,0,0],[0,0,0,2],[0,3,0,0]], ["sea_connectivity", "island_completion"]),
        ("medium", [[0,2,0,0],[0,0,0,0],[0,0,0,0],[1,0,4,0]], ["island_separation", "no_2x2_sea"]),
        ("hard", [[1,0,1,0],[0,0,0,0],[0,0,0,3],[0,0,0,0]], ["island_expansion", "sea_connectivity"]),
        ("expert", [[2,0,0,0],[0,0,0,0],[1,0,3,0],[0,0,0,0]], ["contradiction", "no_2x2_sea"]),
    ]
    bank: list[PuzzleRecord] = []
    for index, (difficulty, clues, strategies) in enumerate(rows, 1):
        size = len(clues); solutions = solve_nurikabe(size, clues, 2)
        if len(solutions) != 1: raise RuntimeError(f"Built-in Nurikabe puzzle {difficulty} is not uniquely solvable.")
        puzzle = canonical_puzzle(size, clues); mask = solutions[0]; puzzle_id = f"nurikabe_{difficulty}_{index}"
        bank.append(PuzzleRecord(puzzle_id=puzzle_id, puzzle=puzzle, solution=_mask_string(mask), difficulty=difficulty, num_clues=sum(bool(v) for row in clues for v in row), required_strategies=strategies, unique_solution_status=True, source="builtin", canonical_signature=puzzle_id, usage_count=0, rendered_board=render_nurikabe(size, clues), ground_truth=_ground_truth(size, clues, solutions, mask, "valid", "unique", []), metadata={"size": size, "clues": clues}))
    return bank


def _ground_truth(size: int, clues: list[list[int]], solutions: list[list[list[int]]], mask: list[list[int]], validity: str, solvability: str, structural: list[dict[str, Any]]) -> dict[str, Any]:
    forced_sea, forced_island = [], []
    for row in range(size):
        for col in range(size):
            states = {solution[row][col] for solution in solutions}
            if states == {1}: forced_sea.append(_cell(row, col))
            elif states == {0}: forced_island.append(_cell(row, col))
    suggested = {"cell": forced_sea[0], "state": "sea"} if forced_sea else None
    return {"solution": _mask_string(mask), "solution_mask": mask, "solved_board": render_nurikabe(size, clues, mask), "validity_status": validity, "solvability_status": solvability, "unique_solution_status": solvability == "unique", "solution_count": len(solutions), "structural_violations": structural, "solution_violations": solution_violations(size, clues, mask) if validity == "valid" and solvability == "unique" else [], "forced_sea": forced_sea, "forced_island": forced_island, "suggested_move": suggested}


def _neighbors(row: int, col: int, size: int) -> list[Coord]: return [(r, c) for r, c in ((row-1,col),(row+1,col),(row,col-1),(row,col+1)) if 0 <= r < size and 0 <= c < size]
def _cell(row: int, col: int) -> str: return f"r{row + 1}c{col + 1}"
def _mask_string(mask: list[list[int]]) -> str: return "".join("#" if cell else "." for row in mask for cell in row)
