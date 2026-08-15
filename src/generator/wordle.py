from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import PuzzleRecord, Scenario


DATA_DIR = Path(__file__).with_name("data")
EXPECTED = {"wordle_allowed.txt": (12972, "95b1813ade7abaea175ac9395e88b1e174af2c164b1dd90b716e2379d73bf8bf"), "wordle_answers.txt": (2315, "3a185cbda0d81285d2304eec05aec963af42edb3f4afeb6296c32a41e73a5dd3")}


def _load_words(filename: str) -> tuple[str, ...]:
    words = tuple(line.strip().lower() for line in (DATA_DIR / filename).read_text(encoding="utf-8").splitlines() if line.strip())
    count, checksum = EXPECTED[filename]; actual = hashlib.sha256("\n".join(words).encode()).hexdigest()
    if len(words) != count or len(set(words)) != count or actual != checksum: raise RuntimeError(f"Wordle vocabulary integrity failure for {filename}")
    if any(len(word) != 5 or not word.isalpha() or not word.isascii() for word in words): raise RuntimeError(f"Wordle vocabulary contains an invalid word in {filename}")
    return words


ALLOWED_WORDS = _load_words("wordle_allowed.txt")
ANSWER_WORDS = _load_words("wordle_answers.txt")
ALLOWED_SET = frozenset(ALLOWED_WORDS)
ANSWER_SET = frozenset(ANSWER_WORDS)


def score_feedback(answer: str, guess: str) -> str:
    if len(answer) != 5 or len(guess) != 5: raise ValueError("Wordle words must contain exactly five letters")
    result = ["B"] * 5; remaining = Counter()
    for index, (target, proposed) in enumerate(zip(answer, guess)):
        if target == proposed: result[index] = "G"
        else: remaining[target] += 1
    for index, proposed in enumerate(guess):
        if result[index] == "B" and remaining[proposed] > 0: result[index] = "Y"; remaining[proposed] -= 1
    return "".join(result)


def canonical_game(history: list[dict[str, str]], hard_mode: bool = False, max_guesses: int = 6) -> str:
    return json.dumps({"history": history, "hard_mode": hard_mode, "max_guesses": max_guesses}, sort_keys=True, separators=(",", ":"))


def parse_game(value: str) -> tuple[list[dict[str, str]], bool, int]:
    payload = json.loads(value); return [dict(item) for item in payload.get("history", [])], bool(payload.get("hard_mode", False)), int(payload.get("max_guesses", 6))


def filter_candidates(history: list[dict[str, str]], answers: tuple[str, ...] = ANSWER_WORDS) -> tuple[str, ...]:
    return tuple(answer for answer in answers if all(score_feedback(answer, item.get("guess", "")) == item.get("feedback") for item in history if len(item.get("guess", "")) == 5 and len(item.get("feedback", "")) == 5))


def letter_constraints(history: list[dict[str, str]]) -> dict[str, Any]:
    greens: list[str | None] = [None] * 5; banned_positions: dict[str, set[int]] = {}; minimums: dict[str, int] = {}; maximums: dict[str, int] = {}
    for item in history:
        guess, feedback = item.get("guess", ""), item.get("feedback", "")
        positive = Counter(letter for letter, mark in zip(guess, feedback) if mark in "GY"); gray = Counter(letter for letter, mark in zip(guess, feedback) if mark == "B")
        for index, (letter, mark) in enumerate(zip(guess, feedback)):
            if mark == "G": greens[index] = letter
            elif mark == "Y": banned_positions.setdefault(letter, set()).add(index)
        for letter, count in positive.items(): minimums[letter] = max(minimums.get(letter, 0), count)
        for letter in gray:
            if positive[letter]: maximums[letter] = min(maximums.get(letter, 5), positive[letter])
            else: maximums[letter] = 0
    return {"green_positions": greens, "banned_positions": {letter: sorted(values) for letter, values in sorted(banned_positions.items())}, "minimum_counts": dict(sorted(minimums.items())), "maximum_counts": dict(sorted(maximums.items()))}


def hard_mode_valid(guess: str, history: list[dict[str, str]]) -> tuple[bool, list[str]]:
    constraints = letter_constraints(history); errors = []
    for index, required in enumerate(constraints["green_positions"]):
        if required and (len(guess) <= index or guess[index] != required): errors.append(f"position {index + 1} must be {required}")
    counts = Counter(guess)
    for letter, minimum in constraints["minimum_counts"].items():
        if counts[letter] < minimum: errors.append(f"guess must contain at least {minimum} {letter}")
    for letter, positions in constraints["banned_positions"].items():
        for position in positions:
            if len(guess) > position and guess[position] == letter: errors.append(f"{letter} cannot remain in position {position + 1}")
    return not errors, errors


