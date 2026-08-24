from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


class PuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "puzzle_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "puzzle_usage.json")
        self.seed = int(config.get("generation", {}).get("random_seed", 17))
        self.base_bank = _build_base_puzzle_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        candidates = [p for p in self.base_bank if p.difficulty == scenario.difficulty]
        if not candidates:
            candidates = list(self.base_bank)

        ranked = sorted(
            candidates,
            key=lambda puzzle: (
                self.usage_stats.get(puzzle.puzzle_id, 0),
                self.usage_stats.get(puzzle.parent_puzzle_id or puzzle.puzzle_id, 0),
                puzzle.num_clues,
            ),
        )
        parent = ranked[0]
        if scenario.edge_case == "malformed_input":
            return self._build_malformed_variant(parent, sample_index)
        if scenario.edge_case == "invalid_board":
            return self._build_invalid_variant(parent, sample_index)
        if scenario.edge_case == "unsolvable_board":
            return self._build_unsolvable_variant(parent, sample_index)
        if scenario.edge_case == "ambiguous_board":
            return self._build_ambiguous_variant(parent, sample_index)
        return self._build_transformed_variant(parent, sample_index)

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        self.usage_stats[puzzle.puzzle_id] = self.usage_stats.get(puzzle.puzzle_id, 0) + 1
        parent_id = puzzle.parent_puzzle_id or puzzle.puzzle_id
        self.usage_stats[parent_id] = self.usage_stats.get(parent_id, 0) + 1
        with self.usage_path.open("w", encoding="utf-8") as f:
            json.dump(self.usage_stats, f, indent=2, sort_keys=True)

    def _ensure_bank(self) -> None:
        if self.bank_path.exists():
            return
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        with self.bank_path.open("w", encoding="utf-8") as f:
            for puzzle in self.base_bank:
                f.write(json.dumps(puzzle.to_dict(), ensure_ascii=False) + "\n")

    def _load_usage_stats(self) -> dict[str, int]:
        if not self.usage_path.exists():
            return {}
        return json.loads(self.usage_path.read_text(encoding="utf-8"))

    def _build_transformed_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        # A single operation chosen from a short cycle quickly exhausts the
        # effective variant pool, especially because rejected generations are
        # reserved too. Address the full Sudoku symmetry space instead. The
        # mixed-radix index deterministically composes digit, row, column, band,
        # stack, and transpose permutations while remaining stable on resume.
        variant_index = self.seed + sample_index
        puzzle_string, solution_string = _apply_indexed_transformation(
            parent.puzzle,
            parent.solution,
            variant_index,
        )
        transformation = f"indexed_permutation_{variant_index}"
        return self._make_variant(parent, puzzle_string, solution_string, transformation)

    def _build_malformed_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        malformed = parent.rendered_board.replace(" | ", " ").replace("-", "")
        malformed = "\n".join(line.replace(" ", "") for line in malformed.splitlines()[:5])
        return self._make_variant(
            parent,
            parent.puzzle,
            parent.solution,
            f"malformed_{sample_index}",
            rendered_board=malformed,
            unique_solution_status=False,
            metadata={"edge_case_kind": "malformed_input"},
        )

    def _build_invalid_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        chars = list(parent.puzzle)
        chars[0] = chars[1] if chars[1] != "0" else "5"
        puzzle_string = "".join(chars)
        return self._make_variant(
            parent,
            puzzle_string,
            parent.solution,
            f"invalid_{sample_index}",
            unique_solution_status=False,
            metadata={"edge_case_kind": "invalid_board"},
        )

    def _build_unsolvable_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        chars = list(parent.puzzle)
        chars[8] = "9"
        puzzle_string = "".join(chars)
        return self._make_variant(
            parent,
            puzzle_string,
            parent.solution,
            f"unsolvable_{sample_index}",
            unique_solution_status=False,
            metadata={"edge_case_kind": "unsolvable_board"},
        )

    def _build_ambiguous_variant(self, parent: PuzzleRecord, sample_index: int) -> PuzzleRecord:
        chars = list(parent.puzzle)
        for index in (0, 10, 20, 30, 40):
            chars[index] = "0"
        puzzle_string = "".join(chars)
        return self._make_variant(
            parent,
            puzzle_string,
            parent.solution,
            f"ambiguous_{sample_index}",
            unique_solution_status=False,
            metadata={"edge_case_kind": "ambiguous_board"},
        )

    def _make_variant(
        self,
        parent: PuzzleRecord,
        puzzle_string: str,
        solution_string: str,
        transformation: str,
        *,
        rendered_board: str | None = None,
        unique_solution_status: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PuzzleRecord:
        variant_id = hashlib.sha1(
            f"{parent.puzzle_id}|{transformation}|{puzzle_string}".encode("utf-8")
        ).hexdigest()[:16]
        canonical_signature = parent.canonical_signature
        variant = PuzzleRecord(
            puzzle_id=variant_id,
            puzzle=puzzle_string,
            solution=solution_string,
            difficulty=parent.difficulty,
            num_clues=sum(1 for char in puzzle_string if char != "0"),
            required_strategies=list(parent.required_strategies),
            unique_solution_status=parent.unique_solution_status if unique_solution_status is None else unique_solution_status,
            source=parent.source,
            canonical_signature=canonical_signature,
            usage_count=self.usage_stats.get(variant_id, 0),
            parent_puzzle_id=parent.puzzle_id,
            transformation=transformation,
            rendered_board=rendered_board or render_board(puzzle_string),
            ground_truth=build_ground_truth(
                puzzle_string=puzzle_string,
                solution_string=solution_string,
                unique_solution_status=parent.unique_solution_status if unique_solution_status is None else unique_solution_status,
                rendered_board=rendered_board or render_board(puzzle_string),
                edge_case_kind=(metadata or {}).get("edge_case_kind"),
            ),
            metadata=metadata or {},
        )
        return variant


def render_board(puzzle_string: str) -> str:
    if len(puzzle_string) != 81:
        return puzzle_string
    formatted_rows: list[str] = []
    for row_index in range(9):
        row = puzzle_string[row_index * 9:(row_index + 1) * 9]
        values = [value if value != "0" else "." for value in row]
        formatted_rows.append(
            f"{' '.join(values[0:3])} | {' '.join(values[3:6])} | {' '.join(values[6:9])}"
        )
        if row_index in {2, 5}:
            formatted_rows.append("------+-------+------")
    return "\n".join(formatted_rows)


def _build_base_puzzle_bank() -> list[PuzzleRecord]:
    base_rows = [
        {
            "puzzle_id": "base_easy_1",
            "puzzle": "530070000600195000098000060800060003400803001700020006060000280000419005000080079",
            "solution": "534678912672195348198342567859761423426853791713924856961537284287419635345286179",
            "difficulty": "easy",
            "required_strategies": ["single_candidate", "single_position"],
        },
        {
            "puzzle_id": "base_medium_1",
            "puzzle": "003020600900305001001806400008102900700000008006708200002609500800203009005010300",
            "solution": "483921657967345821251876493548132976729564138136798245372689514814253769695417382",
            "difficulty": "medium",
            "required_strategies": ["single_candidate", "hidden_pair"],
        },
        {
            "puzzle_id": "base_hard_1",
            "puzzle": "200080300060070084030500209000105408000000000402706000301007040720040060004010003",
            "solution": "245981376169273584837564219976135428513428697482796135391657842728349561654812793",
            "difficulty": "hard",
            "required_strategies": ["x_wing", "locked_candidates"],
        },
        {
            "puzzle_id": "base_expert_1",
            "puzzle": "000000907000420180000705026100904000050000040000507009920108000034059000507000000",
            "solution": "483651927659423187271795326168934572952876413347517869926148735834259671517362894",
            "difficulty": "expert",
            "required_strategies": ["swordfish", "xy_wing"],
        },
    ]
    puzzles: list[PuzzleRecord] = []
    for row in base_rows:
        puzzle = row["puzzle"]
        puzzles.append(
            PuzzleRecord(
                puzzle_id=row["puzzle_id"],
                puzzle=puzzle,
                solution=row["solution"],
                difficulty=row["difficulty"],
                num_clues=sum(1 for char in puzzle if char != "0"),
                required_strategies=row["required_strategies"],
                unique_solution_status=True,
                source="builtin",
                canonical_signature=row["puzzle_id"],
                usage_count=0,
                rendered_board=render_board(puzzle),
                ground_truth=build_ground_truth(
                    puzzle_string=puzzle,
                    solution_string=row["solution"],
                    unique_solution_status=True,
                    rendered_board=render_board(puzzle),
                ),
            )
        )
    return puzzles


def build_ground_truth(
    *,
    puzzle_string: str,
    solution_string: str,
    unique_solution_status: bool,
    rendered_board: str,
    edge_case_kind: str | None = None,
) -> dict[str, Any]:
    conflicts = _find_conflicts(puzzle_string)
    given_cells = _cell_positions(puzzle_string, include_given=True)
    empty_cells = _cell_positions(puzzle_string, include_given=False)
    candidates = _candidate_map(puzzle_string)
    first_empty_cell = empty_cells[0] if empty_cells else None
    suggested_move = None
    if first_empty_cell and len(solution_string) == 81:
        row, col = _parse_cell(first_empty_cell)
        suggested_move = {
            "cell": first_empty_cell,
            "value": solution_string[row * 9 + col],
            "candidates": candidates.get(first_empty_cell, []),
        }

    validity_status = "valid" if not conflicts and len(puzzle_string) == 81 else "invalid"
    solvability_status = "unique" if unique_solution_status else "non_unique_or_invalid"
    if edge_case_kind == "unsolvable_board":
        solvability_status = "unsolvable"
    elif edge_case_kind == "ambiguous_board":
        solvability_status = "ambiguous"
    elif edge_case_kind == "malformed_input":
        validity_status = "malformed"
        solvability_status = "unknown"

    return {
        "solved_board": render_board(solution_string),
        "solution": solution_string,
        "validity_status": validity_status,
        "solvability_status": solvability_status,
        "unique_solution_status": unique_solution_status,
        "num_given_cells": len(given_cells),
        "num_empty_cells": len(empty_cells),
        "given_cells": given_cells,
        "empty_cells": empty_cells,
        "candidates": candidates,
        "conflicts": conflicts,
        "suggested_move": suggested_move,
    }


def _candidate_map(puzzle_string: str) -> dict[str, list[str]]:
    if len(puzzle_string) != 81:
        return {}
    candidates: dict[str, list[str]] = {}
    for index, value in enumerate(puzzle_string):
        if value != "0":
            continue
        row, col = divmod(index, 9)
        used = set()
        used.update(puzzle_string[row * 9:row * 9 + 9].replace("0", ""))
        used.update(puzzle_string[col::9].replace("0", ""))
        box_row, box_col = (row // 3) * 3, (col // 3) * 3
        for r in range(box_row, box_row + 3):
            for c in range(box_col, box_col + 3):
                cell = puzzle_string[r * 9 + c]
                if cell != "0":
                    used.add(cell)
        candidates[_format_cell(row, col)] = [str(num) for num in range(1, 10) if str(num) not in used]
    return candidates


def _find_conflicts(puzzle_string: str) -> list[dict[str, Any]]:
    if len(puzzle_string) != 81:
        return [{"type": "length", "message": "Puzzle is not 81 cells long."}]
    conflicts: list[dict[str, Any]] = []
    units: list[tuple[str, list[int]]] = []
    units.extend((f"row_{row + 1}", [row * 9 + col for col in range(9)]) for row in range(9))
    units.extend((f"col_{col + 1}", [row * 9 + col for row in range(9)]) for col in range(9))
    for box_row in range(3):
        for box_col in range(3):
            indexes = [
                (box_row * 3 + row) * 9 + (box_col * 3 + col)
                for row in range(3)
                for col in range(3)
            ]
            units.append((f"box_{box_row + 1}_{box_col + 1}", indexes))

    for unit_name, indexes in units:
        seen: dict[str, list[str]] = {}
        for index in indexes:
            value = puzzle_string[index]
            if value == "0":
                continue
            row, col = divmod(index, 9)
            seen.setdefault(value, []).append(_format_cell(row, col))
        for value, cells in seen.items():
            if len(cells) > 1:
                conflicts.append({"unit": unit_name, "value": value, "cells": cells})
    return conflicts


def _cell_positions(puzzle_string: str, *, include_given: bool) -> list[str]:
    if len(puzzle_string) != 81:
        return []
    cells: list[str] = []
    for index, value in enumerate(puzzle_string):
        is_given = value != "0"
        if is_given == include_given:
            row, col = divmod(index, 9)
            cells.append(_format_cell(row, col))
    return cells


def _format_cell(row: int, col: int) -> str:
    return f"r{row + 1}c{col + 1}"


def _parse_cell(cell: str) -> tuple[int, int]:
    return int(cell[1]) - 1, int(cell[3]) - 1


def _apply_indexed_transformation(
    puzzle_string: str,
    solution_string: str,
    variant_index: int,
) -> tuple[str, str]:
    """Return a reproducible member of the Sudoku symmetry group.

    There are 9! * 6^8 * 2 addressable combinations (over 1.2 trillion),
    before accounting for the separate base puzzle in each difficulty band.
    """
    code = variant_index
    digit_order, code = _take_permutation(code, 9)
    band_order, code = _take_permutation(code, 3)
    rows_in_bands = []
    for _ in range(3):
        order, code = _take_permutation(code, 3)
        rows_in_bands.append(order)
    stack_order, code = _take_permutation(code, 3)
    cols_in_stacks = []
    for _ in range(3):
        order, code = _take_permutation(code, 3)
        cols_in_stacks.append(order)
    transpose = bool(code % 2)

    row_order = [band * 3 + row for band in band_order for row in rows_in_bands[band]]
    col_order = [stack * 3 + col for stack in stack_order for col in cols_in_stacks[stack]]
    digit_map = {str(source + 1): str(target + 1) for source, target in enumerate(digit_order)}

    def transform(value: str) -> str:
        grid = _to_grid(value)
        grid = [[grid[row][col] for col in col_order] for row in row_order]
        if transpose:
            grid = [list(row) for row in zip(*grid)]
        return _from_grid([
            [digit_map.get(cell, cell) if cell != "0" else "0" for cell in row]
            for row in grid
        ])

    return transform(puzzle_string), transform(solution_string)


def _take_permutation(code: int, size: int) -> tuple[list[int], int]:
    """Consume one factoradic permutation from a mixed-radix integer."""
    radix = math.factorial(size)
    rank, remainder = code % radix, code // radix
    available = list(range(size))
    permutation: list[int] = []
    for remaining in range(size, 0, -1):
        factor = math.factorial(remaining - 1)
        position, rank = divmod(rank, factor)
        permutation.append(available.pop(position))
    return permutation, remainder


def _to_grid(puzzle_string: str) -> list[list[str]]:
    return [list(puzzle_string[index:index + 9]) for index in range(0, 81, 9)]


def _from_grid(grid: list[list[str]]) -> str:
    return "".join("".join(row) for row in grid)
