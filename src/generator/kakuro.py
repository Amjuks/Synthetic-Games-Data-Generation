from __future__ import annotations

import hashlib
import itertools
import json
import random
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


Cell = tuple[int, int]


TEMPLATES: dict[str, tuple[str, ...]] = {
    "easy": (
        "#####",
        "#..##",
        "#..##",
        "#####",
        "#####",
    ),
    "medium": (
        "#######",
        "#...#..",
        "#...#..",
        "#######",
        "#...#..",
        "#...#..",
        "#######",
    ),
    "hard": (
        "#########",
        "#...#...#",
        "#...#...#",
        "#########",
        "#...#...#",
        "#...#...#",
        "#########",
        "#...#...#",
        "#...#...#",
    ),
    "expert": (
        "#########",
        "#....#...",
        "#....#...",
        "#########",
        "#....#...",
        "#....#...",
        "#########",
        "#....#...",
        "#....#...",
    ),
}


def format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def parse_cell(cell: str) -> Cell:
    row_text, col_text = cell.lower().split("c", 1)
    return int(row_text[1:]) - 1, int(col_text) - 1


def canonical_puzzle(height: int, width: int, blocks: list[str], runs: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"height": height, "width": width, "blocks": sorted(blocks), "runs": runs},
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_puzzle(puzzle: str) -> tuple[int, int, list[str], list[dict[str, Any]]]:
    payload = json.loads(puzzle)
    return int(payload["height"]), int(payload["width"]), list(payload["blocks"]), list(payload["runs"])


@lru_cache(maxsize=None)
def run_permutations(length: int, target: int) -> tuple[tuple[int, ...], ...]:
    if length < 2 or length > 9 or target < 1:
        return ()
    return tuple(
        permutation
        for combination in run_combinations(length, target)
        for permutation in itertools.permutations(combination)
    )


@lru_cache(maxsize=None)
def run_combinations(length: int, target: int) -> tuple[tuple[int, ...], ...]:
    return tuple(values for values in itertools.combinations(range(1, 10), length) if sum(values) == target)


def runs_from_template(template: tuple[str, ...]) -> tuple[list[str], list[dict[str, Any]]]:
    height, width = len(template), len(template[0])
    blocks = [format_cell(row, col) for row in range(height) for col in range(width) if template[row][col] == "#"]
    runs: list[dict[str, Any]] = []
    across_index = 1
    down_index = 1
    for row in range(height):
        col = 0
        while col < width:
            if template[row][col] == "." and (col == 0 or template[row][col - 1] == "#"):
                cells: list[str] = []
                cursor = col
                while cursor < width and template[row][cursor] == ".":
                    cells.append(format_cell(row, cursor))
                    cursor += 1
                runs.append({
                    "id": f"A{across_index}",
                    "direction": "across",
                    "clue_cell": format_cell(row, col - 1),
                    "target": 0,
                    "cells": cells,
                })
                across_index += 1
                col = cursor
            else:
                col += 1
    for col in range(width):
        row = 0
        while row < height:
            if template[row][col] == "." and (row == 0 or template[row - 1][col] == "#"):
                cells = []
                cursor = row
                while cursor < height and template[cursor][col] == ".":
                    cells.append(format_cell(cursor, col))
                    cursor += 1
                runs.append({
                    "id": f"D{down_index}",
                    "direction": "down",
                    "clue_cell": format_cell(row - 1, col),
                    "target": 0,
                    "cells": cells,
                })
                down_index += 1
                row = cursor
            else:
                row += 1
    return blocks, runs