def validate_history(history: list[dict[str, str]], hard_mode: bool = False, max_guesses: int = 6) -> dict[str, Any]:
    errors = []
    if not 1 <= max_guesses <= 20: errors.append({"type": "max_guesses", "value": max_guesses})
    if len(history) > max_guesses: errors.append({"type": "too_many_guesses", "count": len(history)})
    seen = set()
    for index, item in enumerate(history):
        guess, feedback = str(item.get("guess", "")).lower(), str(item.get("feedback", "")).upper()
        if len(guess) != 5 or not guess.isalpha() or not guess.isascii(): errors.append({"type": "guess_format", "turn": index + 1, "guess": guess})
        elif guess not in ALLOWED_SET: errors.append({"type": "guess_not_allowed", "turn": index + 1, "guess": guess})
        if len(feedback) != 5 or set(feedback) - set("GYB"): errors.append({"type": "feedback_format", "turn": index + 1, "feedback": feedback})
        if guess in seen: errors.append({"type": "repeated_guess", "turn": index + 1, "guess": guess})
        if hard_mode and index:
            valid, reasons = hard_mode_valid(guess, history[:index])
            if not valid: errors.append({"type": "hard_mode_violation", "turn": index + 1, "reasons": reasons})
        seen.add(guess)
    candidates = filter_candidates(history) if not any(item["type"] in {"guess_format", "feedback_format"} for item in errors) else ()
    if history and not candidates: errors.append({"type": "impossible_history"})
    return {"valid": not errors, "errors": errors, "candidates": candidates, "candidate_count": len(candidates)}


def frequency_analysis(candidates: tuple[str, ...]) -> dict[str, Any]:
    positional = [Counter() for _ in range(5)]; presence = Counter()
    for word in candidates:
        for index, letter in enumerate(word): positional[index][letter] += 1
        presence.update(set(word))
    return {"candidate_count": len(candidates), "letter_presence": dict(presence.most_common()), "positional_frequency": [dict(counter.most_common()) for counter in positional]}


def entropy_for_guess(guess: str, candidates: tuple[str, ...]) -> tuple[float, int, int]:
    partitions = Counter(score_feedback(answer, guess) for answer in candidates); total = len(candidates)
    entropy = -sum((count/total) * math.log2(count/total) for count in partitions.values()) if total else 0.0
    return entropy, max(partitions.values(), default=0), len(partitions)


@lru_cache(maxsize=256)
def rank_guesses(candidates: tuple[str, ...], limit: int = 10) -> tuple[dict[str, Any], ...]:
    if not candidates: return ()
    ranked = []
    candidate_set = set(candidates)
    for guess in ALLOWED_WORDS:
        entropy, worst, partitions = entropy_for_guess(guess, candidates)
        ranked.append((entropy, worst, guess in candidate_set, guess, partitions))
    ranked.sort(key=lambda item: (-item[0], item[1], -item[2], item[3]))
    return tuple({"guess": guess, "entropy_bits": round(entropy, 6), "worst_case_remaining": worst, "partition_count": partitions, "possible_answer": is_answer} for entropy, worst, is_answer, guess, partitions in ranked[:limit])


def cheap_guess(candidates: tuple[str, ...], excluded: set[str]) -> str:
    presence = Counter(letter for word in candidates for letter in set(word)); positional = [Counter(word[i] for word in candidates) for i in range(5)]
    choices = [word for word in candidates if word not in excluded] or [word for word in ALLOWED_WORDS if word not in excluded]
    return min(choices, key=lambda word: (-sum(presence[letter] for letter in set(word)) - sum(positional[i][letter] for i, letter in enumerate(word)), word))


def render_game(history: list[dict[str, str]], hard_mode: bool, max_guesses: int) -> str:
    symbols = {"G": "🟩", "Y": "🟨", "B": "⬛"}; lines = [f"Wordle | {'Hard' if hard_mode else 'Normal'} mode | {max_guesses} guesses"]
    lines.extend(f"{index + 1}. {item['guess'].upper()}  {''.join(symbols.get(mark, '?') for mark in item['feedback'])}" for index, item in enumerate(history))
    lines.extend(f"{index + 1}. _____" for index in range(len(history), max_guesses))
    return "\n".join(lines)


def build_ground_truth(history: list[dict[str, str]], hard_mode: bool, max_guesses: int, target: str, *, mutation: dict[str, Any] | None = None) -> dict[str, Any]:
    validation = validate_history(history, hard_mode, max_guesses); candidates = validation["candidates"]
    actual_feedback_valid = all(score_feedback(target, item.get("guess", "")) == item.get("feedback") for item in history if len(item.get("guess", "")) == 5)
    if not actual_feedback_valid: validation = {**validation, "valid": False, "errors": [*validation["errors"], {"type": "target_feedback_mismatch"}]}
    recommended = cheap_guess(candidates, {item.get("guess", "") for item in history}) if candidates and not any(item.get("feedback") == "GGGGG" for item in history) and len(history) < max_guesses else None
    return {"validity_status": "valid" if validation["valid"] else "invalid", "validation_errors": validation["errors"], "target": target, "solution": target, "history": history, "hard_mode": hard_mode, "max_guesses": max_guesses, "candidates": list(candidates), "candidate_count": len(candidates), "unique_solution_status": len(candidates) == 1, "constraints": letter_constraints(history), "recommended_guess": recommended, "last_feedback": history[-1]["feedback"] if history else None, "solved": any(item.get("feedback") == "GGGGG" for item in history), "attempts_remaining": max(0, max_guesses - len(history)), "mutation": mutation or {}}


