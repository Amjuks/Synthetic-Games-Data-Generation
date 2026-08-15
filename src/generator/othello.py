from __future__ import annotations

import hashlib
import json
import random
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .models import PuzzleRecord, Scenario


SIZE = 8
DIRECTIONS = tuple((dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0))
WEIGHTS = (
    120, -25, 20, 5, 5, 20, -25, 120,
    -25, -45, -5, -5, -5, -5, -45, -25,
    20, -5, 15, 3, 3, 15, -5, 20,
    5, -5, 3, 3, 3, 3, -5, 5,
    5, -5, 3, 3, 3, 3, -5, 5,
    20, -5, 15, 3, 3, 15, -5, 20,
    -25, -45, -5, -5, -5, -5, -45, -25,
    120, -25, 20, 5, 5, 20, -25, 120,
)


def opponent(side: str) -> str:
    return "W" if side == "B" else "B"


def format_square(index: int) -> str:
    return f"{chr(97 + index % SIZE)}{index // SIZE + 1}"


def parse_square(square: str) -> int:
    if len(square) != 2 or square[0] not in "abcdefgh" or square[1] not in "12345678":
        raise ValueError(f"invalid Othello square: {square}")
    return (int(square[1]) - 1) * SIZE + ord(square[0]) - 97


def initial_board() -> str:
    board = ["."] * 64
    board[3 * 8 + 3], board[3 * 8 + 4] = "W", "B"
    board[4 * 8 + 3], board[4 * 8 + 4] = "B", "W"
    return "".join(board)


def canonical_state(board: str, side_to_move: str, consecutive_passes: int = 0) -> str:
    return json.dumps({"board": board, "side_to_move": side_to_move, "consecutive_passes": consecutive_passes}, sort_keys=True, separators=(",", ":"))


def parse_state(value: str) -> tuple[str, str, int]:
    payload = json.loads(value)
    return str(payload["board"]), str(payload["side_to_move"]), int(payload.get("consecutive_passes", 0))


def validate_state(board: str, side: str, passes: int = 0) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if len(board) != 64: errors.append({"type": "board_length", "expected": 64, "actual": len(board)})
    illegal = sorted(set(board) - {"B", "W", "."})
    if illegal: errors.append({"type": "board_symbols", "symbols": illegal})
    if side not in {"B", "W"}: errors.append({"type": "side_to_move", "value": side})
    if passes not in {0, 1, 2}: errors.append({"type": "consecutive_passes", "value": passes})
    if len(board) == 64 and not illegal and board.count("B") + board.count("W") < 4:
        errors.append({"type": "disc_count", "message": "A reachable Othello position has at least four discs."})
    return errors


def flips_for_move(board: str, side: str, index: int) -> tuple[int, ...]:
    if len(board) != 64 or not 0 <= index < 64 or board[index] != ".": return ()
    row, col = divmod(index, 8); other = opponent(side); flips: list[int] = []
    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc; line: list[int] = []
        while 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == other:
            line.append(r * 8 + c); r += dr; c += dc
        if line and 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == side:
            flips.extend(line)
    return tuple(flips)


def legal_moves(board: str, side: str) -> dict[str, list[str]]:
    return {format_square(i): [format_square(j) for j in flips] for i in range(64) if (flips := flips_for_move(board, side, i))}


def apply_move(board: str, side: str, move: str) -> tuple[str, str, int, list[str]]:
    moves = legal_moves(board, side)
    if move == "pass":
        if moves: raise ValueError("pass is illegal while a legal move exists")
        return board, opponent(side), 1, []
    if move not in moves: raise ValueError(f"illegal Othello move: {move}")
    mutable = list(board); index = parse_square(move); mutable[index] = side
    for square in moves[move]: mutable[parse_square(square)] = side
    next_board = "".join(mutable); other = opponent(side)
    if legal_moves(next_board, other): return next_board, other, 0, moves[move]
    if legal_moves(next_board, side): return next_board, side, 1, moves[move]
    return next_board, other, 2, moves[move]


