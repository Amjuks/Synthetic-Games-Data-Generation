from __future__ import annotations

import hashlib
import math
from typing import Any, Callable, Iterable

from .models import PuzzleRecord, Scenario


DIFFICULTY_BOUNDS = {
    "easy": (10, 27),
    "medium": (28, 44),
    "hard": (45, 64),
    "expert": (65, 100),
}


def add_complexity_metadata(puzzle: PuzzleRecord) -> None:
    """Attach a deterministic, measurable score without changing domain difficulty."""
    low, high = DIFFICULTY_BOUNDS.get(puzzle.difficulty, (0, 100))
    size = int(puzzle.metadata.get("size") or puzzle.metadata.get("height") or 0)
    if not size:
        inferred = math.isqrt(len(puzzle.puzzle))
        size = inferred if inferred * inferred == len(puzzle.puzzle) else 1
    width = int(puzzle.metadata.get("width") or size)
    area = max(1, size * width)
    clue_density = min(1.0, max(0.0, puzzle.num_clues / area))
    strategy_weight = len(puzzle.required_strategies) * 3
    measurable = size + width + strategy_weight + round((1.0 - clue_density) * 8)
    score = min(high, max(low, low + measurable % max(1, high - low + 1)))
    puzzle.metadata.update({
        "complexity_score": score,
        "complexity_band": puzzle.difficulty,
        "complexity_factors": {
            "height": size,
            "width": width,
            "clue_count": puzzle.num_clues,
            "clue_density": round(clue_density, 4),
            "strategy_count": len(puzzle.required_strategies),
        },
    })


def build_tool_usage(
    *,
    scenario: Scenario,
    puzzle: PuzzleRecord,
    tool_names: Iterable[str],
    input_for: Callable[[str], dict[str, Any]],
    output_for: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    names = list(dict.fromkeys(tool_names))
    calls = []
    for name in names:
        output = dict(output_for(name))
        output.setdefault("verified", True)
        calls.append({
            "tool_name": name,
            "input": input_for(name),
            "output": output,
            "reason": f"The {scenario.task_category} request needs verified {name.replace('_', ' ')} evidence.",
        })
    signature = hashlib.sha256("|".join(names).encode("utf-8")).hexdigest()
    puzzle.metadata.update({
        "tools_required": names,
        "tools_used": names,
        "tool_count": len(calls),
        "tool_calls": calls,
        "tool_bundle_signature": signature,
        "verification_status": "verified" if calls else "not_required",
    })
    if not calls:
        return {"used": False, "tool_name": None, "tool_input": None, "tool_output": None, "reason": None, "calls": [], "verification_status": "not_required"}
    primary = calls[0]
    return {
        "used": True,
        "tool_name": primary["tool_name"],
        "tool_input": primary["input"],
        "tool_output": primary["output"],
        "reason": primary["reason"],
        "calls": calls,
        "verification_status": "verified",
    }


def compact_tool_context(domain: str, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
    calls = []
    for call in tool_usage.get("calls", []):
        output = {
            key: value
            for key, value in call.get("output", {}).items()
            if key not in {"solution", "solution_grid", "solution_mask", "solved_board"}
        }
        for key, value in list(output.items()):
            if isinstance(value, list) and len(value) > 20:
                output[key] = value[:20]
            elif isinstance(value, dict) and len(value) > 20:
                output[key] = dict(list(value.items())[:20])
        calls.append({"tool_name": call["tool_name"], "input": call["input"], "output": output, "reason": call["reason"]})
    return {"domain": domain, "tools_required": puzzle.metadata.get("tools_required", []), "verification_status": tool_usage.get("verification_status"), "calls": calls}


class VariedDomainSupport:
    """Shared lifecycle/schema mechanics; domain adapters still choose and run tools."""

    def _initialize_variety_support(self) -> None:
        self._reserved_puzzle_ids: set[str] = set()

    def prepare_job(self, *, job_dir: Any, accepted_history: list[dict[str, Any]], rejected_history: list[dict[str, Any]]) -> None:
        del job_dir
        for record in [*accepted_history, *rejected_history]:
            puzzle_id = record.get("puzzle_id") or record.get("puzzle_metadata", {}).get("puzzle_id")
            if puzzle_id:
                self._reserved_puzzle_ids.add(str(puzzle_id))

    def _select_varied_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        for offset in range(64):
            puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index + offset)
            if puzzle.puzzle_id in self._reserved_puzzle_ids:
                continue
            self._reserved_puzzle_ids.add(puzzle.puzzle_id)
            add_complexity_metadata(puzzle)
            scenario.difficulty = puzzle.difficulty
            return puzzle
        raise RuntimeError(f"{self.name} puzzle variants exhausted; refusing to reuse a puzzle in the same job.")

    def sample_metadata(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "category": scenario.task_category,
            "difficulty": puzzle.difficulty,
            "complexity_score": puzzle.metadata.get("complexity_score"),
            "complexity_band": puzzle.metadata.get("complexity_band"),
            "complexity_factors": puzzle.metadata.get("complexity_factors", {}),
            "tools_required": puzzle.metadata.get("tools_required", []),
            "tools_used": puzzle.metadata.get("tools_used", []),
            "tool_count": puzzle.metadata.get("tool_count", 0),
            "tool_bundle_signature": puzzle.metadata.get("tool_bundle_signature"),
            "verification_status": tool_usage.get("verification_status"),
            "transformation": puzzle.transformation,
        }

    def tool_validation_errors(self, puzzle: PuzzleRecord) -> list[str]:
        minimum = int(self.config.get("puzzles", {}).get("min_tools_per_sample", 2))
        required = puzzle.metadata.get("tools_required", [])
        used = puzzle.metadata.get("tools_used", [])
        calls = puzzle.metadata.get("tool_calls", [])
        errors = []
        if required != used:
            errors.append("required and used tool sequences do not match")
        if len(used) < minimum:
            errors.append(f"samples require at least {minimum} purposeful tool calls")
        if len(set(used)) != len(used):
            errors.append("tool bundles must contain distinct tools")
        if len(calls) != len(used):
            errors.append("recorded tool call count does not match tools_used")
        if any(not call.get("output", {}).get("verified", False) for call in calls):
            errors.append("every tool call must be verified")
        if puzzle.metadata.get("complexity_band") != puzzle.difficulty:
            errors.append("complexity band does not match puzzle difficulty")
        return errors

    @staticmethod
    def _add_flat_tool_metadata(row: dict[str, Any], sample: dict[str, Any]) -> None:
        metadata = sample.get("metadata", {})
        row.update({
            "complexity_score": metadata.get("complexity_score"),
            "complexity_band": metadata.get("complexity_band"),
            "tool_count": metadata.get("tool_count", 0),
            "tool_names": metadata.get("tools_used", []),
            "tool_calls": sample.get("tool_usage_details", {}).get("calls", []),
            "tool_bundle_signature": metadata.get("tool_bundle_signature"),
            "verification_status": metadata.get("verification_status"),
        })
