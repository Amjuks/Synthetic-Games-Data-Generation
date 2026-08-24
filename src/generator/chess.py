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


def canonical_state_signature(fen: str) -> str:
    """Identify a Chess state by legality-relevant FEN fields, excluding counters."""
    parts = fen.split()
    if len(parts) < 4:
        return hashlib.sha256(f"incomplete|{fen}".encode("utf-8")).hexdigest()
    return hashlib.sha256(" ".join(parts[:4]).encode("utf-8")).hexdigest()


def complexity_band(score: int) -> str:
    """Map a measured position score to the public four-band vocabulary."""
    return "easy" if score < 28 else "medium" if score < 45 else "hard" if score < 65 else "expert"


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
    snapshot_id: str
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
        self.catalog_version = str(puzzle_config.get("catalog_version", "chess-v2"))
        self.lineage_count = max(8, int(puzzle_config.get("lineage_count", 256)))
        self.snapshots_per_lineage = min(4, max(1, int(puzzle_config.get("snapshots_per_lineage", 4))))
        self.reserved_inputs: set[str] = set()
        self.reserved_states: set[str] = set()
        self.accepted_inputs: set[str] = set()
        self.accepted_states: set[str] = set()
        if self.bank_path.exists():
            self.lineages = []
            self.base_bank = self._load_bank()
        else:
            self.lineages = self._build_lineages()
            self.base_bank = self._build_bank()
            self._ensure_bank()
        self.next_lineage_index = self._next_generated_lineage_index()
        self.usage_stats = self._load_usage_stats()

    def reserve_history(self, records: Iterable[dict[str, Any]]) -> None:
        for record in records:
            puzzle = record.get("puzzle_metadata", {})
            metadata = record.get("metadata", {})
            if puzzle.get("puzzle"):
                input_signature = hashlib.sha256(str(puzzle["puzzle"]).encode("utf-8")).hexdigest()
                self.accepted_inputs.add(input_signature)
                self.reserved_inputs.add(input_signature)
            signature = metadata.get("canonical_state_signature") or puzzle.get("metadata", {}).get("canonical_state_signature")
            if signature:
                state_signature = str(signature)
                self.accepted_states.add(state_signature)
                self.reserved_states.add(state_signature)

    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        if scenario.edge_case != "none":
            return self._edge_record(scenario, sample_index)
        record_slot = int(scenario.metadata.get("record_slot", sample_index))
        input_type = "game_history" if record_slot % 2 == 0 else "fen"
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
        candidates = [p for p in candidates if self._is_available(p)]
        if not candidates and scenario.task_category == "endgame_analysis":
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and p.metadata.get("piece_count", 99) <= 7 and self._is_available(p)]
        if not candidates and scenario.task_category in {"special_move", "draw_rules", "terminal_state"}:
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and p.metadata.get("special_theme") and self._is_available(p)]
        if not candidates:
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and self._is_available(p)]
        candidates.sort(key=lambda p: (self.usage_stats.get(p.puzzle_id, 0), self.usage_stats.get(p.parent_puzzle_id or "", 0), p.puzzle_id))
        if not candidates:
            self._extend_catalog()
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type and self._is_available(p)]
            candidates.sort(key=lambda p: (self.usage_stats.get(p.puzzle_id, 0), self.usage_stats.get(p.parent_puzzle_id or "", 0), p.puzzle_id))
        if not candidates:
            candidates = [p for p in self.base_bank if p.metadata.get("input_type") == input_type]
            candidates.sort(key=lambda p: (self.usage_stats.get(p.puzzle_id, 0), p.puzzle_id))
            if not candidates:
                raise RuntimeError("Chess catalog contains no compatible position.")
            selected = candidates[record_slot % len(candidates)]
            selected.metadata["reused_in_job"] = True
            self._reserve(selected)
            return selected
        selected = candidates[record_slot % len(candidates)]
        selected.metadata["reused_in_job"] = False
        self._reserve(selected)
        return selected

    def mark_used(self, puzzle: PuzzleRecord) -> None:
        for key in {puzzle.puzzle_id, puzzle.parent_puzzle_id or puzzle.puzzle_id}:
            self.usage_stats[key] = self.usage_stats.get(key, 0) + 1
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.usage_path.write_text(json.dumps(self.usage_stats, indent=2, sort_keys=True), encoding="utf-8")

    def _build_lineages(self, *, start_index: int = 0, count: int | None = None, include_special: bool = True) -> list[_Lineage]:
        lineages: list[_Lineage] = []
        lineage_count = self.lineage_count if count is None else count
        for index in range(start_index, start_index + lineage_count):
            rng = random.Random(self.seed * 1009 + index * 7919)
            board = chess.Board()
            sans: list[str] = []
            trace: list[dict[str, Any]] = [{"ply": 0, "san": None, "uci": None, "fen": board.fen(en_passant="fen")}]
            target_plies = 28 + (index % 9) * 5
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
            lineage_id = f"chess-generated-{self.catalog_version}-{self.seed}-{index:04d}"
            available_plies = len(sans)
            snapshot_plies = sorted(set(max(4, min(available_plies, int(available_plies * ratio))) for ratio in (0.22, 0.45, 0.70, 1.0)))
            for snapshot_index, snapshot_ply in enumerate(snapshot_plies[: self.snapshots_per_lineage]):
                snapshot_moves = sans[:snapshot_ply]
                snapshot_trace = trace[: snapshot_ply + 1]
                snapshot_board = chess.Board(snapshot_trace[-1]["fen"])
                game = chess.pgn.Game()
                game.headers.update({"Event": "Synthetic validated game", "Site": "Generator", "Round": str(index + 1), "White": f"Synthetic-{index}-W", "Black": f"Synthetic-{index}-B", "Result": "*"})
                node = game
                replay = chess.Board()
                for san in snapshot_moves:
                    move = replay.parse_san(san)
                    node = node.add_variation(move)
                    replay.push(move)
                exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
                lineages.append(_Lineage(lineage_id, f"s{snapshot_index + 1}", STARTING_FEN, game.accept(exporter), snapshot_board.fen(en_passant="fen"), tuple(snapshot_moves), tuple(snapshot_trace)))
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
        for offset, (theme, initial_fen, preset_moves) in enumerate(special_positions if include_special else []):
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
            lineages.append(_Lineage(lineage_id, "special", initial, game.accept(exporter), board.fen(en_passant="fen"), tuple(sans), tuple(trace)))
        return lineages

    def _build_bank(self, lineages: Iterable[_Lineage] | None = None) -> list[PuzzleRecord]:
        records: list[PuzzleRecord] = []
        for index, lineage in enumerate(self.lineages if lineages is None else lineages):
            profile = self._position_profile(chess.Board(lineage.final_fen))
            difficulty = profile["difficulty"]
            truth = self._ground_truth(lineage, "canonical")
            for input_type in ("game_history", "fen"):
                puzzle_id = hashlib.sha1(f"{lineage.lineage_id}|{lineage.snapshot_id}|{input_type}".encode()).hexdigest()[:16]
                if input_type == "game_history":
                    payload = {"input_type": input_type, "pgn": lineage.pgn, "current_fen": lineage.final_fen}
                    rendered = f"PGN:\n{lineage.pgn}\nCurrent FEN: {lineage.final_fen}"
                    notation_format = "pgn_san"
                else:
                    payload = {"input_type": input_type, "fen": lineage.final_fen}
                    rendered = render_position(chess.Board(lineage.final_fen))
                    notation_format = "fen"
                record_truth = {**truth, "input_type": input_type}
                metadata = {
                    "input_type": input_type,
                    "notation_format": notation_format,
                    "side_to_move": record_truth["side_to_move"],
                    "lineage_id": lineage.lineage_id,
                    "snapshot_id": lineage.snapshot_id,
                    "dataset_split": split_for_lineage(lineage.lineage_id),
                    "verification_status": "verified",
                    "source": "programmatically_generated_legal_play",
                    "ply_count": len(lineage.moves_san),
                    "piece_count": chess.popcount(chess.Board(lineage.final_fen).occupied),
                    "special_theme": lineage.lineage_id.rsplit("-", 1)[-1] if lineage.lineage_id.startswith("chess-special-") else None,
                    "position_phase": profile["position_phase"],
                    "complexity_score": profile["complexity_score"],
                    "complexity_band": difficulty,
                    "motif_theme": profile["motif_theme"],
                    "canonical_state_signature": canonical_state_signature(lineage.final_fen),
                    "catalog_version": self.catalog_version,
                }
                records.append(PuzzleRecord(puzzle_id=puzzle_id, puzzle=json.dumps(payload, sort_keys=True), solution=lineage.final_fen, difficulty=difficulty, num_clues=len(lineage.moves_san), required_strategies=["legal_move_reasoning", "position_interpretation"], unique_solution_status=True, source="programmatic", canonical_signature=metadata["canonical_state_signature"], usage_count=0, parent_puzzle_id=lineage.lineage_id, transformation=f"{lineage.snapshot_id}_{input_type}", rendered_board=rendered, ground_truth=record_truth, metadata=metadata))
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
            "position_status": self.toolkit.position_status(lineage.final_fen),
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
        record_slot = int(scenario.metadata.get("record_slot", sample_index))
        reused_base = False
        bases = [
            p for p in self.base_bank
            if p.metadata.get("input_type") == "fen"
            and p.difficulty == scenario.difficulty
            and self._is_available(p)
        ]
        if not bases:
            bases = [p for p in self.base_bank if p.metadata.get("input_type") == "fen" and self._is_available(p)]
        bases.sort(key=lambda p: (self.usage_stats.get(p.parent_puzzle_id or "", 0), p.puzzle_id))
        if not bases:
            self._extend_catalog()
            bases = [p for p in self.base_bank if p.metadata.get("input_type") == "fen" and self._is_available(p)]
            bases.sort(key=lambda p: (self.usage_stats.get(p.parent_puzzle_id or "", 0), p.puzzle_id))
        if not bases:
            bases = [p for p in self.base_bank if p.metadata.get("input_type") == "fen"]
            bases.sort(key=lambda p: (self.usage_stats.get(p.parent_puzzle_id or "", 0), p.puzzle_id))
            if not bases:
                raise RuntimeError("Chess catalog contains no FEN base for edge-case generation.")
            reused_base = True
        base = bases[record_slot % len(bases)]
        lineage_id = base.parent_puzzle_id or base.puzzle_id
        edge = scenario.edge_case
        fen = base.ground_truth["current_fen"]
        board = chess.Board(fen)
        variant = record_slot // 20
        mutation: dict[str, Any]
        if edge == "malformed_input":
            parts = fen.split()
            mode = variant % 3
            malformed = " ".join(parts[:-1]) if mode == 0 else f"{parts[0]} x {parts[2]} {parts[3]} {parts[4]} {parts[5]}" if mode == 1 else f"{fen} extra"
            mutation = {"type": ("missing_fullmove", "invalid_turn", "extra_field")[mode], "base_fen": fen}
            payload, rendered, status = {"input_type": "fen", "fen": malformed}, f"Malformed FEN: {malformed}", "malformed"
        elif edge == "illegal_move":
            history = list(base.ground_truth.get("moves_san", []))
            fail_ply = max(1, min(len(history) + 1, 2 + (record_slot % max(2, len(history) or 2))))
            valid_prefix = history[: fail_ply - 1]
            prefix = self.toolkit.reconstruct(moves=valid_prefix)
            prefix_board = chess.Board(prefix["final_fen"])
            illegal = self._illegal_san(prefix_board, record_slot)
            moves = valid_prefix + [illegal]
            mutation = {"type": "illegal_san_at_ply", "failed_ply": fail_ply, "valid_prefix": valid_prefix, "illegal_move": illegal, "base_fen": fen}
            rendered_moves = " ".join(f"{(i // 2) + 1}." + (" " if i % 2 == 0 else "...") + move for i, move in enumerate(moves))
            payload, rendered, status = {"input_type": "game_history", "initial_fen": STARTING_FEN, "moves": moves}, f"Move history: {rendered_moves}", "illegal"
        elif edge == "ambiguous_input":
            legal = sorted(board.legal_moves, key=lambda move: move.uci())
            fragment = board.san(legal[record_slot % len(legal)]) if legal else "O-O"
            notation = "san" if record_slot % 2 == 0 else "uci"
            fragment = fragment if notation == "san" else legal[record_slot % len(legal)].uci()
            mutation = {"type": f"context_free_{notation}", "base_fen": fen, "fragment": fragment}
            payload, rendered, status = {"input_type": "move_fragment", "moves": [fragment], "notation_format": notation, "initial_position": None}, f"Context-free {notation.upper()} fragment: {fragment}\nInitial position: not supplied", "incomplete"
        elif edge == "contradictory_input":
            contradiction_types = ("side_to_move", "castling_rights", "en_passant_target", "fullmove_number")
            kind = contradiction_types[variant % len(contradiction_types)]
            claims = {"side_to_move": "black" if board.turn else "white", "castling_rights": "KQkq" if fen.split()[2] == "-" else "-", "en_passant_target": "e3" if fen.split()[3] == "-" else "-", "fullmove_number": board.fullmove_number + 7}
            mutation = {"type": f"contradict_{kind}", "base_fen": fen, "claimed_value": claims[kind]}
            payload, rendered, status = {"input_type": "fen", "fen": fen, f"claimed_{kind}": claims[kind]}, f"FEN: {fen}\nUser claims {kind.replace('_', ' ')}: {claims[kind]}", "contradictory"
        elif edge == "invalid_position":
            invalids = (
                fen.replace("K", "1", 1),
                "P7/" + "/".join(fen.split()[0].split("/")[1:]) + " " + " ".join(fen.split()[1:]),
                f"{fen.split()[0]} {fen.split()[1]} KQkq e3 {fen.split()[4]} {fen.split()[5]}",
            )
            invalid = invalids[variant % len(invalids)]
            mutation = {"type": ("missing_white_king", "pawn_on_back_rank", "impossible_state_rights")[variant % 3], "base_fen": fen}
            payload, rendered, status = {"input_type": "fen", "fen": invalid}, f"Invalid-position FEN: {invalid}", "invalid"
        else:
            parts = fen.split()
            keep = 1 + (variant % 5)
            incomplete = " ".join(parts[:keep])
            mutation = {"type": f"truncated_after_field_{keep}", "base_fen": fen, "provided_fields": keep}
            payload, rendered, status = {"input_type": "fen_fragment", "fen": incomplete}, f"Incomplete FEN ({keep}/6 fields): {incomplete}", "incomplete"
        puzzle_id = hashlib.sha1(f"{lineage_id}|{edge}|{sample_index}".encode()).hexdigest()[:16]
        input_signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        truth = {"validity_status": status, "verification_status": "verified_invalid_or_incomplete", "input_type": payload["input_type"], "current_fen": None, "base_fen": fen, "side_to_move": None, "mutation": mutation, "errors": [f"Input is {status}; do not infer a complete board."]}
        record = PuzzleRecord(puzzle_id=puzzle_id, puzzle=json.dumps(payload, sort_keys=True), solution="", difficulty=base.difficulty, num_clues=0, required_strategies=["input_validation"], unique_solution_status=False, source="programmatic_edge_case", canonical_signature=input_signature, usage_count=0, parent_puzzle_id=lineage_id, transformation=f"{edge}_{mutation['type']}", rendered_board=rendered, ground_truth=truth, metadata={"input_type": payload["input_type"], "notation_format": payload.get("notation_format", "unknown"), "side_to_move": None, "lineage_id": lineage_id, "dataset_split": split_for_lineage(lineage_id), "edge_case_kind": edge, "edge_mutation_type": mutation["type"], "canonical_state_signature": canonical_state_signature(fen), "input_signature": input_signature, "position_phase": base.metadata.get("position_phase"), "complexity_score": base.metadata.get("complexity_score"), "complexity_band": base.difficulty, "motif_theme": "input_diagnosis", "catalog_version": self.catalog_version, "verification_status": "verified_invalid_or_incomplete", "reused_in_job": reused_base})
        self._reserve(record)
        return record

    def _position_profile(self, board: chess.Board) -> dict[str, Any]:
        piece_count = chess.popcount(board.occupied)
        non_pawn_material = sum(PIECE_VALUES[piece_type] * len(board.pieces(piece_type, color)) for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN) for color in (chess.WHITE, chess.BLACK))
        legal_count = board.legal_moves.count()
        forcing = sum(1 for move in board.legal_moves if board.is_capture(move) or board.gives_check(move) or move.promotion)
        pinned = sum(1 for square in chess.scan_forward(board.occupied) if board.color_at(square) is not None and board.is_pinned(board.color_at(square), square))
        phase = "endgame" if piece_count <= 10 or non_pawn_material <= 12 else "opening" if board.fullmove_number <= 10 else "middlegame"
        score = min(100, legal_count + forcing * 5 + pinned * 4 + (12 if board.is_check() else 0) + (10 if phase == "middlegame" else 0))
        difficulty = complexity_band(score)
        motif = "check" if board.is_check() else "forcing_tactics" if forcing >= 4 else "pins" if pinned else "material_endgame" if phase == "endgame" else "positional_play"
        return {"position_phase": phase, "complexity_score": score, "difficulty": difficulty, "motif_theme": motif}

    def _is_available(self, puzzle: PuzzleRecord) -> bool:
        input_signature = hashlib.sha256(puzzle.puzzle.encode("utf-8")).hexdigest()
        state_signature = puzzle.metadata.get("canonical_state_signature")
        return input_signature not in self.reserved_inputs and (not state_signature or state_signature not in self.reserved_states)

    def _reserve(self, puzzle: PuzzleRecord) -> None:
        self.reserved_inputs.add(hashlib.sha256(puzzle.puzzle.encode("utf-8")).hexdigest())
        signature = puzzle.metadata.get("canonical_state_signature")
        if signature:
            self.reserved_states.add(str(signature))

    def release(self, puzzle: PuzzleRecord) -> None:
        input_signature = hashlib.sha256(puzzle.puzzle.encode("utf-8")).hexdigest()
        if input_signature not in self.accepted_inputs:
            self.reserved_inputs.discard(input_signature)
        signature = puzzle.metadata.get("canonical_state_signature")
        if signature and str(signature) not in self.accepted_states:
            self.reserved_states.discard(str(signature))

    def accept(self, puzzle: PuzzleRecord) -> None:
        input_signature = hashlib.sha256(puzzle.puzzle.encode("utf-8")).hexdigest()
        self.accepted_inputs.add(input_signature)
        self.reserved_inputs.add(input_signature)
        signature = puzzle.metadata.get("canonical_state_signature")
        if signature:
            self.accepted_states.add(str(signature))
            self.reserved_states.add(str(signature))

    def _illegal_san(self, board: chess.Board, seed: int) -> str:
        candidates = ("Ke9", "Qxq4", "O-O-O-O", "Pz5", "Nxh9", "Kxe8")
        legal_sans = {board.san(move) for move in board.legal_moves}
        return next(value for value in candidates[seed % len(candidates):] + candidates[:seed % len(candidates)] if value not in legal_sans)

    def _ensure_bank(self) -> None:
        if self.bank_path.exists():
            return
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        with self.bank_path.open("w", encoding="utf-8") as handle:
            for record in self.base_bank:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def _next_generated_lineage_index(self) -> int:
        prefix = f"chess-generated-{self.catalog_version}-{self.seed}-"
        indices = []
        for record in self.base_bank:
            lineage_id = record.parent_puzzle_id or ""
            if lineage_id.startswith(prefix):
                suffix = lineage_id[len(prefix):]
                if suffix.isdigit():
                    indices.append(int(suffix))
        return max(indices, default=-1) + 1

    def _extend_catalog(self) -> None:
        """Append a deterministic batch instead of ever recycling a reserved board."""
        batch_size = max(32, self.lineage_count // 4)
        lineages = self._build_lineages(start_index=self.next_lineage_index, count=batch_size, include_special=False)
        records = self._build_bank(lineages)
        if not records:
            return
        self.bank_path.parent.mkdir(parents=True, exist_ok=True)
        with self.bank_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        self.base_bank.extend(records)
        self.next_lineage_index += batch_size

    def _load_usage_stats(self) -> dict[str, int]:
        if not self.usage_path.exists():
            return {}
        return json.loads(self.usage_path.read_text(encoding="utf-8"))

    def _load_bank(self) -> list[PuzzleRecord]:
        records: list[PuzzleRecord] = []
        with self.bank_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                records.append(PuzzleRecord(**row))
        return records


def _looks_like_uci(value: str) -> bool:
    value = value.strip()
    return len(value) in {4, 5} and value[0] in chess.FILE_NAMES and value[2] in chess.FILE_NAMES and value[1] in chess.RANK_NAMES and value[3] in chess.RANK_NAMES
