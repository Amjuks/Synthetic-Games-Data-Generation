from __future__ import annotations

import hashlib
import io
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import chess
import chess.engine
import chess.pgn
import chess.syzygy
import requests

from .models import PuzzleRecord, Scenario
from .chess_backends import ChessBackendManager


STARTING_FEN = chess.STARTING_FEN
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def split_for_lineage(lineage_id: str) -> str:
    """Assign every descendant of a lineage to the same stable data split."""
    bucket = int(hashlib.sha256(lineage_id.encode("utf-8")).hexdigest()[:8], 16) % 10
    return "train" if bucket < 8 else "validation" if bucket == 8 else "test"


def render_position(board: chess.Board) -> str:
    return f"FEN: {board.fen(en_passant='fen')}\n\n{board.unicode(borders=True)}"


def _status_names(status: chess.Status) -> list[str]:
    if status == chess.STATUS_VALID:
        return []
    names: list[str] = []
    for name in dir(chess):
        if not name.startswith("STATUS_") or name == "STATUS_VALID":
            continue
        value = getattr(chess, name)
        if isinstance(value, int) and status & value:
            names.append(name.removeprefix("STATUS_").lower())
    return sorted(set(names))


def _outcome(board: chess.Board, *, claim_draw: bool = True) -> dict[str, Any] | None:
    outcome = board.outcome(claim_draw=claim_draw)
    if outcome is None:
        return None
    return {
        "result": outcome.result(),
        "winner": "white" if outcome.winner else "black" if outcome.winner is not None else None,
        "termination": outcome.termination.name.lower(),
    }


def _position_status(board: chess.Board) -> dict[str, Any]:
    return {
        "valid": board.is_valid(),
        "side_to_move": "white" if board.turn else "black",
        "in_check": board.is_check(),
        "checkmate": board.is_checkmate(),
        "stalemate": board.is_stalemate(),
        "insufficient_material": board.is_insufficient_material(),
        "seventyfive_move_draw": board.is_seventyfive_moves(),
        "fivefold_repetition": board.is_fivefold_repetition(),
        "can_claim_fifty_moves": board.can_claim_fifty_moves(),
        "can_claim_threefold_repetition": board.can_claim_threefold_repetition(),
        "game_over": board.is_game_over(claim_draw=True),
        "outcome": _outcome(board),
    }


