from __future__ import annotations

import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .models import PuzzleRecord, Scenario


Cell = tuple[int, int]


# Version-controlled region layouts. Each letter is one orthogonally connected
# region. The layouts were solver-checked and each has exactly one 1-star solution.
BASE_LAYOUTS: dict[str, tuple[str, ...]] = {
    "easy": (
        "CCABB",
        "ECCBB",
        "ECCCB",
        "EEEDD",
        "EEEDD",
    ),
    "medium": (
        "AAAAAC",
        "DBBAAC",
        "DAAAAC",
        "DDDDEC",
        "DDDDEE",
        "FDDDEE",
    ),
    "hard": (
        "CCCCAAA",
        "CCCCBBA",
        "CCCEBBB",
        "FFFEBDB",
        "FFFEEEE",
        "FFGGGEE",
        "FFGGGEE",
    ),
    "expert": (
        "AAAECBB",
        "DEEECBB",
        "DEEECCC",
        "DEEECCC",
        "DEEEECC",
        "DFFEECC",
        "DFFEEGG",
    ),
}


def format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def parse_cell(cell: str) -> Cell:
    row_text, col_text = cell.lower().split("c", 1)
    return int(row_text[1:]) - 1, int(col_text) - 1


def canonical_puzzle(size: int, stars_per_unit: int, regions: list[dict[str, Any]]) -> str:
    normalized = [
        {"id": str(region["id"]), "cells": sorted(region["cells"], key=parse_cell)}
        for region in sorted(regions, key=lambda item: str(item["id"]))
    ]
    return json.dumps(
        {"size": size, "stars_per_unit": stars_per_unit, "regions": normalized},
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_puzzle(puzzle: str) -> tuple[int, int, list[dict[str, Any]]]:
    payload = json.loads(puzzle)
    return int(payload["size"]), int(payload["stars_per_unit"]), list(payload["regions"])


def regions_from_layout(layout: tuple[str, ...]) -> list[dict[str, Any]]:
    labels = sorted({label for row in layout for label in row})
    return [
        {
            "id": f"R{index + 1}",
            "cells": [
                format_cell(row, col)
                for row in range(len(layout))
                for col in range(len(layout))
                if layout[row][col] == label
            ],
        }
        for index, label in enumerate(labels)
    ]


def validate_structure(size: int, stars_per_unit: int, regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if size < 4 or size > 15:
        violations.append({"type": "size", "message": "Grid size must be between 4 and 15."})
    if not isinstance(stars_per_unit, int) or stars_per_unit < 1:
        violations.append({"type": "stars_per_unit", "value": stars_per_unit})
    elif stars_per_unit > (size + 1) // 2:
        violations.append({"type": "stars_per_unit_feasibility", "value": stars_per_unit})
    if len(regions) != size:
        violations.append({"type": "region_count", "expected": size, "actual": len(regions)})

    seen_ids: set[str] = set()
    seen_cells: dict[str, str] = {}
    for index, region in enumerate(regions):
        region_id = str(region.get("id", f"region_{index + 1}"))
        if region_id in seen_ids:
            violations.append({"type": "duplicate_region_id", "region_id": region_id})
        seen_ids.add(region_id)
        cells = region.get("cells", [])
        if not isinstance(cells, list) or not cells:
            violations.append({"type": "empty_region", "region_id": region_id})
            continue
        parsed: list[Cell] = []
        local_seen: set[str] = set()
        for raw_cell in cells:
            try:
                row, col = parse_cell(str(raw_cell))
            except (TypeError, ValueError):
                violations.append({"type": "cell_format", "region_id": region_id, "cell": raw_cell})
                continue
            cell = format_cell(row, col)
            if not (0 <= row < size and 0 <= col < size):
                violations.append({"type": "cell_bounds", "region_id": region_id, "cell": cell})
                continue
            if cell in local_seen:
                violations.append({"type": "duplicate_cell", "region_id": region_id, "cell": cell})
            local_seen.add(cell)
            if cell in seen_cells:
                violations.append(
                    {"type": "overlap", "cell": cell, "regions": [seen_cells[cell], region_id]}
                )
            else:
                seen_cells[cell] = region_id
            parsed.append((row, col))
        if parsed and not _is_connected(parsed):
            violations.append({"type": "disconnected_region", "region_id": region_id})

    expected = {format_cell(row, col) for row in range(size) for col in range(size)}
    missing = sorted(expected - set(seen_cells), key=parse_cell)
    if missing:
        violations.append({"type": "missing_cells", "cells": missing})
    return violations


def _is_connected(cells: list[Cell]) -> bool:
    remaining = set(cells)
    if not remaining:
        return False
    frontier = [remaining.pop()]
    while frontier:
        row, col = frontier.pop()
        for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if neighbor in remaining:
                remaining.remove(neighbor)
                frontier.append(neighbor)
    return not remaining


def solve_starbattle(
    size: int,
    stars_per_unit: int,
    regions: list[dict[str, Any]],
    limit: int = 2,
) -> list[list[list[int]]]:
    if validate_structure(size, stars_per_unit, regions):
        return []
    region_index = {
        parse_cell(cell): index
        for index, region in enumerate(regions)
        for cell in region["cells"]
    }
    row_patterns = [
        sum(1 << col for col in columns)
        for columns in itertools.combinations(range(size), stars_per_unit)
        if all(right - left > 1 for left, right in zip(columns, columns[1:]))
    ]
    rows: list[int] = []
    column_counts = [0] * size
    region_counts = [0] * len(regions)
    solutions: list[list[list[int]]] = []

    def search(row: int) -> None:
        if len(solutions) >= limit:
            return
        if row == size:
            if all(count == stars_per_unit for count in column_counts) and all(
                count == stars_per_unit for count in region_counts
            ):
                solutions.append(
                    [[1 if mask & (1 << col) else 0 for col in range(size)] for mask in rows]
                )
            return

        remaining_rows = size - row - 1
        previous = rows[-1] if rows else 0
        forbidden_by_previous = previous | (previous << 1) | (previous >> 1)
        for pattern in row_patterns:
            if pattern & forbidden_by_previous:
                continue
            columns = [col for col in range(size) if pattern & (1 << col)]
            region_ids = [region_index[(row, col)] for col in columns]
            if any(column_counts[col] >= stars_per_unit for col in columns):
                continue
            if any(region_counts[region_id] >= stars_per_unit for region_id in region_ids):
                continue
            if len(region_ids) != len(set(region_ids)) and stars_per_unit == 1:
                continue

            rows.append(pattern)
            for col in columns:
                column_counts[col] += 1
            for region_id in region_ids:
                region_counts[region_id] += 1

            columns_possible = all(
                count <= stars_per_unit and count + remaining_rows >= stars_per_unit
                for count in column_counts
            )
            regions_possible = all(count <= stars_per_unit for count in region_counts)
            if columns_possible and regions_possible:
                search(row + 1)

            for region_id in region_ids:
                region_counts[region_id] -= 1
            for col in columns:
                column_counts[col] -= 1
            rows.pop()
            if len(solutions) >= limit:
                return

    search(0)
    return solutions


def render_starbattle(
    size: int,
    stars_per_unit: int,
    regions: list[dict[str, Any]],
    grid: list[list[int]] | None = None,
) -> str:
    grid = grid or [[0] * size for _ in range(size)]
    cell_regions = {
        cell: str(region["id"])
        for region in regions
        for cell in region.get("cells", [])
    }
    lines = [
        f"Star Battle {size}x{size} ({stars_per_unit} star{'s' if stars_per_unit != 1 else ''} per row, column, and region)",
        "Grid:",
    ]
    for row in range(size):
        lines.append(" ".join("*" if grid[row][col] else "." for col in range(size)))
    lines.append("Region layout:")
    for row in range(size):
        lines.append(" ".join(cell_regions.get(format_cell(row, col), "?") for col in range(size)))
    lines.append("Rule: stars may not touch, including diagonally.")
    return "\n".join(lines)


def solution_violations(
    size: int,
    stars_per_unit: int,
    regions: list[dict[str, Any]],
    solution_grid: list[list[int]],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if len(solution_grid) != size or any(len(row) != size for row in solution_grid):
        return [{"type": "solution_shape"}]
    for row in range(size):
        count = sum(solution_grid[row])
        if count != stars_per_unit:
            violations.append({"type": "row_star_count", "row": row + 1, "count": count})
    for col in range(size):
        count = sum(solution_grid[row][col] for row in range(size))
        if count != stars_per_unit:
            violations.append({"type": "column_star_count", "column": col + 1, "count": count})
    for region in regions:
        count = sum(solution_grid[row][col] for row, col in map(parse_cell, region.get("cells", [])))
        if count != stars_per_unit:
            violations.append({"type": "region_star_count", "region_id": region.get("id"), "count": count})
    stars = [
        (row, col)
        for row in range(size)
        for col in range(size)
        if solution_grid[row][col]
    ]
    for index, (row, col) in enumerate(stars):
        for other_row, other_col in stars[index + 1:]:
            if max(abs(row - other_row), abs(col - other_col)) <= 1:
                violations.append(
                    {
                        "type": "touching_stars",
                        "cells": [format_cell(row, col), format_cell(other_row, other_col)],
                    }
                )
    return violations


def build_ground_truth(
    size: int,
    stars_per_unit: int,
    regions: list[dict[str, Any]],
    solution: str,
    *,
    edge_case_kind: str | None = None,
) -> dict[str, Any]:
    structural = validate_structure(size, stars_per_unit, regions)
    solutions = solve_starbattle(size, stars_per_unit, regions, limit=2) if not structural else []
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

    solution_grid = _string_to_grid(solution, size)
    evaluations = [
        {
            "region_id": region["id"],
            "star_count": sum(solution_grid[row][col] for row, col in map(parse_cell, region["cells"])),
            "required": stars_per_unit,
        }
        for region in regions
    ]
    forced_stars: list[str] = []
    forced_empty: list[str] = []
    # A two-solution cutoff proves uniqueness only when exactly one solution is
    # returned. Do not label overlap between two observed ambiguous solutions as
    # globally forced.
    if len(solutions) == 1:
        for row in range(size):
            for col in range(size):
                if solutions[0][row][col]:
                    forced_stars.append(format_cell(row, col))
                else:
                    forced_empty.append(format_cell(row, col))
    suggested_move = None
    if forced_stars:
        suggested_move = {
            "cell": forced_stars[0],
            "value": "star",
            "reason": "star_in_every_solver_solution",
        }
    elif forced_empty:
        suggested_move = {
            "cell": forced_empty[0],
            "value": "empty",
            "reason": "empty_in_every_observed_solution",
        }

    return {
        "size": size,
        "stars_per_unit": stars_per_unit,
        "regions": regions,
        "solution": solution,
        "solution_grid": solution_grid,
        "solved_board": render_starbattle(size, stars_per_unit, regions, solution_grid),
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": len(solutions) == 1,
        "solution_count": len(solutions),
        "structural_violations": structural,
        "solution_violations": solution_violations(size, stars_per_unit, regions, solution_grid),
        "region_evaluations": evaluations,
        "forced_stars": forced_stars,
        "forced_empty": forced_empty,
        "suggested_move": suggested_move,
    }


class StarBattlePuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get(
            "persistent_bank_filename", "starbattle_puzzle_bank.jsonl"
        )
        self.usage_path = output_path / puzzle_config.get(
            "usage_stats_filename", "starbattle_puzzle_usage.json"
        )
        self.base_bank = self._build_base_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        candidates = [puzzle for puzzle in self.base_bank if puzzle.difficulty == scenario.difficulty] or self.base_bank
        parent = min(candidates, key=lambda puzzle: (self.usage_stats.get(puzzle.puzzle_id, 0), puzzle.puzzle_id))
        if scenario.edge_case == "malformed_input":
            return self._malformed_variant(parent, sample_index)
        if scenario.edge_case == "invalid_regions":
            return self._invalid_variant(parent, sample_index)
        if scenario.edge_case == "unsolvable_puzzle":
            return self._unsolvable_variant(parent, sample_index)
        if scenario.edge_case == "ambiguous_puzzle":
            return self._ambiguous_variant(parent, sample_index)
        transformations = [
            "identity",
            "rotate_90",
            "rotate_180",
            "rotate_270",
            "reflect_horizontal",
            "reflect_vertical",
            "transpose",
            "anti_transpose",
        ]
        return self._transformed_variant(parent, transformations[sample_index % len(transformations)])

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        self.usage_stats[puzzle.puzzle_id] = self.usage_stats.get(puzzle.puzzle_id, 0) + 1
        parent_id = puzzle.parent_puzzle_id or puzzle.puzzle_id
        self.usage_stats[parent_id] = self.usage_stats.get(parent_id, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _build_base_bank(self) -> list[PuzzleRecord]:
        records: list[PuzzleRecord] = []
        for difficulty, layout in BASE_LAYOUTS.items():
            size = len(layout)
            regions = regions_from_layout(layout)
            solutions = solve_starbattle(size, 1, regions, limit=2)
            if len(solutions) != 1:
                raise RuntimeError(
                    f"Built-in {difficulty} Star Battle layout is not uniquely solvable."
                )
            puzzle_id = f"starbattle_{difficulty}_{size}"
            records.append(
                self._record(
                    puzzle_id=puzzle_id,
                    size=size,
                    stars_per_unit=1,
                    regions=regions,
                    solution=_grid_to_string(solutions[0]),
                    difficulty=difficulty,
                    parent_id=None,
                    transformation="identity",
                    source="built_in",
                    canonical_signature=puzzle_id,
                )
            )
        return records

    def _transformed_variant(self, parent: PuzzleRecord, operation: str) -> PuzzleRecord:
        size, stars_per_unit, regions = parse_puzzle(parent.puzzle)
        transform = _coordinate_transform(size, operation)
        transformed_regions = deepcopy(regions)
        for region in transformed_regions:
            region["cells"] = sorted(
                [format_cell(*transform(*parse_cell(cell))) for cell in region["cells"]],
                key=parse_cell,
            )
        original_grid = _string_to_grid(parent.solution, size)
        transformed_grid = [[0] * size for _ in range(size)]
        for row in range(size):
            for col in range(size):
                new_row, new_col = transform(row, col)
                transformed_grid[new_row][new_col] = original_grid[row][col]
        return self._record(
            puzzle_id=self._variant_id(parent, operation, transformed_regions),
            size=size,
            stars_per_unit=stars_per_unit,
            regions=transformed_regions,
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
        size, stars_per_unit, regions = parse_puzzle(variant.puzzle)
        variant.ground_truth = build_ground_truth(
            size,
            stars_per_unit,
            regions,
            variant.solution,
            edge_case_kind="malformed_input",
        )
        variant.metadata = {
            "size": size,
            "stars_per_unit": stars_per_unit,
            "regions": regions,
            "edge_case_kind": "malformed_input",
        }
        return variant

    def _invalid_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, stars_per_unit, regions = parse_puzzle(parent.puzzle)
        regions = deepcopy(regions)
        regions[1]["cells"].append(regions[0]["cells"][0])
        return self._record_edge(
            parent, size, stars_per_unit, regions, f"invalid_{sample_index}", "invalid_regions"
        )

    def _unsolvable_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, stars_per_unit, regions = parse_puzzle(parent.puzzle)
        unsolvable = _find_unsolvable_regions(size, stars_per_unit, regions)
        if unsolvable is None:
            raise RuntimeError(f"Unable to derive an unsolvable Star Battle variant from {parent.puzzle_id}.")
        return self._record_edge(
            parent,
            size,
            stars_per_unit,
            unsolvable,
            f"unsolvable_{sample_index}",
            "unsolvable_puzzle",
        )

    def _ambiguous_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        size, stars_per_unit, _ = parse_puzzle(parent.puzzle)
        regions = [
            {
                "id": f"R{row + 1}",
                "cells": [format_cell(row, col) for col in range(size)],
            }
            for row in range(size)
        ]
        return self._record_edge(
            parent,
            size,
            stars_per_unit,
            regions,
            f"ambiguous_{sample_index}",
            "ambiguous_puzzle",
        )

    def _record_edge(
        self,
        parent: PuzzleRecord,
        size: int,
        stars_per_unit: int,
        regions: list[dict[str, Any]],
        transformation: str,
        kind: str,
    ) -> PuzzleRecord:
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, regions),
            size=size,
            stars_per_unit=stars_per_unit,
            regions=regions,
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
        stars_per_unit: int,
        regions: list[dict[str, Any]],
        solution: str,
        difficulty: str,
        parent_id: str | None,
        transformation: str,
        source: str,
        canonical_signature: str,
        edge_case_kind: str | None = None,
    ) -> PuzzleRecord:
        puzzle = canonical_puzzle(size, stars_per_unit, regions)
        truth = build_ground_truth(
            size, stars_per_unit, regions, solution, edge_case_kind=edge_case_kind
        )
        return PuzzleRecord(
            puzzle_id=puzzle_id,
            puzzle=puzzle,
            solution=solution,
            difficulty=difficulty,
            num_clues=len(regions),
            required_strategies=[
                "row_column_region_counts",
                "non_touching_elimination",
                "region_interactions",
            ],
            unique_solution_status=truth["unique_solution_status"],
            source=source,
            canonical_signature=canonical_signature,
            usage_count=0,
            parent_puzzle_id=parent_id,
            transformation=transformation,
            rendered_board=render_starbattle(size, stars_per_unit, regions),
            ground_truth=truth,
            metadata={
                "size": size,
                "stars_per_unit": stars_per_unit,
                "regions": regions,
                **({"edge_case_kind": edge_case_kind} if edge_case_kind else {}),
            },
        )

    def _variant_id(
        self,
        parent: PuzzleRecord,
        transformation: str,
        regions: list[dict[str, Any]],
    ) -> str:
        payload = json.dumps(regions, sort_keys=True)
        return hashlib.sha1(f"{parent.puzzle_id}|{transformation}|{payload}".encode()).hexdigest()[:16]

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


def _find_unsolvable_regions(
    size: int,
    stars_per_unit: int,
    regions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    cell_region = {
        parse_cell(cell): index
        for index, region in enumerate(regions)
        for cell in region["cells"]
    }
    for row in range(size):
        for col in range(size):
            source_index = cell_region[(row, col)]
            for other in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if other not in cell_region:
                    continue
                target_index = cell_region[other]
                if target_index == source_index:
                    continue
                candidate = deepcopy(regions)
                cell = format_cell(row, col)
                candidate[source_index]["cells"].remove(cell)
                candidate[target_index]["cells"].append(cell)
                candidate[target_index]["cells"].sort(key=parse_cell)
                if validate_structure(size, stars_per_unit, candidate):
                    continue
                if not solve_starbattle(size, stars_per_unit, candidate, limit=1):
                    return candidate
    return None


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
    return "".join("*" if value else "." for row in grid for value in row)


def _string_to_grid(solution: str, size: int) -> list[list[int]]:
    values = [1 if value == "*" else 0 for value in solution]
    if len(values) != size * size:
        return [[0] * size for _ in range(size)]
    return [values[index:index + size] for index in range(0, size * size, size)]