class WordlePuzzleManager:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        output = Path(config["output_path"]); puzzles = config.get("puzzles", {})
        self.bank_path = output / puzzles.get("persistent_bank_filename", "wordle_game_bank.jsonl"); self.usage_path = output / puzzles.get("usage_stats_filename", "wordle_game_usage.json")
        self.usage_stats: dict[str, int] = {}; self.base_bank = self._build_bank(); self._ensure_bank(); self.usage_stats = self._load_usage_stats()
    def select_puzzle(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        choices = [p for p in self.base_bank if p.difficulty == scenario.difficulty] or self.base_bank; parent = choices[sample_index % len(choices)]
        return self._edge(parent, scenario.edge_case, sample_index) if scenario.edge_case != "none" else deepcopy(parent)
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
        desired_turns = {"easy": 4, "medium": 3, "hard": 2, "expert": 1}; bank = []
        for difficulty, turns in desired_turns.items():
            for offset in range(2):
                target = ANSWER_WORDS[(500 * offset + 277 * turns + 13) % len(ANSWER_WORDS)]; history = []; candidates = ANSWER_WORDS; used = set()
                first = ("soare", "slate")[offset]
                for turn in range(turns):
                    guess = first if turn == 0 and first in ALLOWED_SET else cheap_guess(candidates, used)
                    if guess == target:
                        alternatives = [word for word in candidates if word != target and word not in used]
                        if not alternatives:
                            break
                        guess = alternatives[0]
                    history.append({"guess": guess, "feedback": score_feedback(target, guess)}); used.add(guess); candidates = filter_candidates(history)
                bank.append(self._record(history, offset % 2 == 1, 6, target, difficulty, None, "identity"))
        return bank
    def _record(self, history: list[dict[str, str]], hard: bool, maximum: int, target: str, difficulty: str, parent: str | None, transformation: str, mutation: dict[str, Any] | None = None) -> PuzzleRecord:
        puzzle = canonical_game(history, hard, maximum); truth = build_ground_truth(history, hard, maximum, target, mutation=mutation); digest = hashlib.sha1(f"{puzzle}|{target}|{transformation}".encode()).hexdigest()[:16]
        return PuzzleRecord(f"wordle_{digest}", puzzle, target, difficulty, len(history), ["candidate_filtering", "duplicate_letter_accounting", "information_gain"], truth["unique_solution_status"], "classic_wordle_vocabulary", hashlib.sha256(puzzle.encode()).hexdigest(), self.usage_stats.get(f"wordle_{digest}", 0), parent_puzzle_id=parent, transformation=transformation, rendered_board=render_game(history, hard, maximum), ground_truth=truth, metadata={"size": 5, "word_length": 5, "max_guesses": maximum, "hard_mode": hard, "guess_count": len(history), "candidate_count": truth["candidate_count"], "vocabulary_size": len(ALLOWED_WORDS), "answer_count": len(ANSWER_WORDS)})
    def _edge(self, parent: PuzzleRecord, edge: str, sample_index: int) -> PuzzleRecord:
        history, hard, maximum = parse_game(parent.puzzle); target = parent.solution; altered = deepcopy(history)
        if edge == "malformed_input":
            record = self._record(altered, hard, maximum, target, parent.difficulty, parent.puzzle_id, f"malformed_{sample_index}", {"type": edge}); record.rendered_board = "WORDLE ???"; record.ground_truth["validity_status"] = "malformed"; return record
        if edge == "invalid_guess": altered.append({"guess": "12abc", "feedback": "BBBBB"})
        elif edge in {"contradictory_feedback", "impossible_history"}: altered = [{"guess": "aahed", "feedback": "GGGGG"}]
        elif edge == "repeated_guess" and altered: altered.append(dict(altered[0]))
        elif edge == "hard_mode_violation":
            hard = True
            if not altered: altered = [{"guess": "soare", "feedback": score_feedback(target, "soare")}]
            violation = next((word for word in ALLOWED_WORDS if not hard_mode_valid(word, altered)[0]), "fuzzy"); altered.append({"guess": violation, "feedback": score_feedback(target, violation)})
        elif edge == "exhausted_attempts":
            altered = []
            for guess in ("soare", "clint", "dumpy", "beach", "forge", "waltz"):
                if guess in ALLOWED_SET and guess != target: altered.append({"guess": guess, "feedback": score_feedback(target, guess)})
            maximum = len(altered)
        return self._record(altered, hard, maximum, target, parent.difficulty, parent.puzzle_id, f"{edge}_{sample_index}", {"type": edge})