class ChessToolkit:
    """Deterministic chess operations used as dataset-generation tools."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        chess_config = self.config.get("chess", self.config)
        self.engine_path = os.getenv("CHESS_ENGINE_PATH") or chess_config.get("engine_path")
        self.engine_depth = int(chess_config.get("engine_depth", 12))
        self.backend_manager = ChessBackendManager(self.config)
        self.tablebase_path = os.getenv("CHESS_TABLEBASE_PATH") or chess_config.get("tablebase_path")
        self.tablebase_url = chess_config.get("tablebase_url", "https://tablebase.lichess.ovh/standard")
        self.tablebase_online = bool(chess_config.get("tablebase_online", False))
        self.tablebase_retries = int(chess_config.get("tablebase_retries", 3))
        self.timeout = float(chess_config.get("tool_timeout", 10))

    def parse_fen(self, fen: str) -> dict[str, Any]:
        if not isinstance(fen, str):
            return {"valid": False, "format": "fen", "errors": ["FEN must be a string."], "fen": fen}
        parts = fen.split()
        if len(parts) != 6:
            return {
                "valid": False,
                "format": "fen",
                "errors": [f"A complete FEN must contain exactly 6 fields; received {len(parts)}."],
                "fen": fen,
                "provided_field_count": len(parts),
                "missing_fields": [
                    name for name in (
                        "piece_placement",
                        "side_to_move",
                        "castling_rights",
                        "en_passant_target",
                        "halfmove_clock",
                        "fullmove_number",
                    )[len(parts):]
                ],
            }
        try:
            board = chess.Board(fen)
        except (ValueError, IndexError) as exc:
            return {"valid": False, "format": "fen", "errors": [str(exc)], "fen": fen}
        status = board.status()
        return {
            "valid": status == chess.STATUS_VALID,
            "format": "fen",
            "errors": _status_names(status),
            "fen": board.fen(en_passant="fen"),
            "fields": {
                "piece_placement": parts[0],
                "side_to_move": "white" if board.turn else "black",
                "castling_rights": parts[2],
                "en_passant_target": parts[3],
                "halfmove_clock": board.halfmove_clock,
                "fullmove_number": board.fullmove_number,
            },
        }

    def parse_pgn(self, pgn_text: str) -> dict[str, Any]:
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
        except (ValueError, IndexError) as exc:
            return {"valid": False, "format": "pgn", "errors": [str(exc)]}
        if game is None:
            return {"valid": False, "format": "pgn", "errors": ["No PGN game found."]}
        board = game.board()
        moves: list[dict[str, str]] = []
        try:
            for move in game.mainline_moves():
                san = board.san(move)
                moves.append({"san": san, "uci": move.uci()})
                board.push(move)
        except (ValueError, AssertionError) as exc:
            return {"valid": False, "format": "pgn", "headers": dict(game.headers), "moves": moves, "errors": [str(exc)]}
        errors = [str(error) for error in game.errors]
        return {
            "valid": not errors and board.is_valid(),
            "format": "pgn",
            "headers": dict(game.headers),
            "moves": moves,
            "initial_fen": game.board().fen(en_passant="fen"),
            "final_fen": board.fen(en_passant="fen"),
            "position_status": _position_status(board),
            "errors": errors + _status_names(board.status()),
        }

    def parse_move(self, fen: str, notation: str, notation_format: str = "auto") -> dict[str, Any]:
        parsed = self.parse_fen(fen)
        if not parsed["valid"]:
            return {"valid": False, "errors": parsed["errors"], "notation": notation}
        board = chess.Board(fen)
        try:
            if notation_format == "uci" or (notation_format == "auto" and _looks_like_uci(notation)):
                move = chess.Move.from_uci(notation.strip())
                if move not in board.legal_moves:
                    raise ValueError(f"Illegal UCI move in this position: {notation}")
            else:
                move = board.parse_san(notation.strip())
        except (ValueError, AssertionError) as exc:
            return {"valid": False, "errors": [str(exc)], "notation": notation}
        return {
            "valid": True,
            "san": board.san(move),
            "uci": move.uci(),
            "is_capture": board.is_capture(move),
            "is_castling": board.is_castling(move),
            "is_en_passant": board.is_en_passant(move),
            "promotion": chess.piece_name(move.promotion) if move.promotion else None,
        }

    def reconstruct(
        self,
        *,
        pgn: str | None = None,
        moves: Iterable[str] | None = None,
        initial_fen: str = STARTING_FEN,
        notation_format: str = "san",
    ) -> dict[str, Any]:
        if pgn is not None:
            parsed = self.parse_pgn(pgn)
            if not parsed["valid"]:
                return parsed
            game = chess.pgn.read_game(io.StringIO(pgn))
            assert game is not None
            board = game.board()
            trace = [{"ply": 0, "fen": board.fen(en_passant="fen"), "san": None, "uci": None}]
            for ply, move in enumerate(game.mainline_moves(), 1):
                san = board.san(move)
                board.push(move)
                trace.append({"ply": ply, "san": san, "uci": move.uci(), "fen": board.fen(en_passant="fen")})
            return {**parsed, "state_trace": trace}

        fen_result = self.parse_fen(initial_fen)
        if not fen_result["valid"]:
            return {"valid": False, "errors": fen_result["errors"], "state_trace": []}
        board = chess.Board(initial_fen)
        trace = [{"ply": 0, "fen": board.fen(en_passant="fen"), "san": None, "uci": None}]
        for ply, notation in enumerate(moves or [], 1):
            parsed = self.parse_move(board.fen(en_passant="fen"), notation, notation_format)
            if not parsed["valid"]:
                return {"valid": False, "errors": parsed["errors"], "failed_ply": ply, "state_trace": trace}
            move = chess.Move.from_uci(parsed["uci"])
            board.push(move)
            trace.append({"ply": ply, "san": parsed["san"], "uci": parsed["uci"], "fen": board.fen(en_passant="fen")})
        return {"valid": True, "errors": [], "final_fen": board.fen(en_passant="fen"), "state_trace": trace}

    def legal_moves(self, fen: str) -> dict[str, Any]:
        parsed = self.parse_fen(fen)
        if not parsed["valid"]:
            return {"valid": False, "errors": parsed["errors"], "moves": []}
        board = chess.Board(fen)
        moves = sorted(
            ({"san": board.san(move), "uci": move.uci()} for move in board.legal_moves),
            key=lambda row: row["uci"],
        )
        return {"valid": True, "count": len(moves), "moves": moves}

    def apply_move(self, fen: str, notation: str, notation_format: str = "auto") -> dict[str, Any]:
        parsed = self.parse_move(fen, notation, notation_format)
        if not parsed["valid"]:
            return parsed
        board = chess.Board(fen)
        board.push(chess.Move.from_uci(parsed["uci"]))
        return {
            **parsed,
            "fen_before": chess.Board(fen).fen(en_passant="fen"),
            "fen_after": board.fen(en_passant="fen"),
            "position_status": self.position_status(board.fen(en_passant="fen")),
        }

    def undo_moves(self, initial_fen: str, moves: Iterable[str], count: int = 1, notation_format: str = "san") -> dict[str, Any]:
        reconstructed = self.reconstruct(initial_fen=initial_fen, moves=moves, notation_format=notation_format)
        if not reconstructed["valid"]:
            return reconstructed
        trace = reconstructed["state_trace"]
        keep = max(0, len(trace) - 1 - max(0, count))
        return {"valid": True, "undone": min(count, len(trace) - 1), "fen": trace[keep]["fen"], "state_trace": trace[: keep + 1]}

    def convert_move(self, fen: str, notation: str, from_format: str = "auto") -> dict[str, Any]:
        return self.parse_move(fen, notation, from_format)

    def position_status(self, fen: str) -> dict[str, Any]:
        parsed = self.parse_fen(fen)
        if not parsed["valid"]:
            return {"valid": False, "errors": parsed["errors"]}
        board = chess.Board(fen)
        return _position_status(board)

    def history_status(
        self,
        *,
        pgn: str | None = None,
        moves: Iterable[str] | None = None,
        initial_fen: str = STARTING_FEN,
        notation_format: str = "san",
    ) -> dict[str, Any]:
        """Detect repetition and claimable draws while retaining the move stack."""
        if pgn is not None:
            try:
                game = chess.pgn.read_game(io.StringIO(pgn))
            except (ValueError, IndexError) as exc:
                return {"valid": False, "errors": [str(exc)]}
            if game is None:
                return {"valid": False, "errors": ["No PGN game found."]}
            board = game.board()
            try:
                for move in game.mainline_moves():
                    board.push(move)
            except (ValueError, AssertionError) as exc:
                return {"valid": False, "errors": [str(exc)]}
            return _position_status(board)
        rebuilt = self.reconstruct(initial_fen=initial_fen, moves=moves, notation_format=notation_format)
        if not rebuilt["valid"]:
            return rebuilt
        board = chess.Board(initial_fen)
        for row in rebuilt["state_trace"][1:]:
            board.push(chess.Move.from_uci(row["uci"]))
        return _position_status(board)

    def inspect(self, fen: str, squares: Iterable[str] | None = None) -> dict[str, Any]:
        parsed = self.parse_fen(fen)
        if not parsed["valid"]:
            return {"valid": False, "errors": parsed["errors"]}
        board = chess.Board(fen)
        requested = list(squares or [])
        if not requested:
            requested = [chess.square_name(square) for square in chess.scan_forward(board.occupied)]
        square_rows: dict[str, Any] = {}
        for name in requested:
            try:
                square = chess.parse_square(name)
            except ValueError:
                square_rows[name] = {"error": "invalid square"}
                continue
            square_rows[name] = {
                "piece": board.piece_at(square).symbol() if board.piece_at(square) else None,
                "attacked_by_white": sorted(chess.square_name(s) for s in board.attackers(chess.WHITE, square)),
                "attacked_by_black": sorted(chess.square_name(s) for s in board.attackers(chess.BLACK, square)),
                "defenders": sorted(
                    chess.square_name(s)
                    for s in board.attackers(board.color_at(square), square)
                ) if board.piece_at(square) else [],
                "white_piece_pinned": bool(board.piece_at(square) and board.color_at(square) == chess.WHITE and board.is_pinned(chess.WHITE, square)),
                "black_piece_pinned": bool(board.piece_at(square) and board.color_at(square) == chess.BLACK and board.is_pinned(chess.BLACK, square)),
            }
        checks: list[dict[str, str]] = []
        captures: list[dict[str, str]] = []
        for move in board.legal_moves:
            row = {"san": board.san(move), "uci": move.uci()}
            if board.gives_check(move):
                checks.append(row)
            if board.is_capture(move):
                captures.append(row)
        material = {
            color_name: {
                chess.piece_name(piece_type): len(board.pieces(piece_type, color))
                for piece_type in PIECE_VALUES
            }
            for color_name, color in (("white", chess.WHITE), ("black", chess.BLACK))
        }
        material["balance_white_minus_black"] = sum(
            PIECE_VALUES[piece_type] * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
            for piece_type in PIECE_VALUES
        )
        return {
            "valid": True,
            "squares": square_rows,
            "checks": checks,
            "captures": captures,
            "material": material,
            "tactical_features": self._tactical_features(board),
        }

    def _tactical_features(self, board: chess.Board) -> dict[str, Any]:
        pinned = []
        for square in chess.scan_forward(board.occupied):
            color = board.color_at(square)
            if color is not None and board.is_pinned(color, square):
                pinned.append(chess.square_name(square))
        forks: list[dict[str, Any]] = []
        discovered_checks: list[dict[str, str]] = []
        sacrifice_candidates: list[dict[str, Any]] = []
        skewer_candidates: list[dict[str, Any]] = []
        forcing_moves: list[dict[str, str]] = []
        for move in board.legal_moves:
            san = board.san(move)
            mover_type = board.piece_type_at(move.from_square)
            captured_type = board.piece_type_at(move.to_square)
            copy = board.copy(stack=False)
            copy.push(move)
            attacked = []
            for target in copy.attacks(move.to_square):
                piece = copy.piece_at(target)
                if piece and piece.color != copy.color_at(move.to_square) and piece.piece_type != chess.PAWN:
                    attacked.append({"square": chess.square_name(target), "piece": piece.symbol()})
            if len(attacked) >= 2:
                forks.append({"san": san, "uci": move.uci(), "targets": attacked})
            if board.gives_check(move) or board.is_capture(move) or move.promotion:
                forcing_moves.append({"san": san, "uci": move.uci()})
            if board.gives_check(move):
                enemy_king = copy.king(copy.turn)
                if enemy_king is not None and move.to_square not in copy.attackers(not copy.turn, enemy_king):
                    discovered_checks.append({"san": san, "uci": move.uci()})
                skewered = self._piece_behind_king(copy, move.to_square, enemy_king)
                if skewered is not None:
                    skewer_candidates.append({"san": san, "uci": move.uci(), "piece_behind_king": chess.square_name(skewered)})
            if mover_type and copy.is_attacked_by(copy.turn, move.to_square):
                mover_value = PIECE_VALUES[mover_type]
                captured_value = PIECE_VALUES.get(captured_type or chess.KING, 0)
                if mover_value > captured_value and (board.is_capture(move) or board.gives_check(move)):
                    sacrifice_candidates.append({"san": san, "uci": move.uci(), "material_risk": mover_value - captured_value})
        return {
            "pinned_pieces": pinned,
            "fork_candidates": forks[:20],
            "forcing_move_threats": forcing_moves[:30],
            "skewer_candidates": skewer_candidates[:20],
            "discovered_check_candidates": discovered_checks,
            "sacrifice_candidates_unverified": sacrifice_candidates[:20],
            "note": "Candidates describe geometry/legality only; tactical soundness requires engine verification.",
        }

    @staticmethod
    def _piece_behind_king(board: chess.Board, attacker: chess.Square, king: chess.Square) -> chess.Square | None:
        piece = board.piece_at(attacker)
        if piece is None or piece.piece_type not in {chess.BISHOP, chess.ROOK, chess.QUEEN}:
            return None
        file_delta = chess.square_file(king) - chess.square_file(attacker)
        rank_delta = chess.square_rank(king) - chess.square_rank(attacker)
        if file_delta == 0:
            step_file, step_rank = 0, 1 if rank_delta > 0 else -1
        elif rank_delta == 0:
            step_file, step_rank = 1 if file_delta > 0 else -1, 0
        elif abs(file_delta) == abs(rank_delta):
            step_file, step_rank = 1 if file_delta > 0 else -1, 1 if rank_delta > 0 else -1
        else:
            return None
        current_file = chess.square_file(king) + step_file
        current_rank = chess.square_rank(king) + step_rank
        while 0 <= current_file < 8 and 0 <= current_rank < 8:
            square = chess.square(current_file, current_rank)
            target = board.piece_at(square)
            if target:
                return square if target.color == board.turn and target.piece_type != chess.PAWN else None
            current_file += step_file
            current_rank += step_rank
        return None

    def engine_analysis(self, fen: str, *, multipv: int = 3, depth: int | None = None) -> dict[str, Any]:
        parsed = self.parse_fen(fen)
        if not parsed["valid"]:
            return {"available": False, "verified": False, "errors": parsed["errors"]}
        backend = self.backend_manager.ensure_engine()
        if not backend.get("available"):
            return backend
        engine_path = backend["engine_path"]
        board = chess.Board(fen)
        try:
            with chess.engine.SimpleEngine.popen_uci(engine_path, timeout=self.timeout) as engine:
                infos = engine.analyse(board, chess.engine.Limit(depth=depth or self.engine_depth), multipv=max(1, multipv))
        except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError, TimeoutError) as exc:
            return {"available": False, "verified": False, "reason": str(exc)}
        if isinstance(infos, dict):
            infos = [infos]
        lines: list[dict[str, Any]] = []
        for info in infos:
            pv = info.get("pv", [])
            copy = board.copy()
            san_line: list[str] = []
            for move in pv:
                san_line.append(copy.san(move))
                copy.push(move)
            score = info.get("score")
            pov = score.pov(board.turn) if score is not None else None
            lines.append({
                "depth": info.get("depth"),
                "score_cp": pov.score(mate_score=100000) if pov else None,
                "mate_in": pov.mate() if pov else None,
                "pv_san": san_line,
                "pv_uci": [move.uci() for move in pv],
            })
        return {**backend, "available": True, "verified": True, "engine_path": str(engine_path), "lines": lines, "evaluation_is_estimate": True}

    def tablebase_lookup(self, fen: str) -> dict[str, Any]:
        parsed = self.parse_fen(fen)
        if not parsed["valid"]:
            return {"available": False, "verified": False, "errors": parsed["errors"]}
        board = chess.Board(fen)
        pieces = chess.popcount(board.occupied)
        if pieces > 7:
            return {"available": False, "verified": False, "applicable": False, "piece_count": pieces, "reason": "Syzygy tablebases support at most seven pieces."}
        if self.tablebase_path and str(self.tablebase_path).lower() != "auto" and Path(self.tablebase_path).is_dir():
            try:
                with chess.syzygy.open_tablebase(self.tablebase_path) as tablebase:
                    return {"available": True, "verified": True, "applicable": True, "piece_count": pieces, "source": "local_syzygy", "wdl": tablebase.probe_wdl(board), "dtz": tablebase.probe_dtz(board)}
            except (OSError, KeyError) as exc:
                return {"available": False, "verified": False, "applicable": True, "piece_count": pieces, "reason": str(exc)}
        if self.tablebase_online:
            last_error = "unknown tablebase error"
            for attempt in range(max(1, self.tablebase_retries)):
                try:
                    response = requests.get(
                        self.tablebase_url,
                        params={"fen": board.fen(en_passant="fen")},
                        headers={"User-Agent": "synthetic-chess-dataset/0.1"},
                        timeout=self.timeout,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not payload.get("category"):
                        raise ValueError("Tablebase response did not include a result category.")
                    return {"available": True, "verified": True, "applicable": True, "piece_count": pieces, "source": "lichess_syzygy", "category": payload.get("category"), "dtz": payload.get("dtz"), "dtm": payload.get("dtm"), "checkmate": payload.get("checkmate"), "stalemate": payload.get("stalemate"), "moves": payload.get("moves", [])[:10]}
                except (requests.RequestException, ValueError) as exc:
                    last_error = str(exc)
                    if attempt + 1 < max(1, self.tablebase_retries):
                        time.sleep(0.25 * (2 ** attempt))
            return {"available": False, "verified": False, "applicable": True, "piece_count": pieces, "reason": last_error}
        return {"available": False, "verified": False, "applicable": True, "piece_count": pieces, "reason": "No local tablebase is configured and online lookup is disabled."}

    def verify_backends(self) -> dict[str, Any]:
        engine = self.backend_manager.healthcheck()
        tablebase = self.tablebase_lookup("7k/8/6K1/8/8/8/6Q1/8 w - - 0 1")
        return {
            "verified": bool(engine.get("verified") and tablebase.get("verified")),
            "engine": engine,
            "tablebase": tablebase,
        }


@dataclass(frozen=True)
class _Lineage:
    lineage_id: str
    initial_fen: str
    pgn: str
    final_fen: str
    moves_san: tuple[str, ...]
    state_trace: tuple[dict[str, Any], ...]


class ChessProblemManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.toolkit = ChessToolkit(config)
        self.seed = int(config.get("generation", {}).get("random_seed", 17))
        output_path = Path(config["output_path"])
        puzzle_config = config.get("puzzles", {})
        self.bank_path = output_path / puzzle_config.get("persistent_bank_filename", "chess_position_bank.jsonl")
        self.usage_path = output_path / puzzle_config.get("usage_stats_filename", "chess_position_usage.json")
        self.lineages = self._build_lineages()
        self.base_bank = self._build_bank()
        self._ensure_bank()
        self.usage_stats = self._load_usage_stats()

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        if scenario.edge_case != "none":
            return self._edge_record(scenario, sample_index)
        input_type = "game_history" if sample_index % 2 == 0 else "fen"
        candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and p.difficulty == scenario.difficulty]
        if scenario.task_category == "endgame_analysis":
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and p.metadata.get("piece_count", 99) <= 7]
        elif scenario.task_category in {"special_move", "draw_rules", "terminal_state"}:
            requested_theme = scenario.metadata.get("rules_theme")
            themed = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and p.metadata.get("special_theme") == requested_theme]
            if not themed:
                themed = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and p.metadata.get("special_theme")]
            if themed:
                candidates = themed
        if not candidates:
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type]
        candidates.sort(key=lambda p: (self.usage_stats.get(p.puzzle_id, 0), p.puzzle_id))
        return candidates[sample_index % min(6, len(candidates))]

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        for key in {puzzle.puzzle_id, puzzle.parent_puzzle_id or puzzle.puzzle_id}:
            self.usage_stats[key] = self.usage_stats.get(key, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _build_lineages(self) -> list[_Lineage]:
        lineages: list[_Lineage] = []
        for index in range(32):
            rng = random.Random(self.seed * 1009 + index * 7919)
            board = chess.Board()
            sans: list[str] = []
            trace: list[dict[str, Any]] = [{"ply": 0, "san": None, "uci": None, "fen": board.fen(en_passant="fen")}]
            target_plies = 10 + (index % 5) * 6
            while len(sans) < target_plies and not board.is_game_over():
                legal = list(board.legal_moves)
                legal.sort(key=lambda move: move.uci())
                weighted: list[chess.Move] = []
                for move in legal:
                    weight = 1 + 4 * board.is_capture(move) + 3 * board.gives_check(move) + 2 * board.is_castling(move)
                    weighted.extend([move] * weight)
                move = rng.choice(weighted)
                san = board.san(move)
                board.push(move)
                sans.append(san)
                trace.append({"ply": len(sans), "san": san, "uci": move.uci(), "fen": board.fen(en_passant="fen")})
            lineage_id = f"chess-generated-{self.seed}-{index:03d}"
            game = chess.pgn.Game()
            game.headers.update({"Event": "Synthetic validated game", "Site": "Generator", "Round": str(index + 1), "White": f"Synthetic-{index}-W", "Black": f"Synthetic-{index}-B", "Result": "*"})
            node = game
            replay = chess.Board()
            for san in sans:
                move = replay.parse_san(san)
                replay.push(move)
                node = node.add_variation(move)
            exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
            lineages.append(_Lineage(lineage_id, STARTING_FEN, game.accept(exporter), board.fen(en_passant="fen"), tuple(sans), tuple(trace)))
        special_positions = [
            ("promotion", "7k/P7/6K1/8/8/8/8/8 w - - 0 1", []),
            ("castling", "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", []),
            ("en_passant", "rnbqkbnr/1pp1pppp/p7/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3", []),
            ("fifty_move_rule", "8/8/8/8/8/6k1/4R3/6K1 w - - 100 75", []),
            ("stalemate", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", []),
            ("insufficient_material", "8/8/8/8/8/8/4K3/7k w - - 0 1", []),
            ("checkmate", "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", []),
            ("rook_endgame", "8/7k/8/8/8/8/4R3/6K1 w - - 0 1", []),
            ("repetition", STARTING_FEN, ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1", "Ng8"]),
        ]
        for offset, (theme, initial_fen, preset_moves) in enumerate(special_positions):
            board = chess.Board(initial_fen)
            initial = board.fen(en_passant="fen")
            trace = [{"ply": 0, "san": None, "uci": None, "fen": initial}]
            sans: list[str] = []
            for ply, notation in enumerate(preset_moves):
                move = board.parse_san(notation)
                san = board.san(move)
                board.push(move)
                sans.append(san)
                trace.append({"ply": ply + 1, "san": san, "uci": move.uci(), "fen": board.fen(en_passant="fen")})
            game = chess.pgn.Game()
            game.setup(chess.Board(initial))
            game.headers.update({"Event": "Synthetic validated special position", "Site": "Generator", "Round": str(offset + 1), "White": f"Synthetic-special-{offset}-W", "Black": f"Synthetic-special-{offset}-B", "Result": "*"})
            node = game
            replay = chess.Board(initial)
            for san in sans:
                move = replay.parse_san(san)
                node = node.add_variation(move)
                replay.push(move)
            exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
            lineage_id = f"chess-special-{self.seed}-{theme}"
            lineages.append(_Lineage(lineage_id, initial, game.accept(exporter), board.fen(en_passant="fen"), tuple(sans), tuple(trace)))
        return lineages

    def _build_bank(self) -> list[PuzzleRecord]:
        records: list[PuzzleRecord] = []
        for index, lineage in enumerate(self.lineages):
            difficulty = ("easy", "medium", "hard", "expert")[index % 4]
            for input_type in ("game_history", "fen"):
                puzzle_id = hashlib.sha1(f"{lineage.lineage_id}|{input_type}".encode()).hexdigest()[:16]
                if input_type == "game_history":
                    payload = {"input_type": input_type, "pgn": lineage.pgn, "current_fen": lineage.final_fen}
                    rendered = f"PGN:\n{lineage.pgn}\nCurrent FEN: {lineage.final_fen}"
                    notation_format = "pgn_san"
                else:
                    payload = {"input_type": input_type, "fen": lineage.final_fen}
                    rendered = render_position(chess.Board(lineage.final_fen))
                    notation_format = "fen"
                truth = self._ground_truth(lineage, input_type)
                metadata = {
                    "input_type": input_type,
                    "notation_format": notation_format,
                    "side_to_move": truth["side_to_move"],
                    "lineage_id": lineage.lineage_id,
                    "dataset_split": split_for_lineage(lineage.lineage_id),
                    "verification_status": "verified",
                    "source": "programmatically_generated_legal_play",
                    "ply_count": len(lineage.moves_san),
                    "piece_count": chess.popcount(chess.Board(lineage.final_fen).occupied),
                    "special_theme": lineage.lineage_id.rsplit("-", 1)[-1] if lineage.lineage_id.startswith("chess-special-") else None,
                }
                records.append(PuzzleRecord(puzzle_id=puzzle_id, puzzle=json.dumps(payload, sort_keys=True), solution=lineage.final_fen, difficulty=difficulty, num_clues=len(lineage.moves_san), required_strategies=["legal_move_reasoning", "position_interpretation"], unique_solution_status=True, source="programmatic", canonical_signature=lineage.lineage_id, usage_count=0, parent_puzzle_id=lineage.lineage_id, transformation=input_type, rendered_board=rendered, ground_truth=truth, metadata=metadata))
        return records

    def _ground_truth(self, lineage: _Lineage, input_type: str) -> dict[str, Any]:
        board = chess.Board(lineage.final_fen)
        special_theme = lineage.lineage_id.rsplit("-", 1)[-1] if lineage.lineage_id.startswith("chess-special-") else None
        continuation = self._validated_continuation(board, 6, special_theme=special_theme)
        return {
            "validity_status": "valid",
            "verification_status": "verified",
            "input_type": input_type,
            "initial_fen": lineage.initial_fen,
            "current_fen": lineage.final_fen,
            "fen_fields": self.toolkit.parse_fen(lineage.final_fen)["fields"],
            "side_to_move": "white" if board.turn else "black",
            "moves_san": list(lineage.moves_san),
            "history_state_trace": list(lineage.state_trace),
            "validated_continuation": continuation,
            "legal_moves": self.toolkit.legal_moves(lineage.final_fen),
            "position_status": self.toolkit.history_status(pgn=lineage.pgn) if input_type == "game_history" else self.toolkit.position_status(lineage.final_fen),
            "inspection": self.toolkit.inspect(lineage.final_fen),
        }

    def _validated_continuation(self, board: chess.Board, plies: int, *, special_theme: str | None = None) -> list[dict[str, Any]]:
        copy = board.copy(stack=True)
        rows: list[dict[str, Any]] = []
        for ply in range(1, plies + 1):
            if copy.is_game_over():
                break
            def move_priority(move: chess.Move) -> tuple[Any, ...]:
                preferred = (
                    (special_theme == "castling" and copy.is_castling(move))
                    or (special_theme == "en_passant" and copy.is_en_passant(move))
                    or (special_theme == "promotion" and move.promotion is not None)
                )
                return (not preferred, not copy.gives_check(move), not copy.is_capture(move), move.uci())

            moves = sorted(copy.legal_moves, key=move_priority)
            move = moves[0]
            before = copy.fen(en_passant="fen")
            san = copy.san(move)
            copy.push(move)
            rows.append({"ply": ply, "fen_before": before, "san": san, "uci": move.uci(), "fen_after": copy.fen(en_passant="fen")})
        return rows

    def _edge_record(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        base = self.base_bank[sample_index % len(self.base_bank)]
        lineage_id = base.parent_puzzle_id or base.puzzle_id
        edge = scenario.edge_case
        if edge == "malformed_input":
            payload, rendered, status = {"input_type": "fen", "fen": "8/8/8 broken"}, "FEN: 8/8/8 broken", "malformed"
        elif edge == "illegal_move":
            payload, rendered, status = {"input_type": "game_history", "initial_fen": STARTING_FEN, "moves": ["e4", "e5", "Ke3"]}, "Moves: 1. e4 e5 2. Ke3", "illegal"
        elif edge == "ambiguous_input":
            payload, rendered, status = {"input_type": "move_fragment", "moves": ["Nf3"], "initial_position": None}, "Move fragment: Nf3\nPosition: unspecified", "incomplete"
        elif edge == "contradictory_input":
            fen = base.ground_truth["current_fen"]
            claimed = "black" if base.ground_truth["side_to_move"] == "white" else "white"
            payload, rendered, status = {"input_type": "fen", "fen": fen, "claimed_side_to_move": claimed}, f"FEN: {fen}\nUser claims: {claimed} to move", "contradictory"
        elif edge == "invalid_position":
            payload, rendered, status = {"input_type": "fen", "fen": "8/8/8/8/8/8/8/8 w - - 0 1"}, "FEN: 8/8/8/8/8/8/8/8 w - - 0 1", "invalid"
        else:
            payload, rendered, status = {"input_type": "fen_fragment", "fen": "8/8/8/8/8/8/4K3/7k w"}, "Incomplete FEN: 8/8/8/8/8/8/4K3/7k w", "incomplete"
        puzzle_id = hashlib.sha1(f"{lineage_id}|{edge}|{sample_index}".encode()).hexdigest()[:16]
        truth = {"validity_status": status, "verification_status": "verified_invalid_or_incomplete", "input_type": payload["input_type"], "current_fen": None, "side_to_move": None, "errors": [f"Input is {status}; do not infer a complete board."]}
        return PuzzleRecord(puzzle_id=puzzle_id, puzzle=json.dumps(payload, sort_keys=True), solution="", difficulty=scenario.difficulty, num_clues=0, required_strategies=["input_validation"], unique_solution_status=False, source="programmatic_edge_case", canonical_signature=lineage_id, usage_count=0, parent_puzzle_id=lineage_id, transformation=edge, rendered_board=rendered, ground_truth=truth, metadata={"input_type": payload["input_type"], "notation_format": "unknown", "side_to_move": None, "lineage_id": lineage_id, "dataset_split": split_for_lineage(lineage_id), "edge_case_kind": edge, "verification_status": "verified_invalid_or_incomplete"})

    def _ensure_bank(self) -> None:
        if self.bank_path.exists():
            return
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        with self.bank_path.open("w", encoding="utf-8") as handle:
            for record in self.base_bank:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def _load_usage_stats(self) -> dict[str, int]:
        if not self.usage_path.exists():
            return {}
        return json.loads(self.usage_path.read_text(encoding="utf-8"))


def _looks_like_uci(value: str) -> bool:
    value = value.strip()
    return len(value) in {4, 5} and value[0] in chess.FILE_NAMES and value[2] in chess.FILE_NAMES and value[1] in chess.RANK_NAMES and value[3] in chess.RANK_NAMES