def validate_structure(height: int, width: int, blocks: list[str], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if height < 3 or width < 3:
        violations.append({"type": "dimensions", "message": "Kakuro grids must be at least 3x3."})
    all_cells = {format_cell(row, col) for row in range(height) for col in range(width)}
    block_set = set(blocks)
    if len(block_set) != len(blocks):
        violations.append({"type": "duplicate_blocks"})
    invalid_blocks = sorted(block_set - all_cells)
    if invalid_blocks:
        violations.append({"type": "block_bounds", "cells": invalid_blocks})

    memberships: dict[str, dict[str, list[str]]] = {}
    seen_ids: set[str] = set()
    for index, run in enumerate(runs):
        run_id = str(run.get("id", f"run_{index + 1}"))
        direction = run.get("direction")
        cells = list(run.get("cells", []))
        if run_id in seen_ids:
            violations.append({"type": "duplicate_run_id", "run_id": run_id})
        seen_ids.add(run_id)
        if direction not in {"across", "down"}:
            violations.append({"type": "direction", "run_id": run_id, "direction": direction})
            continue
        if not 2 <= len(cells) <= 9:
            violations.append({"type": "run_length", "run_id": run_id, "length": len(cells)})
        if not isinstance(run.get("target"), int) or int(run.get("target", 0)) < 1:
            violations.append({"type": "target", "run_id": run_id, "target": run.get("target")})
        try:
            parsed = [parse_cell(cell) for cell in cells]
            clue = parse_cell(str(run.get("clue_cell")))
        except (TypeError, ValueError):
            violations.append({"type": "cell_format", "run_id": run_id})
            continue
        if any(format_cell(row, col) not in all_cells for row, col in parsed) or format_cell(*clue) not in all_cells:
            violations.append({"type": "cell_bounds", "run_id": run_id})
            continue
        if len(set(cells)) != len(cells):
            violations.append({"type": "duplicate_run_cell", "run_id": run_id})
        if any(cell in block_set for cell in cells):
            violations.append({"type": "play_cell_is_block", "run_id": run_id})
        if format_cell(*clue) not in block_set:
            violations.append({"type": "clue_not_block", "run_id": run_id, "clue_cell": run.get("clue_cell")})
        if parsed:
            first_row, first_col = parsed[0]
            expected_clue = (first_row, first_col - 1) if direction == "across" else (first_row - 1, first_col)
            expected_cells = (
                [(first_row, first_col + offset) for offset in range(len(parsed))]
                if direction == "across"
                else [(first_row + offset, first_col) for offset in range(len(parsed))]
            )
            if clue != expected_clue:
                violations.append({"type": "clue_position", "run_id": run_id})
            if parsed != expected_cells:
                violations.append({"type": "noncontiguous_run", "run_id": run_id})
        for cell in cells:
            memberships.setdefault(cell, {"across": [], "down": []})[direction].append(run_id)

    play_cells = set(memberships)
    unclassified = sorted(all_cells - block_set - play_cells)
    if unclassified:
        violations.append({"type": "unclassified_cells", "cells": unclassified})
    for cell, directions in memberships.items():
        if len(directions["across"]) != 1 or len(directions["down"]) != 1:
            violations.append({"type": "run_membership", "cell": cell, **directions})
    return violations


def solve_kakuro(
    height: int,
    width: int,
    blocks: list[str],
    runs: list[dict[str, Any]],
    limit: int = 2,
) -> list[dict[str, int]]:
    if validate_structure(height, width, blocks, runs):
        return []
    prepared = [
        (list(run["cells"]), run_permutations(len(run["cells"]), int(run["target"])))
        for run in runs
    ]
    if any(not candidates for _, candidates in prepared):
        return []
    assignments: dict[str, int] = {}
    solutions: list[dict[str, int]] = []

    def viable(cells: list[str], values: tuple[int, ...]) -> bool:
        return all(cell not in assignments or assignments[cell] == value for cell, value in zip(cells, values))

    def search(remaining: list[int]) -> None:
        if len(solutions) >= limit:
            return
        if not remaining:
            solutions.append(dict(assignments))
            return
        choices: list[tuple[int, int, list[tuple[int, ...]]]] = []
        for run_index in remaining:
            cells, candidates = prepared[run_index]
            filtered = [values for values in candidates if viable(cells, values)]
            if not filtered:
                return
            choices.append((len(filtered), run_index, filtered))
        _, run_index, candidates = min(choices, key=lambda item: (item[0], item[1]))
        cells, _ = prepared[run_index]
        next_remaining = [index for index in remaining if index != run_index]
        for values in candidates:
            newly_assigned: list[str] = []
            for cell, value in zip(cells, values):
                if cell not in assignments:
                    assignments[cell] = value
                    newly_assigned.append(cell)
            search(next_remaining)
            for cell in newly_assigned:
                del assignments[cell]
            if len(solutions) >= limit:
                return

    search(list(range(len(prepared))))
    return solutions


def render_kakuro(
    height: int,
    width: int,
    blocks: list[str],
    runs: list[dict[str, Any]],
    solution: str | None = None,
) -> str:
    block_set = set(blocks)
    values = solution_to_values(solution, height, width) if solution else {}
    clues: dict[str, dict[str, int]] = {}
    for run in runs:
        clues.setdefault(run["clue_cell"], {})[run["direction"]] = int(run["target"])
    lines = [f"Kakuro {height}x{width}", "Grid (down\\across):"]
    for row in range(height):
        tokens: list[str] = []
        for col in range(width):
            cell = format_cell(row, col)
            if cell in clues:
                clue = clues[cell]
                tokens.append(f"{clue.get('down', '')}\\{clue.get('across', '')}".center(7))
            elif cell in block_set:
                tokens.append("#######")
            else:
                tokens.append(str(values.get(cell, ".")).center(7))
        lines.append("|".join(tokens))
    lines.append("Runs:")
    for run in runs:
        marker = "A" if run["direction"] == "across" else "D"
        lines.append(f"{run['id']} ({marker}) {run['target']}: {', '.join(run['cells'])}")
    return "\n".join(lines)


def solution_to_values(solution: str | None, height: int, width: int) -> dict[str, int]:
    if not solution or len(solution) != height * width:
        return {}
    return {
        format_cell(row, col): int(solution[row * width + col])
        for row in range(height)
        for col in range(width)
        if solution[row * width + col] != "#"
    }


def values_to_solution(height: int, width: int, blocks: list[str], values: dict[str, int]) -> str:
    block_set = set(blocks)
    return "".join(
        "#" if format_cell(row, col) in block_set else str(values[format_cell(row, col)])
        for row in range(height)
        for col in range(width)
    )


def build_ground_truth(
    height: int,
    width: int,
    blocks: list[str],
    runs: list[dict[str, Any]],
    solution: str,
    *,
    edge_case_kind: str | None = None,
) -> dict[str, Any]:
    violations = validate_structure(height, width, blocks, runs)
    solutions = solve_kakuro(height, width, blocks, runs, limit=2) if not violations else []
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

    solution_values = solution_to_values(solution, height, width)
    combination_map: dict[str, list[list[int]]] = {}
    run_evaluations: list[dict[str, Any]] = []
    cell_candidates: dict[str, set[int]] = {}
    for run in runs:
        combinations = run_combinations(len(run["cells"]), int(run["target"])) if not violations else ()
        combination_map[run["id"]] = [list(values) for values in combinations]
        permutations = run_permutations(len(run["cells"]), int(run["target"])) if not violations else ()
        for offset, cell in enumerate(run["cells"]):
            candidates = {values[offset] for values in permutations}
            cell_candidates[cell] = cell_candidates.get(cell, candidates) & candidates
        solved_values = [solution_values.get(cell) for cell in run["cells"]]
        run_evaluations.append({
            "run_id": run["id"],
            "values": solved_values,
            "sum": sum(value for value in solved_values if value is not None),
            "satisfied": None not in solved_values
            and len(set(solved_values)) == len(solved_values)
            and sum(solved_values) == int(run["target"]),
        })
    compact_candidates = {cell: sorted(values) for cell, values in cell_candidates.items()}
    suggested_move = None
    forced = [(cell, values[0]) for cell, values in compact_candidates.items() if len(values) == 1]
    if forced:
        cell, value = sorted(forced)[0]
        suggested_move = {"cell": cell, "value": value, "reason": "forced_by_crossing_runs"}
    elif runs:
        run = min(runs, key=lambda item: (len(combination_map.get(item["id"], [])) or 10**9, item["id"]))
        suggested_move = {
            "run_id": run["id"],
            "candidate_combinations": combination_map.get(run["id"], []),
            "reason": "most_constrained_run",
        }
    return {
        "height": height,
        "width": width,
        "blocks": blocks,
        "runs": runs,
        "solution": solution,
        "solution_grid": [solution[index:index + width] for index in range(0, height * width, width)],
        "solved_board": render_kakuro(height, width, blocks, runs, solution),
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": len(solutions) == 1,
        "solution_count": len(solutions),
        "structural_violations": violations,
        "run_evaluations": run_evaluations,
        "run_candidate_combinations": combination_map,
        "cell_candidates": compact_candidates,
        "suggested_move": suggested_move,
    }


class KakuroPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "kakuro_puzzle_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "kakuro_puzzle_usage.json")
        self.seed = int(config.get("generation", {}).get("random_seed", 17))
        self.base_bank = self._build_base_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        candidates = [puzzle for puzzle in self.base_bank if puzzle.difficulty == scenario.difficulty] or self.base_bank
        parent = min(candidates, key=lambda puzzle: (self.usage_stats.get(puzzle.puzzle_id, 0), puzzle.puzzle_id))
        edge_builders = {
            "malformed_input": self._malformed_variant,
            "invalid_clues": self._invalid_variant,
            "unsolvable_puzzle": self._unsolvable_variant,
            "ambiguous_puzzle": self._ambiguous_variant,
        }
        if scenario.edge_case in edge_builders:
            return edge_builders[scenario.edge_case](parent, sample_index)
        operation = ["identity", "transpose", "digit_relabel"][sample_index % 3]
        return self._transformed_variant(parent, operation, sample_index)

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        self.usage_stats[puzzle.puzzle_id] = self.usage_stats.get(puzzle.puzzle_id, 0) + 1
        parent_id = puzzle.parent_puzzle_id or puzzle.puzzle_id
        self.usage_stats[parent_id] = self.usage_stats.get(parent_id, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _build_base_bank(self) -> list[PuzzleRecord]:
        return [self._generate_base(difficulty, offset) for offset, difficulty in enumerate(("easy", "medium", "hard", "expert"))]

    def _generate_base(self, difficulty: str, offset: int) -> PuzzleRecord:
        template = TEMPLATES[difficulty]
        height, width = len(template), len(template[0])
        blocks, bare_runs = runs_from_template(template)
        if validate_structure(height, width, blocks, [{**run, "target": 1} for run in bare_runs]):
            raise RuntimeError(f"Invalid built-in Kakuro template for {difficulty}.")
        rng = random.Random(self.seed + offset * 1009)
        for _ in range(500):
            values = _generate_fill(
                height,
                width,
                blocks,
                bare_runs,
                rng,
                max_run_combinations={"easy": 1, "medium": 2, "hard": 3, "expert": 4}[difficulty],
            )
            if not values:
                continue
            runs = _derive_targets(bare_runs, values)
            if len(solve_kakuro(height, width, blocks, runs, limit=2)) == 1:
                puzzle_id = f"kakuro_{difficulty}_{height}x{width}_{offset + 1}"
                return self._record(
                    puzzle_id=puzzle_id,
                    height=height,
                    width=width,
                    blocks=blocks,
                    runs=runs,
                    solution=values_to_solution(height, width, blocks, values),
                    difficulty=difficulty,
                    parent_id=None,
                    transformation="identity",
                    source="generated",
                    canonical_signature=puzzle_id,
                )
        raise RuntimeError(f"Unable to generate a unique {difficulty} Kakuro puzzle after 500 attempts.")

    def _transformed_variant(self, parent: PuzzleRecord, operation: str, sample_index: int) -> PuzzleRecord:
        height, width, blocks, runs = parse_puzzle(parent.puzzle)
        values = solution_to_values(parent.solution, height, width)
        if operation == "transpose":
            transformed_blocks = sorted(format_cell(*reversed(parse_cell(cell))) for cell in blocks)
            transformed_runs = []
            for run in runs:
                transformed_runs.append({
                    **run,
                    "id": ("D" if run["direction"] == "across" else "A") + run["id"][1:],
                    "direction": "down" if run["direction"] == "across" else "across",
                    "clue_cell": format_cell(*reversed(parse_cell(run["clue_cell"]))),
                    "cells": [format_cell(*reversed(parse_cell(cell))) for cell in run["cells"]],
                })
            transformed_values = {format_cell(*reversed(parse_cell(cell))): value for cell, value in values.items()}
            height, width, blocks, runs, values = width, height, transformed_blocks, transformed_runs, transformed_values
        elif operation == "digit_relabel":
            rng = random.Random(self.seed + sample_index * 313)
            original_values = dict(values)
            original_runs = deepcopy(runs)
            selected: tuple[dict[str, int], list[dict[str, Any]]] | None = None
            for _ in range(20):
                mapping = dict(zip(range(1, 10), rng.sample(range(1, 10), 9)))
                candidate_values = {cell: mapping[value] for cell, value in original_values.items()}
                candidate_runs = _derive_targets(original_runs, candidate_values)
                if candidate_values != original_values and len(solve_kakuro(height, width, blocks, candidate_runs, limit=2)) == 1:
                    selected = candidate_values, candidate_runs
                    break
            if selected is None:
                candidate_values = {cell: 10 - value for cell, value in original_values.items()}
                candidate_runs = _derive_targets(original_runs, candidate_values)
                if len(solve_kakuro(height, width, blocks, candidate_runs, limit=2)) == 1:
                    selected = candidate_values, candidate_runs
            if selected is None:
                operation = "identity"
                values, runs = original_values, original_runs
            else:
                values, runs = selected
        solution = values_to_solution(height, width, blocks, values)
        return self._record(
            puzzle_id=self._variant_id(parent, operation, runs),
            height=height,
            width=width,
            blocks=blocks,
            runs=runs,
            solution=solution,
            difficulty=parent.difficulty,
            parent_id=parent.puzzle_id,
            transformation=operation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
        )

    def _malformed_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        variant = self._transformed_variant(parent, "identity", sample_index)
        variant.puzzle_id = self._variant_id(parent, f"malformed_{sample_index}", [])
        variant.transformation = f"malformed_{sample_index}"
        variant.rendered_board = "\n".join(variant.rendered_board.splitlines()[:3])
        height, width, blocks, runs = parse_puzzle(variant.puzzle)
        variant.ground_truth = build_ground_truth(height, width, blocks, runs, variant.solution, edge_case_kind="malformed_input")
        variant.metadata["edge_case_kind"] = "malformed_input"
        return variant

    def _invalid_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        height, width, blocks, runs = parse_puzzle(parent.puzzle)
        runs = deepcopy(runs)
        runs[0]["cells"].append(runs[0]["cells"][0])
        return self._record_edge(parent, height, width, blocks, runs, parent.solution, f"invalid_{sample_index}", "invalid_clues")

    def _unsolvable_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        height, width, blocks, runs = parse_puzzle(parent.puzzle)
        runs = deepcopy(runs)
        runs[0]["target"] = 100
        return self._record_edge(parent, height, width, blocks, runs, parent.solution, f"unsolvable_{sample_index}", "unsolvable_puzzle")

    def _ambiguous_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        height, width, _, _ = parse_puzzle(parent.puzzle)
        play = {format_cell(row, col): value for row, values in ((1, (1, 2)), (2, (2, 1))) for col, value in enumerate(values, start=1)}
        blocks = [format_cell(row, col) for row in range(height) for col in range(width) if format_cell(row, col) not in play]
        runs = [
            {"id": "A1", "direction": "across", "clue_cell": "r2c1", "target": 3, "cells": ["r2c2", "r2c3"]},
            {"id": "A2", "direction": "across", "clue_cell": "r3c1", "target": 3, "cells": ["r3c2", "r3c3"]},
            {"id": "D1", "direction": "down", "clue_cell": "r1c2", "target": 3, "cells": ["r2c2", "r3c2"]},
            {"id": "D2", "direction": "down", "clue_cell": "r1c3", "target": 3, "cells": ["r2c3", "r3c3"]},
        ]
        solution = values_to_solution(height, width, blocks, play)
        return self._record_edge(parent, height, width, blocks, runs, solution, f"ambiguous_{sample_index}", "ambiguous_puzzle")

    def _record_edge(
        self,
        parent: PuzzleRecord,
        height: int,
        width: int,
        blocks: list[str],
        runs: list[dict[str, Any]],
        solution: str,
        transformation: str,
        kind: str,
    ) -> PuzzleRecord:
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, runs),
            height=height,
            width=width,
            blocks=blocks,
            runs=runs,
            solution=solution,
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
        height: int,
        width: int,
        blocks: list[str],
        runs: list[dict[str, Any]],
        solution: str,
        difficulty: str,
        parent_id: str | None,
        transformation: str,
        source: str,
        canonical_signature: str,
        edge_case_kind: str | None = None,
    ) -> PuzzleRecord:
        truth = build_ground_truth(height, width, blocks, runs, solution, edge_case_kind=edge_case_kind)
        metadata = {"height": height, "width": width, "blocks": blocks, "runs": runs}
        if edge_case_kind:
            metadata["edge_case_kind"] = edge_case_kind
        return PuzzleRecord(
            puzzle_id=puzzle_id,
            puzzle=canonical_puzzle(height, width, blocks, runs),
            solution=solution,
            difficulty=difficulty,
            num_clues=len(runs),
            required_strategies=["distinct_digit_sums", "cross_run_elimination"],
            unique_solution_status=truth["unique_solution_status"],
            source=source,
            canonical_signature=canonical_signature,
            usage_count=0,
            parent_puzzle_id=parent_id,
            transformation=transformation,
            rendered_board=render_kakuro(height, width, blocks, runs),
            ground_truth=truth,
            metadata=metadata,
        )

    def _variant_id(self, parent: PuzzleRecord, transformation: str, runs: list[dict[str, Any]]) -> str:
        return hashlib.sha1(f"{parent.puzzle_id}|{transformation}|{json.dumps(runs, sort_keys=True)}".encode()).hexdigest()[:16]

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


