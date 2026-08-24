from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Callable

from .models import PuzzleRecord, Scenario


Mask = list[list[int]]
Grid = list[list[int]]
Cell = tuple[int, int]

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
    "easy": (4, ["direct_subset", "line_completion"]),
    "medium": (5, ["subset_elimination", "cross_line_check"]),
    "hard": (6, ["target_balance", "forced_state_intersection"]),
    "expert": (7, ["multi_line_reasoning", "exact_subset_cover"]),
}


def format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def canonical_puzzle(
    height: int,
    width: int,
    grid: Grid,
    row_targets: list[int],
    column_targets: list[int],
) -> str:
    return json.dumps(
        {
            "height": height,
            "width": width,
            "grid": [list(row) for row in grid],
            "row_targets": list(row_targets),
            "column_targets": list(column_targets),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_puzzle(puzzle: str) -> tuple[int, int, Grid, list[int], list[int]]:
    payload = json.loads(puzzle)
    return (
        int(payload["height"]),
        int(payload["width"]),
        list(payload["grid"]),
        list(payload["row_targets"]),
        list(payload["column_targets"]),
    )


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_structure(
    height: int,
    width: int,
    grid: Grid,
    row_targets: list[int],
    column_targets: list[int],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    dimensions_valid = (
        _is_plain_int(height)
        and _is_plain_int(width)
        and 2 <= height <= 12
        and 2 <= width <= 12
    )
    if not dimensions_valid:
        violations.append({"type": "grid_size", "height": height, "width": width})
    grid_valid = isinstance(grid, list) and dimensions_valid and len(grid) == height
    if not grid_valid or any(
        not isinstance(row, list) or (dimensions_valid and len(row) != width)
        for row in grid if isinstance(grid, list)
    ):
        violations.append({"type": "grid_shape", "height": height, "width": width})
        grid_valid = False
    if isinstance(grid, list):
        for row, values in enumerate(grid):
            if not isinstance(values, list):
                continue
            for col, value in enumerate(values):
                if not _is_plain_int(value) or not 1 <= value <= 9:
                    violations.append(
                        {
                            "type": "cell_value",
                            "cell": format_cell(row, col),
                            "value": value,
                        }
                    )

    if not isinstance(row_targets, list):
        violations.append({"type": "row_targets_type", "actual": type(row_targets).__name__})
        row_targets = []
    if not isinstance(column_targets, list):
        violations.append(
            {"type": "column_targets_type", "actual": type(column_targets).__name__}
        )
        column_targets = []
    if dimensions_valid and len(row_targets) != height:
        violations.append(
            {"type": "row_target_count", "expected": height, "actual": len(row_targets)}
        )
    if dimensions_valid and len(column_targets) != width:
        violations.append(
            {
                "type": "column_target_count",
                "expected": width,
                "actual": len(column_targets),
            }
        )
    for row, target in enumerate(row_targets):
        maximum = (
            sum(grid[row])
            if grid_valid and row < len(grid) and isinstance(grid[row], list)
            else None
        )
        if not _is_plain_int(target) or target < 0:
            violations.append({"type": "row_target", "row": row + 1, "target": target})
        elif maximum is not None and target > maximum:
            violations.append(
                {
                    "type": "row_target_range",
                    "row": row + 1,
                    "target": target,
                    "maximum": maximum,
                }
            )
    for col, target in enumerate(column_targets):
        maximum = (
            sum(grid[row][col] for row in range(height))
            if grid_valid and col < width
            else None
        )
        if not _is_plain_int(target) or target < 0:
            violations.append(
                {"type": "column_target", "column": col + 1, "target": target}
            )
        elif maximum is not None and target > maximum:
            violations.append(
                {
                    "type": "column_target_range",
                    "column": col + 1,
                    "target": target,
                    "maximum": maximum,
                }
            )
    # Do not reject unequal row/column target totals here. Their mismatch is a
    # valid, solver-confirmed unsolvable instance used by the edge-case profile.
    return violations


def _line_subsets(values: list[int], target: int) -> list[list[int]]:
    subsets = []
    for bits in range(1 << len(values)):
        pattern = [1 if bits & (1 << index) else 0 for index in range(len(values))]
        if sum(value * state for value, state in zip(values, pattern)) == target:
            subsets.append(pattern)
    return subsets


def solve_sumplete(
    height: int,
    width: int,
    grid: Grid,
    row_targets: list[int],
    column_targets: list[int],
    limit: int = 2,
) -> list[Mask]:
    """Enumerate row keep-subsets and prune by column sums/capacities."""

    if limit <= 0 or validate_structure(height, width, grid, row_targets, column_targets):
        return []
    row_options = [
        _line_subsets(grid[row], row_targets[row]) for row in range(height)
    ]
    if any(not subsets for subsets in row_options):
        return []

    suffix_minimum = [[0] * width for _ in range(height + 1)]
    suffix_maximum = [[0] * width for _ in range(height + 1)]
    for row in range(height - 1, -1, -1):
        for col in range(width):
            states = {pattern[col] for pattern in row_options[row]}
            value = grid[row][col]
            suffix_minimum[row][col] = suffix_minimum[row + 1][col] + (
                value if states == {1} else 0
            )
            suffix_maximum[row][col] = suffix_maximum[row + 1][col] + (
                value if 1 in states else 0
            )

    selected_rows: list[list[int]] = []
    column_sums = [0] * width
    solutions: list[Mask] = []

    def search(row: int) -> None:
        if len(solutions) >= limit:
            return
        if row == height:
            if column_sums == column_targets:
                solutions.append([values[:] for values in selected_rows])
            return
        for pattern in row_options[row]:
            next_sums = [
                column_sums[col] + grid[row][col] * pattern[col]
                for col in range(width)
            ]
            if any(next_sums[col] > column_targets[col] for col in range(width)):
                continue
            if any(
                next_sums[col] + suffix_minimum[row + 1][col] > column_targets[col]
                or next_sums[col] + suffix_maximum[row + 1][col] < column_targets[col]
                for col in range(width)
            ):
                continue
            selected_rows.append(pattern)
            previous = column_sums[:]
            column_sums[:] = next_sums
            search(row + 1)
            column_sums[:] = previous
            selected_rows.pop()
            if len(solutions) >= limit:
                return

    search(0)
    return solutions


def _coerce_mask(mask: Mask | str, height: int, width: int) -> Mask | None:
    if isinstance(mask, str):
        if len(mask) != height * width or any(cell not in "#." for cell in mask):
            return None
        values = [1 if cell == "#" else 0 for cell in mask]
        return [values[index : index + width] for index in range(0, height * width, width)]
    if not isinstance(mask, list) or len(mask) != height:
        return None
    if any(not isinstance(row, list) or len(row) != width for row in mask):
        return None
    return mask


def solution_violations(
    height: int,
    width: int,
    grid: Grid,
    row_targets: list[int],
    column_targets: list[int],
    mask: Mask | str,
) -> list[dict[str, Any]]:
    parsed = _coerce_mask(mask, height, width)
    if parsed is None:
        return [{"type": "solution_shape"}]
    violations: list[dict[str, Any]] = []
    for row in range(height):
        if any(not _is_plain_int(value) or value not in {0, 1} for value in parsed[row]):
            violations.append({"type": "solution_value", "row": row + 1})
            continue
        actual = sum(grid[row][col] * parsed[row][col] for col in range(width))
        if actual != row_targets[row]:
            violations.append(
                {
                    "type": "row_sum",
                    "row": row + 1,
                    "expected": row_targets[row],
                    "actual": actual,
                }
            )
    for col in range(width):
        if not all(
            _is_plain_int(parsed[row][col]) and parsed[row][col] in {0, 1}
            for row in range(height)
        ):
            continue
        actual = sum(grid[row][col] * parsed[row][col] for row in range(height))
        if actual != column_targets[col]:
            violations.append(
                {
                    "type": "column_sum",
                    "column": col + 1,
                    "expected": column_targets[col],
                    "actual": actual,
                }
            )
    return violations


def render_sumplete(
    height: int,
    width: int,
    grid: Grid,
    row_targets: list[int],
    column_targets: list[int],
    mask: Mask | str | None = None,
) -> str:
    parsed = _coerce_mask(mask, height, width) if mask is not None else None
    lines = [
        f"Sumplete {height}x{width}",
        "Number grid | row target:",
    ]
    for row in range(height):
        if parsed is None:
            cells = " ".join(str(value) for value in grid[row])
        else:
            cells = " ".join(
                ("#" if parsed[row][col] else ".") + str(grid[row][col])
                for col in range(width)
            )
        lines.append(f"{cells} | {row_targets[row]}")
    lines.append("Column targets: " + " ".join(map(str, column_targets)))
    lines.append(
        "Rule: remove numbers so the kept numbers meet every row and column target; "
        "in solution masks # means kept and . means removed."
    )
    return "\n".join(lines)


def _mask_string(mask: Mask) -> str:
    return "".join("#" if value else "." for row in mask for value in row)


def _pattern_string(pattern: list[int]) -> str:
    return "".join("#" if value else "." for value in pattern)


def _targets_from_mask(grid: Grid, mask: Mask) -> tuple[list[int], list[int]]:
    height, width = len(grid), len(grid[0])
    return (
        [
            sum(grid[row][col] * mask[row][col] for col in range(width))
            for row in range(height)
        ],
        [
            sum(grid[row][col] * mask[row][col] for row in range(height))
            for col in range(width)
        ],
    )


def build_ground_truth(
    height: int,
    width: int,
    grid: Grid,
    row_targets: list[int],
    column_targets: list[int],
    solution: Mask | str,
    *,
    edge_case_kind: str | None = None,
    _solutions: list[Mask] | None = None,
) -> dict[str, Any]:
    structural = validate_structure(height, width, grid, row_targets, column_targets)
    solutions = (
        _solutions
        if _solutions is not None
        else solve_sumplete(
            height, width, grid, row_targets, column_targets, limit=2
        )
        if not structural
        else []
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

    selected = _coerce_mask(solution, height, width)
    if selected is None:
        selected = solutions[0] if solutions else [[0] * width for _ in range(height)]
    solution_text = _mask_string(selected)
    row_subsets = {
        f"r{row + 1}": [
            _pattern_string(pattern)
            for pattern in _line_subsets(grid[row], row_targets[row])
        ]
        for row in range(height)
    } if not structural else {}
    column_subsets = {
        f"c{col + 1}": [
            _pattern_string(pattern)
            for pattern in _line_subsets(
                [grid[row][col] for row in range(height)], column_targets[col]
            )
        ]
        for col in range(width)
    } if not structural else {}

    row_evaluations = [
        {
            "row": row + 1,
            "target": row_targets[row],
            "actual": sum(grid[row][col] * selected[row][col] for col in range(width)),
            "kept_cells": [format_cell(row, col) for col in range(width) if selected[row][col]],
            "kept_values": [grid[row][col] for col in range(width) if selected[row][col]],
        }
        for row in range(height)
    ]
    for evaluation in row_evaluations:
        evaluation["satisfied"] = evaluation["actual"] == evaluation["target"]
    column_evaluations = [
        {
            "column": col + 1,
            "target": column_targets[col],
            "actual": sum(grid[row][col] * selected[row][col] for row in range(height)),
            "kept_cells": [format_cell(row, col) for row in range(height) if selected[row][col]],
            "kept_values": [grid[row][col] for row in range(height) if selected[row][col]],
        }
        for col in range(width)
    ]
    for evaluation in column_evaluations:
        evaluation["satisfied"] = evaluation["actual"] == evaluation["target"]

    kept_cells = [
        format_cell(row, col)
        for row in range(height)
        for col in range(width)
        if selected[row][col]
    ]
    removed_cells = [
        format_cell(row, col)
        for row in range(height)
        for col in range(width)
        if not selected[row][col]
    ]
    contributions = [
        {"cell": format_cell(row, col), "value": grid[row][col]}
        for row in range(height)
        for col in range(width)
        if selected[row][col]
    ]

    forced_kept: list[str] = []
    forced_removed: list[str] = []
    if solutions:
        row_pattern_values = [
            _line_subsets(grid[row], row_targets[row]) for row in range(height)
        ]
        column_pattern_values = [
            _line_subsets(
                [grid[row][col] for row in range(height)], column_targets[col]
            )
            for col in range(width)
        ]
        for row in range(height):
            for col in range(width):
                row_states = {pattern[col] for pattern in row_pattern_values[row]}
                column_states = {pattern[row] for pattern in column_pattern_values[col]}
                states = row_states & column_states
                cell = format_cell(row, col)
                if states == {1}:
                    forced_kept.append(cell)
                elif states == {0}:
                    forced_removed.append(cell)
        if len(solutions) == 1:
            forced_kept = list(kept_cells)
            forced_removed = list(removed_cells)

    differences = []
    if len(solutions) > 1:
        differences = [
            {
                "cell": format_cell(row, col),
                "states": [
                    "kept" if solutions[0][row][col] else "removed",
                    "kept" if solutions[1][row][col] else "removed",
                ],
            }
            for row in range(height)
            for col in range(width)
            if solutions[0][row][col] != solutions[1][row][col]
        ]
    suggested_move: dict[str, Any] | None = None
    if solvability_status == "unique":
        if forced_kept:
            suggested_move = {
                "cell": forced_kept[0],
                "state": "kept",
                "reason": "forced_by_subset_sums",
            }
        elif forced_removed:
            suggested_move = {
                "cell": forced_removed[0],
                "state": "removed",
                "reason": "forced_by_subset_sums",
            }

    row_total = sum(row_targets) if all(_is_plain_int(value) for value in row_targets) else None
    column_total = (
        sum(column_targets) if all(_is_plain_int(value) for value in column_targets) else None
    )
    target_balance = {
        "row_target_total": row_total,
        "column_target_total": column_total,
        "balanced": row_total == column_total if row_total is not None and column_total is not None else False,
    }
    return {
        "height": height,
        "width": width,
        "grid": grid,
        "row_targets": row_targets,
        "column_targets": column_targets,
        "solution": solution_text,
        "solution_mask": selected,
        "solution_grid": selected,
        "kept_cells": kept_cells,
        "removed_cells": removed_cells,
        "solved_board": render_sumplete(
            height, width, grid, row_targets, column_targets, selected
        ),
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": solvability_status == "unique",
        "solution_count": 0 if edge_case_kind == "malformed_input" else len(solutions),
        "structural_violations": structural,
        "solution_violations": solution_violations(
            height, width, grid, row_targets, column_targets, selected
        ),
        "row_evaluations": row_evaluations,
        "column_evaluations": column_evaluations,
        "row_subsets": row_subsets,
        "column_subsets": column_subsets,
        "row_subset_counts": {line: len(subsets) for line, subsets in row_subsets.items()},
        "column_subset_counts": {
            line: len(subsets) for line, subsets in column_subsets.items()
        },
        "row_subset_examples": {line: subsets[:4] for line, subsets in row_subsets.items()},
        "column_subset_examples": {
            line: subsets[:4] for line, subsets in column_subsets.items()
        },
        "kept_contributions": contributions,
        "target_balance": target_balance,
        "forced_kept": forced_kept,
        "forced_removed": forced_removed,
        "solution_differences": differences,
        "suggested_move": suggested_move,
    }


class SumpletePuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get(
            "persistent_bank_filename", "sumplete_puzzle_bank.jsonl"
        )
        self.usage_path = output_path / puzzle_config.get(
            "usage_stats_filename", "sumplete_puzzle_usage.json"
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
        fixture_grid = [[1, 1], [1, 1]]
        if scenario.edge_case == "invalid_targets":
            return self._fixture_variant(
                parent,
                sample_index,
                fixture_grid,
                [3, 0],
                [0, 0],
                "invalid_targets",
            )
        if scenario.edge_case == "unsolvable_puzzle":
            return self._fixture_variant(
                parent,
                sample_index,
                fixture_grid,
                [0, 0],
                [1, 0],
                "unsolvable_puzzle",
            )
        if scenario.edge_case == "ambiguous_puzzle":
            return self._fixture_variant(
                parent,
                sample_index,
                fixture_grid,
                [1, 1],
                [1, 1],
                "ambiguous_puzzle",
            )

        position = sample_index % len(_TRANSFORMATIONS)
        operation = _D4_OPERATIONS[position % len(_D4_OPERATIONS)]
        complement = position >= len(_D4_OPERATIONS)
        transformation = f"complement_{operation}" if complement else operation
        return self._transformed_variant(parent, operation, complement, transformation)

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
        size, strategies = _DIFFICULTY_SPECS[difficulty]
        difficulty_offset = list(_DIFFICULTY_SPECS).index(difficulty) + 1
        rng = random.Random(
            self.seed * 1_000_003 + difficulty_offset * 100_019 + lineage * 7_919
        )
        for _ in range(2_000):
            grid = [[rng.randint(1, 9) for _ in range(size)] for _ in range(size)]
            density = {"easy": 0.40, "medium": 0.45, "hard": 0.50, "expert": 0.55}[difficulty]
            mask = [
                [1 if rng.random() < density else 0 for _ in range(size)]
                for _ in range(size)
            ]
            selected_count = sum(sum(row) for row in mask)
            if selected_count in {0, size * size}:
                continue
            if any(all(value == row[0] for value in row) for row in mask):
                continue
            if any(
                all(mask[row][col] == mask[0][col] for row in range(size))
                for col in range(size)
            ):
                continue
            row_targets, column_targets = _targets_from_mask(grid, mask)
            solutions = solve_sumplete(
                size, size, grid, row_targets, column_targets, limit=2
            )
            if len(solutions) != 1:
                continue
            lineage_id = f"sumplete_{difficulty}_{size}_lineage_{lineage}"
            puzzle = canonical_puzzle(size, size, grid, row_targets, column_targets)
            signature = hashlib.sha1(puzzle.encode()).hexdigest()[:16]
            return self._record(
                puzzle_id=lineage_id,
                height=size,
                width=size,
                grid=grid,
                row_targets=row_targets,
                column_targets=column_targets,
                solution_mask=mask,
                difficulty=difficulty,
                strategies=strategies,
                parent_id=None,
                transformation="identity",
                source="generated",
                canonical_signature=signature,
                _solutions=solutions,
            )
        raise RuntimeError(
            f"Unable to generate a unique {difficulty} Sumplete lineage {lineage}."
        )

    def _transformed_variant(
        self,
        parent: PuzzleRecord,
        operation: str,
        complement: bool,
        transformation: str,
    ) -> PuzzleRecord:
        height, width, grid, _, _ = parse_puzzle(parent.puzzle)
        parent_mask = _coerce_mask(parent.solution, height, width)
        assert parent_mask is not None
        new_height, new_width, transform = _coordinate_transform(height, width, operation)
        transformed_grid = [[0] * new_width for _ in range(new_height)]
        transformed_mask = [[0] * new_width for _ in range(new_height)]
        for row in range(height):
            for col in range(width):
                new_row, new_col = transform(row, col)
                transformed_grid[new_row][new_col] = grid[row][col]
                transformed_mask[new_row][new_col] = (
                    1 - parent_mask[row][col] if complement else parent_mask[row][col]
                )
        row_targets, column_targets = _targets_from_mask(
            transformed_grid, transformed_mask
        )
        puzzle = canonical_puzzle(
            new_height, new_width, transformed_grid, row_targets, column_targets
        )
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, puzzle),
            height=new_height,
            width=new_width,
            grid=transformed_grid,
            row_targets=row_targets,
            column_targets=column_targets,
            solution_mask=transformed_mask,
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
        grid: Grid,
        row_targets: list[int],
        column_targets: list[int],
        kind: str,
    ) -> PuzzleRecord:
        height, width = len(grid), len(grid[0])
        structural = validate_structure(height, width, grid, row_targets, column_targets)
        solutions = (
            solve_sumplete(
                height, width, grid, row_targets, column_targets, limit=2
            )
            if not structural
            else []
        )
        fallback = [[0] * width for _ in range(height)]
        transformation = f"{kind}_{sample_index}"
        puzzle = canonical_puzzle(height, width, grid, row_targets, column_targets)
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, puzzle),
            height=height,
            width=width,
            grid=grid,
            row_targets=row_targets,
            column_targets=column_targets,
            solution_mask=solutions[0] if solutions else fallback,
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
        height, width, grid, row_targets, column_targets = parse_puzzle(parent.puzzle)
        mask = _coerce_mask(parent.solution, height, width)
        assert mask is not None
        broken = (
            "Sumplete ?x?: grid=[4, x, 7; row targets missing",
            "Sumplete numbers: {r1: 2 3 ??? | columns=[",
            "Sumplete: remove [?, 5; target => broken",
        )[sample_index % 3]
        transformation = f"malformed_{sample_index}"
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, parent.puzzle),
            height=height,
            width=width,
            grid=grid,
            row_targets=row_targets,
            column_targets=column_targets,
            solution_mask=mask,
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
        height: int,
        width: int,
        grid: Grid,
        row_targets: list[int],
        column_targets: list[int],
        solution_mask: Mask,
        difficulty: str,
        strategies: list[str],
        parent_id: str | None,
        transformation: str,
        source: str,
        canonical_signature: str,
        edge_case_kind: str | None = None,
        rendered_board: str | None = None,
        _solutions: list[Mask] | None = None,
    ) -> PuzzleRecord:
        grid = [list(row) for row in grid]
        row_targets = list(row_targets)
        column_targets = list(column_targets)
        puzzle = canonical_puzzle(height, width, grid, row_targets, column_targets)
        solution = _mask_string(solution_mask)
        truth = build_ground_truth(
            height,
            width,
            grid,
            row_targets,
            column_targets,
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
            num_clues=height * width + height + width,
            required_strategies=list(strategies),
            unique_solution_status=truth["unique_solution_status"],
            source=source,
            canonical_signature=canonical_signature,
            usage_count=usage_stats.get(puzzle_id, 0),
            parent_puzzle_id=parent_id,
            transformation=transformation,
            rendered_board=rendered_board
            or render_sumplete(height, width, grid, row_targets, column_targets),
            ground_truth=truth,
            metadata={
                "height": height,
                "width": width,
                "grid": grid,
                "row_targets": row_targets,
                "column_targets": column_targets,
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


def _coordinate_transform(
    height: int,
    width: int,
    operation: str,
) -> tuple[int, int, Callable[[int, int], Cell]]:
    transforms: dict[str, tuple[int, int, Callable[[int, int], Cell]]] = {
        "identity": (height, width, lambda row, col: (row, col)),
        "rotate_90": (width, height, lambda row, col: (col, height - 1 - row)),
        "rotate_180": (
            height,
            width,
            lambda row, col: (height - 1 - row, width - 1 - col),
        ),
        "rotate_270": (width, height, lambda row, col: (width - 1 - col, row)),
        "reflect_horizontal": (height, width, lambda row, col: (height - 1 - row, col)),
        "reflect_vertical": (height, width, lambda row, col: (row, width - 1 - col)),
        "transpose": (width, height, lambda row, col: (col, row)),
        "anti_transpose": (
            width,
            height,
            lambda row, col: (width - 1 - col, height - 1 - row),
        ),
    }
    if operation not in transforms:
        raise ValueError(f"Unknown Sumplete transformation: {operation}")
    return transforms[operation]
