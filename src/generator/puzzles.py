from __future__ import annotations

import hashlib
import json
import random
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
        rng = random.Random(self.seed + sample_index * 131)

        if scenario.edge_case == "malformed_input":
            return self._build_malformed_variant(parent, sample_index)
        if scenario.edge_case == "invalid_board":
            return self._build_invalid_variant(parent, sample_index)
        if scenario.edge_case == "unsolvable_board":
            return self._build_unsolvable_variant(parent, sample_index)
        if scenario.edge_case == "ambiguous_board":
            return self._build_ambiguous_variant(parent, sample_index)
        return self._build_transformed_variant(parent, sample_index, rng)

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

    def _build_transformed_variant(self, parent: PuzzleRecord, sample_index: int, rng: random.Random) -> PuzzleRecord:
        operations = [
            "identity",
            "digit_relabel",
            "swap_rows_within_band",
            "swap_cols_within_stack",
            "swap_bands",
            "swap_stacks",
            "rotate_90",
            "reflect_horizontal",
        ]
        operation = operations[sample_index % len(operations)]
        puzzle_string, solution_string = _apply_transformation(parent.puzzle, parent.solution, operation, rng)
        return self._make_variant(parent, puzzle_string, solution_string, operation)

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
            )
        )
    return puzzles


def _apply_transformation(
    puzzle_string: str,
    solution_string: str,
    operation: str,
    rng: random.Random,
) -> tuple[str, str]:
    puzzle_grid = _to_grid(puzzle_string)
    solution_grid = _to_grid(solution_string)

    if operation == "digit_relabel":
        mapping = {str(index): str(value) for index, value in enumerate(rng.sample(range(1, 10), 9), start=1)}
        return (
            _from_grid([[mapping.get(cell, cell) if cell != "0" else "0" for cell in row] for row in puzzle_grid]),
            _from_grid([[mapping[cell] for cell in row] for row in solution_grid]),
        )
    if operation == "swap_rows_within_band":
        band = rng.randint(0, 2)
        row_a, row_b = rng.sample(range(band * 3, band * 3 + 3), 2)
        puzzle_grid[row_a], puzzle_grid[row_b] = puzzle_grid[row_b], puzzle_grid[row_a]
        solution_grid[row_a], solution_grid[row_b] = solution_grid[row_b], solution_grid[row_a]
    elif operation == "swap_cols_within_stack":
        stack = rng.randint(0, 2)
        col_a, col_b = rng.sample(range(stack * 3, stack * 3 + 3), 2)
        puzzle_grid = _swap_cols(puzzle_grid, col_a, col_b)
        solution_grid = _swap_cols(solution_grid, col_a, col_b)
    elif operation == "swap_bands":
        band_a, band_b = rng.sample(range(3), 2)
        puzzle_grid = _swap_bands(puzzle_grid, band_a, band_b)
        solution_grid = _swap_bands(solution_grid, band_a, band_b)
    elif operation == "swap_stacks":
        stack_a, stack_b = rng.sample(range(3), 2)
        puzzle_grid = _swap_stacks(puzzle_grid, stack_a, stack_b)
        solution_grid = _swap_stacks(solution_grid, stack_a, stack_b)
    elif operation == "rotate_90":
        puzzle_grid = _rotate_90(puzzle_grid)
        solution_grid = _rotate_90(solution_grid)
    elif operation == "reflect_horizontal":
        puzzle_grid = list(reversed(puzzle_grid))
        solution_grid = list(reversed(solution_grid))

    return _from_grid(puzzle_grid), _from_grid(solution_grid)


def _to_grid(puzzle_string: str) -> list[list[str]]:
    return [list(puzzle_string[index:index + 9]) for index in range(0, 81, 9)]


def _from_grid(grid: list[list[str]]) -> str:
    return "".join("".join(row) for row in grid)


def _swap_cols(grid: list[list[str]], col_a: int, col_b: int) -> list[list[str]]:
    new_grid = [list(row) for row in grid]
    for row in new_grid:
        row[col_a], row[col_b] = row[col_b], row[col_a]
    return new_grid


def _swap_bands(grid: list[list[str]], band_a: int, band_b: int) -> list[list[str]]:
    new_grid = [list(row) for row in grid]
    start_a, start_b = band_a * 3, band_b * 3
    new_grid[start_a:start_a + 3], new_grid[start_b:start_b + 3] = (
        new_grid[start_b:start_b + 3],
        new_grid[start_a:start_a + 3],
    )
    return new_grid


def _swap_stacks(grid: list[list[str]], stack_a: int, stack_b: int) -> list[list[str]]:
    new_grid = [list(row) for row in grid]
    start_a, start_b = stack_a * 3, stack_b * 3
    for row in new_grid:
        row[start_a:start_a + 3], row[start_b:start_b + 3] = row[start_b:start_b + 3], row[start_a:start_a + 3]
    return new_grid


def _rotate_90(grid: list[list[str]]) -> list[list[str]]:
    return [list(row) for row in zip(*grid[::-1])]