def _generate_fill(
    height: int,
    width: int,
    blocks: list[str],
    runs: list[dict[str, Any]],
    rng: random.Random,
    max_run_combinations: int,
) -> dict[str, int]:
    del blocks
    cell_runs: dict[str, list[str]] = {}
    for run in runs:
        for cell in run["cells"]:
            cell_runs.setdefault(cell, []).append(run["id"])
    run_cells = {run["id"]: run["cells"] for run in runs}
    play_cells = {parse_cell(cell) for cell in cell_runs}
    components: list[set[Cell]] = []
    remaining = set(play_cells)
    while remaining:
        component = {remaining.pop()}
        frontier = list(component)
        while frontier:
            row, col = frontier.pop()
            for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    frontier.append(neighbor)
        components.append(component)
    if all(len({row for row, _ in component}) == 2 for component in components):
        base_patterns = {
            2: ((1, 2), (2, 9)),
            3: ((4, 7, 1), (8, 9, 5)),
            4: ((8, 2, 1, 5), (9, 6, 5, 7)),
        }
        generated: dict[str, int] = {}
        for component in sorted(components, key=lambda cells: min(cells)):
            rows = sorted({row for row, _ in component})
            cols = sorted({col for _, col in component})
            if len(component) != len(rows) * len(cols) or len(cols) not in base_patterns:
                break
            pattern = base_patterns[len(cols)]
            if rng.choice((False, True)):
                pattern = tuple(reversed(pattern))
            if rng.choice((False, True)):
                pattern = tuple(tuple(reversed(row)) for row in pattern)
            if rng.choice((False, True)):
                pattern = tuple(tuple(10 - value for value in row) for row in pattern)
            for row_offset, row in enumerate(rows):
                for col_offset, col in enumerate(cols):
                    generated[format_cell(row, col)] = pattern[row_offset][col_offset]
        else:
            return generated

    del height, width
    assignments: dict[str, int] = {}

    def candidates(cell: str) -> list[int]:
        used = {
            assignments[other]
            for run_id in cell_runs[cell]
            for other in run_cells[run_id]
            if other in assignments
        }
        values = [value for value in range(1, 10) if value not in used]
        rng.shuffle(values)
        return values

    def search() -> bool:
        if len(assignments) == len(cell_runs):
            return True
        choices = [(len(candidates(cell)), cell) for cell in cell_runs if cell not in assignments]
        _, cell = min(choices, key=lambda item: (item[0], item[1]))
        for value in candidates(cell):
            assignments[cell] = value
            completed_runs_are_compact = True
            for run_id in cell_runs[cell]:
                cells = run_cells[run_id]
                if all(other in assignments for other in cells):
                    target = sum(assignments[other] for other in cells)
                    if len(run_combinations(len(cells), target)) > max_run_combinations:
                        completed_runs_are_compact = False
                        break
            if completed_runs_are_compact and search():
                return True
            del assignments[cell]
        return False

    return assignments if search() else {}


def _derive_targets(runs: list[dict[str, Any]], values: dict[str, int]) -> list[dict[str, Any]]:
    return [{**run, "target": sum(values[cell] for cell in run["cells"])} for run in runs]
