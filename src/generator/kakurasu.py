from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Callable

from .models import PuzzleRecord, Scenario


Mask = list[list[int]]
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
    "easy": (4, ["direct_sum", "line_completion"]),
    "medium": (5, ["subset_elimination", "cross_line_check"]),
    "hard": (6, ["weighted_subset", "forced_state_intersection"]),
    "expert": (7, ["multi_line_reasoning", "exact_subset_cover"]),
}


def format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def canonical_puzzle(
    height: int,
    width: int,
    row_targets: list[int],
    column_targets: list[int],
) -> str:
    return json.dumps(
        {
            "height": height,
            "width": width,
            "row_targets": list(row_targets),
            "column_targets": list(column_targets),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_puzzle(puzzle: str) -> tuple[int, int, list[int], list[int]]:
    payload = json.loads(puzzle)
    return (
        int(payload["height"]),
        int(payload["width"]),
        list(payload["row_targets"]),
        list(payload["column_targets"]),
    )


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _triangular(size: int) -> int:
    return size * (size + 1) // 2


def validate_structure(
    height: int,
    width: int,
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
    row_maximum = _triangular(width) if dimensions_valid else None
    column_maximum = _triangular(height) if dimensions_valid else None
    for index, target in enumerate(row_targets):
        if not _is_plain_int(target) or target < 0:
            violations.append({"type": "row_target", "row": index + 1, "target": target})
        elif row_maximum is not None and target > row_maximum:
            violations.append(
                {
                    "type": "row_target_range",
                    "row": index + 1,
                    "target": target,
                    "maximum": row_maximum,
                }
            )
    for index, target in enumerate(column_targets):
        if not _is_plain_int(target) or target < 0:
            violations.append(
                {"type": "column_target", "column": index + 1, "target": target}
            )
        elif column_maximum is not None and target > column_maximum:
            violations.append(
                {
                    "type": "column_target_range",
                    "column": index + 1,
                    "target": target,
                    "maximum": column_maximum,
                }
            )
    return violations


def _line_patterns(length: int, target: int) -> list[list[int]]:
    patterns = []
    for bits in range(1 << length):
        pattern = [1 if bits & (1 << index) else 0 for index in range(length)]
        if sum((index + 1) * value for index, value in enumerate(pattern)) == target:
            patterns.append(pattern)
    return patterns


def solve_kakurasu(
    height: int,
    width: int,
    row_targets: list[int],
    column_targets: list[int],
    limit: int = 2,
) -> list[Mask]:
    """Enumerate target-matching row subsets with column-capacity pruning."""

    if limit <= 0 or validate_structure(height, width, row_targets, column_targets):
        return []
    row_options = [_line_patterns(width, target) for target in row_targets]
    if any(not patterns for patterns in row_options):
        return []

    # The minimum and maximum contribution that each remaining row can make to
    # each column provide a sound, inexpensive bound at every search depth.
    suffix_minimum = [[0] * width for _ in range(height + 1)]
    suffix_maximum = [[0] * width for _ in range(height + 1)]
    for row in range(height - 1, -1, -1):
        weight = row + 1
        for col in range(width):
            states = {pattern[col] for pattern in row_options[row]}
            suffix_minimum[row][col] = suffix_minimum[row + 1][col] + (
                weight if states == {1} else 0
            )
            suffix_maximum[row][col] = suffix_maximum[row + 1][col] + (
                weight if 1 in states else 0
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
        weight = row + 1
        for pattern in row_options[row]:
            next_sums = [column_sums[col] + weight * pattern[col] for col in range(width)]
            if any(next_sums[col] > column_targets[col] for col in range(width)):
                continue
            if any(
                next_sums[col] + suffix_minimum[row + 1][col] > column_targets[col]
                or next_sums[col] + suffix_maximum[row + 1][col] < column_targets[col]
                for col in range(width)
            ):
                continue
            selected_rows.append(pattern)
            old_sums = column_sums[:]
            column_sums[:] = next_sums
            search(row + 1)
            column_sums[:] = old_sums
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
        actual = sum((col + 1) * parsed[row][col] for col in range(width))
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
        values_valid = all(
            _is_plain_int(parsed[row][col]) and parsed[row][col] in {0, 1}
            for row in range(height)
        )
        if not values_valid:
            continue
        actual = sum((row + 1) * parsed[row][col] for row in range(height))
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


def render_kakurasu(
    height: int,
    width: int,
    row_targets: list[int],
    column_targets: list[int],
    mask: Mask | str | None = None,
) -> str:
    parsed = _coerce_mask(mask, height, width) if mask is not None else None
    lines = [
        f"Kakurasu {height}x{width}",
        "Column weights: " + " ".join(str(col + 1) for col in range(width)),
        "Grid (# = selected, . = unselected/undecided) | row target:",
    ]
    for row in range(height):
        cells = " ".join(
            "#" if parsed is not None and parsed[row][col] else "." for col in range(width)
        )
        lines.append(f"{cells} | {row_targets[row]}")
    lines.append("Column targets: " + " ".join(map(str, column_targets)))
    lines.append("Row weights: " + " ".join(str(row + 1) for row in range(height)))
    lines.append(
        "Rule: a selected cell contributes its column weight to its row target "
        "and its row weight to its column target."
    )
    return "\n".join(lines)


def _mask_string(mask: Mask) -> str:
    return "".join("#" if value else "." for row in mask for value in row)


def _pattern_string(pattern: list[int]) -> str:
    return "".join("#" if value else "." for value in pattern)


def _targets_from_mask(mask: Mask) -> tuple[list[int], list[int]]:
    height, width = len(mask), len(mask[0])
    row_targets = [
        sum((col + 1) * mask[row][col] for col in range(width))
        for row in range(height)
    ]
    column_targets = [
        sum((row + 1) * mask[row][col] for row in range(height))
        for col in range(width)
    ]
    return row_targets, column_targets


def build_ground_truth(
    height: int,
    width: int,
    row_targets: list[int],
    column_targets: list[int],
    solution: Mask | str,
    *,
    edge_case_kind: str | None = None,
    _solutions: list[Mask] | None = None,
) -> dict[str, Any]:
    structural = validate_structure(height, width, row_targets, column_targets)
    solutions = (
        _solutions
        if _solutions is not None
        else solve_kakurasu(height, width, row_targets, column_targets, limit=2)
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
    row_patterns = {
        f"r{row + 1}": [_pattern_string(pattern) for pattern in _line_patterns(width, target)]
        for row, target in enumerate(row_targets)
    } if not structural else {}
    column_patterns = {
        f"c{col + 1}": [_pattern_string(pattern) for pattern in _line_patterns(height, target)]
        for col, target in enumerate(column_targets)
    } if not structural else {}

    row_evaluations = [
        {
            "row": row + 1,
            "target": row_targets[row],
            "actual": sum((col + 1) * selected[row][col] for col in range(width)),
            "selected_columns": [col + 1 for col in range(width) if selected[row][col]],
        }
        for row in range(height)
    ]
    for evaluation in row_evaluations:
        evaluation["satisfied"] = evaluation["actual"] == evaluation["target"]
    column_evaluations = [
        {
            "column": col + 1,
            "target": column_targets[col],
            "actual": sum((row + 1) * selected[row][col] for row in range(height)),
            "selected_rows": [row + 1 for row in range(height) if selected[row][col]],
        }
        for col in range(width)
    ]
    for evaluation in column_evaluations:
        evaluation["satisfied"] = evaluation["actual"] == evaluation["target"]

    selected_cells = [
        format_cell(row, col)
        for row in range(height)
        for col in range(width)
        if selected[row][col]
    ]
    unselected_cells = [
        format_cell(row, col)
        for row in range(height)
        for col in range(width)
        if not selected[row][col]
    ]
    contributions = [
        {
            "cell": format_cell(row, col),
            "row_contribution": col + 1,
            "column_contribution": row + 1,
        }
        for row in range(height)
        for col in range(width)
        if selected[row][col]
    ]

    forced_selected: list[str] = []
    forced_unselected: list[str] = []
    if solutions:
        for row in range(height):
            row_states = [
                {pattern[col] for pattern in _line_patterns(width, row_targets[row])}
                for col in range(width)
            ]
            for col in range(width):
                column_states = {
                    pattern[row] for pattern in _line_patterns(height, column_targets[col])
                }
                states = row_states[col] & column_states
                cell = format_cell(row, col)
                if states == {1}:
                    forced_selected.append(cell)
                elif states == {0}:
                    forced_unselected.append(cell)
        # Exact uniqueness makes every cell state globally forced. This extends
        # the locally proven line-pattern deductions without making any claim
        # from a capped ambiguous search.
        if len(solutions) == 1:
            forced_selected = sorted(selected_cells, key=lambda cell: tuple(map(int, cell[1:].split("c"))))
            forced_unselected = sorted(unselected_cells, key=lambda cell: tuple(map(int, cell[1:].split("c"))))

    differences = []
    if len(solutions) > 1:
        differences = [
            {
                "cell": format_cell(row, col),
                "states": [
                    "selected" if solutions[0][row][col] else "unselected",
                    "selected" if solutions[1][row][col] else "unselected",
                ],
            }
            for row in range(height)
            for col in range(width)
            if solutions[0][row][col] != solutions[1][row][col]
        ]
    suggested_move: dict[str, Any] | None = None
    if solvability_status == "unique":
        if forced_selected:
            suggested_move = {
                "cell": forced_selected[0],
                "state": "selected",
                "reason": "forced_by_weighted_sums",
            }
        elif forced_unselected:
            suggested_move = {
                "cell": forced_unselected[0],
                "state": "unselected",
                "reason": "forced_by_weighted_sums",
            }

    return {
        "height": height,
        "width": width,
        "row_targets": row_targets,
        "column_targets": column_targets,
        "solution": solution_text,
        "solution_mask": selected,
        "solution_grid": selected,
        "selected_cells": selected_cells,
        "unselected_cells": unselected_cells,
        "solved_board": render_kakurasu(height, width, row_targets, column_targets, selected),
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": solvability_status == "unique",
        "solution_count": 0 if edge_case_kind == "malformed_input" else len(solutions),
        "structural_violations": structural,
        "solution_violations": solution_violations(
            height, width, row_targets, column_targets, selected
        ),
        "row_evaluations": row_evaluations,
        "column_evaluations": column_evaluations,
        "row_patterns": row_patterns,
        "column_patterns": column_patterns,
        "row_pattern_counts": {line: len(patterns) for line, patterns in row_patterns.items()},
        "column_pattern_counts": {
            line: len(patterns) for line, patterns in column_patterns.items()
        },
        "row_pattern_examples": {line: patterns[:4] for line, patterns in row_patterns.items()},
        "column_pattern_examples": {
            line: patterns[:4] for line, patterns in column_patterns.items()
        },
        "weighted_contributions": contributions,
        "forced_selected": forced_selected,
        "forced_unselected": forced_unselected,
        "solution_differences": differences,
        "suggested_move": suggested_move,
    }


def _transform_mask(mask: Mask, operation: str, *, complement: bool = False) -> Mask:
    height, width = len(mask), len(mask[0])
    new_height, new_width, transform = _coordinate_transform(height, width, operation)
    transformed = [[0] * new_width for _ in range(new_height)]
    for row in range(height):
        for col in range(width):
            new_row, new_col = transform(row, col)
            transformed[new_row][new_col] = (
                1 - mask[row][col] if complement else mask[row][col]
            )
    return transformed


def _all_d4_targets_are_unique(mask: Mask) -> bool:
    """Kakurasu weights are anchored, so uniqueness must be rechecked after D4."""

    for operation in _D4_OPERATIONS:
        transformed = _transform_mask(mask, operation)
        row_targets, column_targets = _targets_from_mask(transformed)
        solutions = solve_kakurasu(
            len(transformed),
            len(transformed[0]),
            row_targets,
            column_targets,
            limit=2,
        )
        if len(solutions) != 1 or solutions[0] != transformed:
            return False
    return True


class KakurasuPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get(
            "persistent_bank_filename", "kakurasu_puzzle_bank.jsonl"
        )
        self.usage_path = output_path / puzzle_config.get(
            "usage_stats_filename", "kakurasu_puzzle_usage.json"
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
        if scenario.edge_case == "invalid_targets":
            return self._fixture_variant(
                parent,
                sample_index,
                [7, 0, 0],
                [0, 0, 0],
                "invalid_targets",
            )
        if scenario.edge_case == "unsolvable_puzzle":
            return self._fixture_variant(
                parent,
                sample_index,
                [0, 0, 0],
                [1, 0, 0],
                "unsolvable_puzzle",
            )
        if scenario.edge_case == "ambiguous_puzzle":
            return self._fixture_variant(
                parent,
                sample_index,
                [3, 3, 3],
                [3, 3, 3],
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
            density = {"easy": 0.38, "medium": 0.43, "hard": 0.48, "expert": 0.52}[difficulty]
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
            row_targets, column_targets = _targets_from_mask(mask)
            solutions = solve_kakurasu(
                size, size, row_targets, column_targets, limit=2
            )
            if (
                len(solutions) != 1
                or solutions[0] != mask
                or not _all_d4_targets_are_unique(mask)
            ):
                continue
            lineage_id = f"kakurasu_{difficulty}_{size}_lineage_{lineage}"
            puzzle = canonical_puzzle(size, size, row_targets, column_targets)
            signature = hashlib.sha1(puzzle.encode()).hexdigest()[:16]
            return self._record(
                puzzle_id=lineage_id,
                height=size,
                width=size,
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
            f"Unable to generate a unique {difficulty} Kakurasu lineage {lineage}."
        )

    def _transformed_variant(
        self,
        parent: PuzzleRecord,
        operation: str,
        complement: bool,
        transformation: str,
    ) -> PuzzleRecord:
        height, width, _, _ = parse_puzzle(parent.puzzle)
        parent_mask = _coerce_mask(parent.solution, height, width)
        assert parent_mask is not None
        transformed = _transform_mask(parent_mask, operation, complement=complement)
        new_height, new_width = len(transformed), len(transformed[0])
        row_targets, column_targets = _targets_from_mask(transformed)
        solutions = solve_kakurasu(
            new_height,
            new_width,
            row_targets,
            column_targets,
            limit=2,
        )
        if len(solutions) != 1 or solutions[0] != transformed:
            raise RuntimeError(
                "Generated Kakurasu transformation is not solver-confirmed unique: "
                f"{parent.puzzle_id}/{transformation}"
            )
        puzzle = canonical_puzzle(new_height, new_width, row_targets, column_targets)
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, puzzle),
            height=new_height,
            width=new_width,
            row_targets=row_targets,
            column_targets=column_targets,
            solution_mask=transformed,
            difficulty=parent.difficulty,
            strategies=list(parent.required_strategies),
            parent_id=parent.puzzle_id,
            transformation=transformation,
            source=parent.source,
            canonical_signature=parent.canonical_signature,
            _solutions=solutions,
        )

    def _fixture_variant(
        self,
        parent: PuzzleRecord,
        sample_index: int,
        row_targets: list[int],
        column_targets: list[int],
        kind: str,
    ) -> PuzzleRecord:
        height, width = len(row_targets), len(column_targets)
        structural = validate_structure(height, width, row_targets, column_targets)
        solutions = (
            solve_kakurasu(height, width, row_targets, column_targets, limit=2)
            if not structural
            else []
        )
        fallback = [[0] * width for _ in range(height)]
        transformation = f"{kind}_{sample_index}"
        puzzle = canonical_puzzle(height, width, row_targets, column_targets)
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, puzzle),
            height=height,
            width=width,
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
        height, width, row_targets, column_targets = parse_puzzle(parent.puzzle)
        mask = _coerce_mask(parent.solution, height, width)
        assert mask is not None
        broken = (
            "Kakurasu ?x?: rows=[3, ?, 4; columns missing",
            "Kakurasu grid: # . x | target=]; weights={broken",
            "Kakurasu: row sums (2, ???), column sums [",
        )[sample_index % 3]
        transformation = f"malformed_{sample_index}"
        return self._record(
            puzzle_id=self._variant_id(parent, transformation, parent.puzzle),
            height=height,
            width=width,
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
        row_targets = list(row_targets)
        column_targets = list(column_targets)
        puzzle = canonical_puzzle(height, width, row_targets, column_targets)
        solution = _mask_string(solution_mask)
        truth = build_ground_truth(
            height,
            width,
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
            num_clues=height + width,
            required_strategies=list(strategies),
            unique_solution_status=truth["unique_solution_status"],
            source=source,
            canonical_signature=canonical_signature,
            usage_count=usage_stats.get(puzzle_id, 0),
            parent_puzzle_id=parent_id,
            transformation=transformation,
            rendered_board=rendered_board
            or render_kakurasu(height, width, row_targets, column_targets),
            ground_truth=truth,
            metadata={
                "height": height,
                "width": width,
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
        raise ValueError(f"Unknown Kakurasu transformation: {operation}")
    return transforms[operation]