def status(board: str, side: str, passes: int = 0) -> dict[str, Any]:
    moves = legal_moves(board, side) if not validate_state(board, side, passes) else {}
    other_moves = legal_moves(board, opponent(side)) if len(board) == 64 else {}
    terminal = not moves and not other_moves
    counts = {"B": board.count("B"), "W": board.count("W"), "empty": board.count(".")}
    winner = None if not terminal or counts["B"] == counts["W"] else ("B" if counts["B"] > counts["W"] else "W")
    return {"side_to_move": side, "legal_move_count": len(moves), "forced_pass": not terminal and not moves, "terminal": terminal, "winner": winner, "disc_counts": counts}


def positional_features(board: str, side: str) -> dict[str, Any]:
    corners = (0, 7, 56, 63); other = opponent(side)
    frontier = {side: 0, other: 0}
    for index, value in enumerate(board):
        if value not in frontier: continue
        row, col = divmod(index, 8)
        if any(0 <= row+dr < 8 and 0 <= col+dc < 8 and board[(row+dr)*8+col+dc] == "." for dr, dc in DIRECTIONS): frontier[value] += 1
    return {
        "corners": {side: sum(board[i] == side for i in corners), other: sum(board[i] == other for i in corners)},
        "edges": {side: sum(board[i] == side for i in range(64) if i // 8 in {0, 7} or i % 8 in {0, 7}), other: sum(board[i] == other for i in range(64) if i // 8 in {0, 7} or i % 8 in {0, 7})},
        "frontier_discs": frontier,
        "mobility": {side: len(legal_moves(board, side)), other: len(legal_moves(board, other))},
        "empty_parity": board.count(".") % 2,
    }


def _evaluate(board: str, root: str) -> int:
    other = opponent(root); state = status(board, root)
    if state["terminal"]:
        diff = board.count(root) - board.count(other)
        return (100000 + abs(diff)) * (1 if diff > 0 else -1 if diff < 0 else 0)
    positional = sum(WEIGHTS[i] * (1 if value == root else -1 if value == other else 0) for i, value in enumerate(board))
    mobility = 8 * (len(legal_moves(board, root)) - len(legal_moves(board, other)))
    frontier = positional_features(board, root)["frontier_discs"]
    return positional + mobility + 3 * (frontier[other] - frontier[root]) + board.count(root) - board.count(other)


def search_position(board: str, side: str, depth: int, *, exact: bool = False) -> dict[str, Any]:
    root = side; nodes = 0; cache: dict[tuple[str, str, int, bool], tuple[int, tuple[str, ...]]] = {}
    target_depth = board.count(".") + 2 if exact else depth

    def negamax(position: str, turn: str, remaining: int, alpha: int, beta: int, passed: bool) -> tuple[int, tuple[str, ...]]:
        nonlocal nodes
        nodes += 1; key = (position, turn, remaining, passed)
        if key in cache: return cache[key]
        moves = legal_moves(position, turn)
        if not moves:
            if passed or not legal_moves(position, opponent(turn)):
                result = (_evaluate(position, root), ())
            else:
                result = negamax(position, opponent(turn), remaining - 1, alpha, beta, True)
            cache[key] = result; return result
        if remaining <= 0:
            result = (_evaluate(position, root), ()); cache[key] = result; return result
        maximizing = turn == root; best_score = -10**9 if maximizing else 10**9; best_line: tuple[str, ...] = ()
        cutoff = False
        for move in sorted(moves):
            child, next_side, _, _ = apply_move(position, turn, move)
            score, line = negamax(child, next_side, remaining - 1, alpha, beta, False)
            if (maximizing and score > best_score) or (not maximizing and score < best_score): best_score, best_line = score, (move, *line)
            if maximizing: alpha = max(alpha, best_score)
            else: beta = min(beta, best_score)
            if beta <= alpha:
                cutoff = True
                break
        result = (best_score, best_line)
        # A cut-off value is only a bound, so do not reuse it as an exact
        # transposition result under a different alpha/beta window.
        if not cutoff:
            cache[key] = result
        return result

    comparisons = []
    for move in sorted(legal_moves(board, side)):
        child, next_side, _, flips = apply_move(board, side, move)
        score, line = negamax(child, next_side, target_depth - 1, -10**9, 10**9, False)
        comparisons.append({"move": move, "score": score, "flips": len(flips), "principal_variation": [move, *line]})
    comparisons.sort(key=lambda item: (-item["score"], item["move"]))
    best_score = comparisons[0]["score"] if comparisons else None
    best_moves = [item["move"] for item in comparisons if item["score"] == best_score]
    return {"best_move": best_moves[0] if best_moves else ("pass" if not status(board, side)["terminal"] else "terminal"), "best_moves": best_moves, "comparisons": comparisons, "analysis_depth": target_depth, "exact": exact, "nodes": nodes}


def render_board(board: str, side: str) -> str:
    lines = ["  a b c d e f g h"]
    lines.extend(f"{row + 1} " + " ".join(board[row*8:(row+1)*8]) for row in range(8))
    lines.append(f"Side to move: {'Black' if side == 'B' else 'White'}")
    return "\n".join(lines)


def transform_board(board: str, operation: str) -> str:
    def source(r: int, c: int) -> tuple[int, int]:
        if operation == "rotate_90": return 7-c, r
        if operation == "rotate_180": return 7-r, 7-c
        if operation == "reflect_horizontal": return 7-r, c
        return r, c
    return "".join(board[sr*8+sc] for r in range(8) for c in range(8) for sr, sc in [source(r, c)])


def build_ground_truth(board: str, side: str, passes: int, difficulty: str, *, mutation: dict[str, Any] | None = None) -> dict[str, Any]:
    violations = validate_state(board, side, passes)
    if violations:
        return {"validity_status": "invalid", "structural_violations": violations, "mutation": mutation or {}, "legal_moves": {}, "position_status": {}}
    state = status(board, side, passes); exact = difficulty == "expert" and board.count(".") <= 10
    depth = {"easy": 2, "medium": 3, "hard": 4, "expert": 4}[difficulty]
    analysis = search_position(board, side, depth, exact=exact) if not state["forced_pass"] and not state["terminal"] else {"best_move": "pass" if state["forced_pass"] else "terminal", "best_moves": [], "comparisons": [], "analysis_depth": 0, "exact": state["terminal"], "nodes": 0}
    return {"validity_status": "valid", "structural_violations": [], "board": board, "side_to_move": side, "consecutive_passes": passes, "legal_moves": legal_moves(board, side), "position_status": state, "disc_counts": state["disc_counts"], "positional_features": positional_features(board, side), "analysis": analysis, "recommended_move": analysis["best_move"], "unique_best_move": len(analysis["best_moves"]) == 1, "mutation": mutation or {}}


class OthelloPuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output = Path(config["output_path"]); puzzles = config.get("puzzles", {})
        self.bank_path = output / puzzles.get("persistent_bank_filename", "othello_position_bank.jsonl")
        self.usage_path = output / puzzles.get("usage_stats_filename", "othello_position_usage.json")
        self.usage_stats: dict[str, int] = {}; self.base_bank = self._build_bank(); self._ensure_bank(); self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        choices = [p for p in self.base_bank if p.difficulty == scenario.difficulty] or self.base_bank
        parent = choices[sample_index % len(choices)]
        if scenario.edge_case != "none": return self._edge_variant(parent, scenario.edge_case, sample_index)
        operation = ("identity", "rotate_90", "rotate_180", "reflect_horizontal")[sample_index % 4]
        if operation == "identity": return deepcopy(parent)
        board, side, passes = parse_state(parent.puzzle); board = transform_board(board, operation)
        return self._record(board, side, passes, parent.difficulty, parent.source, parent.puzzle_id, operation)

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
        bank = []
        targets = {"easy": (10, 14), "medium": (28, 34), "hard": (44, 48), "expert": (54, 56)}
        for difficulty, plies in targets.items():
            for offset, target in enumerate(plies):
                board, side, passes = initial_board(), "B", 0; rng = random.Random(7300 + target * 17 + offset)
                for _ in range(target):
                    moves = sorted(legal_moves(board, side))
                    if not moves:
                        if not legal_moves(board, opponent(side)): break
                        board, side, passes, _ = apply_move(board, side, "pass"); continue
                    weighted = sorted(moves, key=lambda move: (-WEIGHTS[parse_square(move)], move))[:max(1, min(4, len(moves)))]
                    board, side, passes, _ = apply_move(board, side, rng.choice(weighted))
                if difficulty == "expert":
                    while board.count(".") > 10 and not status(board, side, passes)["terminal"]:
                        moves = sorted(legal_moves(board, side))
                        board, side, passes, _ = apply_move(board, side, moves[0] if moves else "pass")
                bank.append(self._record(board, side, passes, difficulty, "generated", None, "identity"))
        return bank

    def _record(self, board: str, side: str, passes: int, difficulty: str, source: str, parent_id: str | None, transformation: str, mutation: dict[str, Any] | None = None) -> PuzzleRecord:
        puzzle = canonical_state(board, side, passes); truth = build_ground_truth(board, side, passes, difficulty, mutation=mutation)
        digest = hashlib.sha1(f"{puzzle}|{transformation}|{mutation}".encode()).hexdigest()[:16]
        solution = str(truth.get("recommended_move", "invalid"))
        return PuzzleRecord(f"othello_{digest}", puzzle, solution, difficulty, board.count("B") + board.count("W"), ["mobility", "corner_control", "frontier_management"], bool(truth.get("unique_best_move")), source, hashlib.sha256(puzzle.encode()).hexdigest(), self.usage_stats.get(f"othello_{digest}", 0), parent_puzzle_id=parent_id, transformation=transformation, rendered_board=render_board(board, side), ground_truth=truth, metadata={"size": 8, "side_to_move": side, "empty_count": board.count("."), "analysis_exact": truth.get("analysis", {}).get("exact", False)})

    def _edge_variant(self, parent: PuzzleRecord, edge: str, sample_index: int) -> PuzzleRecord:
        board, side, passes = parse_state(parent.puzzle)
        if edge == "malformed_input":
            record = self._record(board, side, passes, parent.difficulty, parent.source, parent.puzzle_id, f"malformed_{sample_index}", {"type": edge}); record.rendered_board = record.rendered_board[:40]; record.ground_truth["validity_status"] = "malformed"; return record
        if edge == "invalid_board":
            invalid = board[:-1] + "X"; truth = build_ground_truth(invalid, side, passes, parent.difficulty, mutation={"type": edge})
            puzzle = canonical_state(invalid, side, passes); return PuzzleRecord(f"othello_invalid_{sample_index}_{parent.puzzle_id}", puzzle, "invalid", parent.difficulty, 0, ["input_validation"], False, parent.source, hashlib.sha256(puzzle.encode()).hexdigest(), 0, parent_puzzle_id=parent.puzzle_id, transformation=f"invalid_{sample_index}", rendered_board="Invalid Othello board", ground_truth=truth, metadata={"size": 8, "edge_case_kind": edge})
        if edge in {"forced_pass", "terminal_position"}:
            special_board, special_side, special_passes = self._special_position(edge, sample_index)
            return self._record(special_board, special_side, special_passes, parent.difficulty, parent.source, parent.puzzle_id, f"{edge}_{sample_index}", {"type": edge})
        mutation = {"type": edge, "requested_move": "a1"} if edge == "illegal_move" else {"type": edge, "claimed_side_to_move": opponent(side)}
        return self._record(board, side, passes, parent.difficulty, parent.source, parent.puzzle_id, f"{edge}_{sample_index}", mutation)

    @staticmethod
    @lru_cache(maxsize=32)
    def _special_position(kind: str, seed: int) -> tuple[str, str, int]:
        board, side, passes = initial_board(), "B", 0; rng = random.Random(9100 + seed)
        for _ in range(200):
            moves = sorted(legal_moves(board, side))
            if not moves:
                other_moves = legal_moves(board, opponent(side))
                if kind == "forced_pass" and other_moves: return board, side, 0
                if not other_moves:
                    if kind == "forced_pass":
                        break
                    return board, side, 2
                board, side, passes, _ = apply_move(board, side, "pass")
            else:
                board, side, passes, _ = apply_move(board, side, rng.choice(moves))
            if kind == "terminal_position" and status(board, side, passes)["terminal"]: return board, side, 2
        if kind == "forced_pass":
            # White has no bracketing line; Black can play c1 across b1.
            return "BW." + "B" * 61, "W", 0
        return "B" * 64, "W", 2
