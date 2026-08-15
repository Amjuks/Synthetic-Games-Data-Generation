from __future__ import annotations

import hashlib
import json
import math
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


Cell = tuple[int, int]


def neighbors(row: int, col: int, height: int, width: int) -> list[Cell]:
    return [(r, c) for r in range(max(0, row-1), min(height, row+2)) for c in range(max(0, col-1), min(width, col+2)) if (r, c) != (row, col)]


def format_cell(cell: Cell) -> str: return f"r{cell[0] + 1}c{cell[1] + 1}"


def parse_cell(value: str) -> Cell:
    row, col = value[1:].split("c"); return int(row) - 1, int(col) - 1


def canonical_state(width: int, height: int, total_mines: int, visible: list[str]) -> str:
    return json.dumps({"width": width, "height": height, "total_mines": total_mines, "visible": visible}, sort_keys=True, separators=(",", ":"))


def parse_state(value: str) -> tuple[int, int, int, list[str]]:
    payload = json.loads(value)
    return int(payload["width"]), int(payload["height"]), int(payload["total_mines"]), [str(row) for row in payload["visible"]]


def mine_mask(width: int, height: int, mines: set[Cell]) -> str:
    return "".join("*" if (r, c) in mines else "." for r in range(height) for c in range(width))


def mines_from_mask(mask: str, width: int, height: int) -> set[Cell]:
    return {(i // width, i % width) for i, value in enumerate(mask[:width*height]) if value == "*"}


def clue_at(cell: Cell, mines: set[Cell], height: int, width: int) -> int:
    return sum(neighbor in mines for neighbor in neighbors(*cell, height, width))


def validate_visible(width: int, height: int, total_mines: int, visible: list[str]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if width <= 0 or height <= 0: errors.append({"type": "dimensions", "width": width, "height": height})
    if len(visible) != height or any(len(row) != width for row in visible): errors.append({"type": "visible_shape", "expected": [height, width]})
    illegal = sorted({char for row in visible for char in row} - set("#F*012345678"))
    if illegal: errors.append({"type": "visible_symbols", "symbols": illegal})
    if not 0 <= total_mines < width * height: errors.append({"type": "mine_count", "value": total_mines})
    if sum(row.count("F") for row in visible) > total_mines: errors.append({"type": "too_many_flags"})
    return errors


def reveal_safe(width: int, height: int, mines: set[Cell], start: Cell, visible: list[list[str]]) -> None:
    stack = [start]; seen = set()
    while stack:
        cell = stack.pop()
        if cell in seen or cell in mines: continue
        seen.add(cell); clue = clue_at(cell, mines, height, width); visible[cell[0]][cell[1]] = str(clue)
        if clue == 0: stack.extend(neighbors(*cell, height, width))


def constraint_analysis(width: int, height: int, total_mines: int, visible: list[str]) -> dict[str, Any]:
    structural = validate_visible(width, height, total_mines, visible)
    if structural: return {"valid": False, "violations": structural, "solution_count": 0, "probabilities": {}, "constraints": [], "components": []}
    hidden = {(r, c) for r in range(height) for c in range(width) if visible[r][c] == "#"}
    flags = {(r, c) for r in range(height) for c in range(width) if visible[r][c] == "F"}
    exploded = {(r, c) for r in range(height) for c in range(width) if visible[r][c] == "*"}
    known_mines = flags | exploded
    constraints: list[tuple[set[Cell], int, Cell]] = []; violations = []
    for r in range(height):
        for c in range(width):
            if visible[r][c].isdigit():
                adjacent = neighbors(r, c, height, width); variables = {cell for cell in adjacent if cell in hidden}
                required = int(visible[r][c]) - sum(cell in known_mines for cell in adjacent)
                if required < 0 or required > len(variables): violations.append({"type": "clue_contradiction", "cell": format_cell((r, c)), "required_hidden_mines": required, "hidden_neighbors": len(variables)})
                elif variables or required: constraints.append((variables, required, (r, c)))
    remaining_total = total_mines - len(known_mines)
    if remaining_total < 0 or remaining_total > len(hidden): violations.append({"type": "global_mine_contradiction", "remaining_mines": remaining_total, "hidden_cells": len(hidden)})
    if violations: return {"valid": False, "violations": violations, "solution_count": 0, "probabilities": {}, "constraints": _render_constraints(constraints), "components": []}
    frontier = set().union(*(cells for cells, _, _ in constraints)) if constraints else set()
    off_frontier = hidden - frontier
    components = _constraint_components(frontier, constraints)
    enumerated = [_enumerate_component(cells, [item for item in constraints if item[0] & cells]) for cells in components]
    if any(not item["ways"] for item in enumerated): return {"valid": False, "violations": [{"type": "impossible_constraints"}], "solution_count": 0, "probabilities": {}, "constraints": _render_constraints(constraints), "components": [sorted(map(format_cell, cells)) for cells in components]}

    distributions = [item["ways"] for item in enumerated]
    all_frontier = {0: 1}
    for distribution in distributions: all_frontier = _convolve(all_frontier, distribution)
    total = sum(ways * _comb(len(off_frontier), remaining_total - used) for used, ways in all_frontier.items())
    if total == 0: return {"valid": False, "violations": [{"type": "global_total_incompatible"}], "solution_count": 0, "probabilities": {}, "constraints": _render_constraints(constraints), "components": [sorted(map(format_cell, cells)) for cells in components]}

    numerators: dict[Cell, int] = {}
    for index, component in enumerate(enumerated):
        outside = {0: 1}
        for other_index, distribution in enumerate(distributions):
            if other_index != index: outside = _convolve(outside, distribution)
        for cell, by_mines in component["mine_ways"].items():
            numerator = 0
            for own_mines, mine_assignments in by_mines.items():
                for other_mines, other_ways in outside.items():
                    numerator += mine_assignments * other_ways * _comb(len(off_frontier), remaining_total - own_mines - other_mines)
            numerators[cell] = numerator
    for cell in off_frontier:
        numerators[cell] = sum(ways * _comb(len(off_frontier)-1, remaining_total-used-1) for used, ways in all_frontier.items())
    probabilities = {format_cell(cell): round(numerators[cell] / total, 8) for cell in sorted(hidden)}
    forced_safe = sorted(format_cell(cell) for cell in hidden if numerators[cell] == 0)
    forced_mines = sorted(format_cell(cell) for cell in hidden if numerators[cell] == total)
    return {"valid": True, "violations": [], "solution_count": total, "probabilities": probabilities, "forced_safe": forced_safe, "forced_mines": forced_mines, "constraints": _render_constraints(constraints), "components": [sorted(map(format_cell, cells)) for cells in components], "frontier_cells": sorted(map(format_cell, frontier)), "off_frontier_count": len(off_frontier), "remaining_mines": remaining_total}


def _render_constraints(constraints: list[tuple[set[Cell], int, Cell]]) -> list[dict[str, Any]]:
    return [{"clue_cell": format_cell(source), "cells": sorted(map(format_cell, cells)), "required_mines": required} for cells, required, source in constraints]


def _constraint_components(frontier: set[Cell], constraints: list[tuple[set[Cell], int, Cell]]) -> list[set[Cell]]:
    remaining = set(frontier); components = []
    while remaining:
        component = {remaining.pop()}; changed = True
        while changed:
            changed = False
            for cells, _, _ in constraints:
                if cells & component and not cells <= component:
                    additions = cells - component; component.update(additions); remaining.difference_update(additions); changed = True
        components.append(component)
    return components


def _enumerate_component(cells: set[Cell], constraints: list[tuple[set[Cell], int, Cell]]) -> dict[str, Any]:
    ordered = sorted(cells); ways: dict[int, int] = {}; mine_ways = {cell: {} for cell in ordered}; assignment: dict[Cell, int] = {}
    def possible() -> bool:
        for variables, required, _ in constraints:
            assigned = sum(assignment.get(cell, 0) for cell in variables); unknown = sum(cell not in assignment for cell in variables)
            if assigned > required or assigned + unknown < required: return False
        return True
    def search(index: int) -> None:
        if index == len(ordered):
            if not possible(): return
            count = sum(assignment.values()); ways[count] = ways.get(count, 0) + 1
            for cell, value in assignment.items():
                if value: mine_ways[cell][count] = mine_ways[cell].get(count, 0) + 1
            return
        cell = ordered[index]
        for value in (0, 1):
            assignment[cell] = value
            if possible(): search(index + 1)
        del assignment[cell]
    search(0)
    return {"ways": ways, "mine_ways": mine_ways}


def _convolve(left: dict[int, int], right: dict[int, int]) -> dict[int, int]:
    result: dict[int, int] = {}
    for a, ways_a in left.items():
        for b, ways_b in right.items(): result[a+b] = result.get(a+b, 0) + ways_a * ways_b
    return result


def _comb(n: int, k: int) -> int: return math.comb(n, k) if 0 <= k <= n else 0


def render_board(width: int, height: int, mines: int, visible: list[str]) -> str:
    lines = ["    " + " ".join(str(i+1) for i in range(width))]
    lines.extend(f"{r+1:>3} " + " ".join(visible[r]) for r in range(height))
    lines.append(f"Mines: {mines} | Flags: {sum(row.count('F') for row in visible)}")
    return "\n".join(lines)


def build_ground_truth(width: int, height: int, total_mines: int, visible: list[str], mines: set[Cell], *, mutation: dict[str, Any] | None = None) -> dict[str, Any]:
    analysis = constraint_analysis(width, height, total_mines, visible)
    clue_mismatches = []
    for r in range(height):
        for c in range(width):
            if visible[r][c].isdigit() and int(visible[r][c]) != clue_at((r, c), mines, height, width): clue_mismatches.append({"cell": format_cell((r, c)), "shown": int(visible[r][c]), "actual": clue_at((r, c), mines, height, width)})
            if visible[r][c] == "*" and (r, c) not in mines: clue_mismatches.append({"cell": format_cell((r, c)), "shown": "exploded_mine", "actual": "safe"})
    if clue_mismatches: analysis = {**analysis, "valid": False, "violations": [*analysis.get("violations", []), {"type": "clue_mismatch", "items": clue_mismatches}]}
    suggested = ({"cell": analysis["forced_safe"][0], "action": "reveal", "reason": "safe_in_every_consistent_layout"} if analysis.get("forced_safe") else {"cell": analysis["forced_mines"][0], "action": "flag", "reason": "mine_in_every_consistent_layout"} if analysis.get("forced_mines") else ({"cell": min(analysis.get("probabilities", {}), key=lambda cell: (analysis["probabilities"][cell], cell)), "action": "reveal", "reason": "lowest_exact_mine_probability"} if analysis.get("probabilities") else None))
    return {"validity_status": "valid" if analysis.get("valid") else "invalid", "structural_violations": analysis.get("violations", []), "solution": mine_mask(width, height, mines), "mine_mask": mine_mask(width, height, mines), "solution_count": analysis.get("solution_count", 0), "unique_solution_status": analysis.get("solution_count") == 1, "probabilities": analysis.get("probabilities", {}), "forced_safe": analysis.get("forced_safe", []), "forced_mines": analysis.get("forced_mines", []), "constraints": analysis.get("constraints", []), "components": analysis.get("components", []), "frontier_cells": analysis.get("frontier_cells", []), "off_frontier_count": analysis.get("off_frontier_count", 0), "suggested_move": suggested, "mutation": mutation or {}, "loss": any("*" in row for row in visible)}


class MinesweeperPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output = Path(config["output_path"]); puzzles = config.get("puzzles", {})
        self.bank_path = output / puzzles.get("persistent_bank_filename", "minesweeper_board_bank.jsonl"); self.usage_path = output / puzzles.get("usage_stats_filename", "minesweeper_board_usage.json")
        self.usage_stats: dict[str, int] = {}; self.base_bank = self._build_bank(); self._ensure_bank(); self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        choices = [p for p in self.base_bank if p.difficulty == scenario.difficulty] or self.base_bank; parent = choices[sample_index % len(choices)]
        if scenario.edge_case != "none": return self._edge(parent, scenario.edge_case, sample_index)
        if sample_index % 2 == 0: return deepcopy(parent)
        width, height, count, visible = parse_state(parent.puzzle); mines = mines_from_mask(parent.solution, width, height)
        reflected = list(reversed(visible)); reflected_mines = {(height - 1 - row, col) for row, col in mines}
        return self._record(width, height, count, reflected, reflected_mines, parent.difficulty, parent.puzzle_id, "reflect_horizontal")

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        self.usage_stats[puzzle.puzzle_id] = self.usage_stats.get(puzzle.puzzle_id, 0) + 1
        parent = puzzle.parent_puzzle_id or puzzle.puzzle_id
        self.usage_stats[parent] = self.usage_stats.get(parent, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _ensure_bank(self) -> None:
        if self.bank_path.exists(): return
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        self.bank_path.write_text("".join(json.dumps(item.to_dict(), ensure_ascii=False) + "\n" for item in self.base_bank), encoding="utf-8")

    def _load_usage_stats(self) -> dict[str, int]:
        return json.loads(self.usage_path.read_text(encoding="utf-8")) if self.usage_path.exists() else {}

    def _build_bank(self) -> list[PuzzleRecord]:
        specs = {"easy": (5, 5, 4), "medium": (8, 8, 10), "hard": (9, 9, 15), "expert": (12, 12, 25)}; bank = []
        for difficulty, (width, height, count) in specs.items():
            for offset in range(2):
                rng = random.Random(12000 + width * 101 + offset); safe = (height // 2, width // 2)
                candidates = [(r, c) for r in range(height) for c in range(width) if (r, c) != safe and (r, c) not in neighbors(*safe, height, width)]
                mines = set(rng.sample(candidates, count)); visible = [["#"] * width for _ in range(height)]; reveal_safe(width, height, mines, safe, visible)
                # Reveal a few additional safe boundary cells to create varied, bounded frontiers.
                hidden_safe = [(r, c) for r in range(height) for c in range(width) if visible[r][c] == "#" and (r, c) not in mines]
                for cell in rng.sample(hidden_safe, min(offset + 1, len(hidden_safe))): visible[cell[0]][cell[1]] = str(clue_at(cell, mines, height, width))
                bank.append(self._record(width, height, count, ["".join(row) for row in visible], mines, difficulty, None, "identity"))
        return bank

    def _record(self, width: int, height: int, count: int, visible: list[str], mines: set[Cell], difficulty: str, parent: str | None, transformation: str, mutation: dict[str, Any] | None = None) -> PuzzleRecord:
        puzzle = canonical_state(width, height, count, visible); truth = build_ground_truth(width, height, count, visible, mines, mutation=mutation); digest = hashlib.sha1(f"{puzzle}|{truth['mine_mask']}|{transformation}".encode()).hexdigest()[:16]
        return PuzzleRecord(f"minesweeper_{digest}", puzzle, truth["mine_mask"], difficulty, sum(char.isdigit() for row in visible for char in row), ["single_cell_deduction", "frontier_constraints", "probability_analysis"], truth["unique_solution_status"], "generated", hashlib.sha256(puzzle.encode()).hexdigest(), self.usage_stats.get(f"minesweeper_{digest}", 0), parent_puzzle_id=parent, transformation=transformation, rendered_board=render_board(width, height, count, visible), ground_truth=truth, metadata={"width": width, "height": height, "size": height, "mine_count": count, "revealed_count": sum(char.isdigit() for row in visible for char in row), "frontier_size": len(truth.get("frontier_cells", []))})

    def _edge(self, parent: PuzzleRecord, edge: str, sample_index: int) -> PuzzleRecord:
        width, height, count, visible = parse_state(parent.puzzle); mines = mines_from_mask(parent.solution, width, height); rows = [list(row) for row in visible]
        if edge == "malformed_input":
            record = self._record(width, height, count, visible, mines, parent.difficulty, parent.puzzle_id, f"malformed_{sample_index}", {"type": edge}); record.rendered_board = "\n".join(record.rendered_board.splitlines()[:2]); record.ground_truth["validity_status"] = "malformed"; return record
        hidden = [(r, c) for r in range(height) for c in range(width) if rows[r][c] == "#"]
        if edge == "loss_state":
            mine = sorted(mines)[0]; rows[mine[0]][mine[1]] = "*"
        elif edge == "contradictory_flags":
            for cell in hidden[:min(count + 1, len(hidden))]: rows[cell[0]][cell[1]] = "F"
        elif edge in {"invalid_clues", "impossible_state"}:
            revealed = next(((r, c) for r in range(height) for c in range(width) if rows[r][c].isdigit()), (0, 0)); rows[revealed[0]][revealed[1]] = "8"
        elif edge == "no_forced_move":
            # An unrevealed board has one unconstrained component: every hidden
            # cell has probability total_mines / area, so no action is forced.
            rows = [["#"] * width for _ in range(height)]
        return self._record(width, height, count, ["".join(row) for row in rows], mines, parent.difficulty, parent.puzzle_id, f"{edge}_{sample_index}", {"type": edge})
