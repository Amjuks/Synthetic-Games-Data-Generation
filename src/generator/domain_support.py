from __future__ import annotations

import hashlib
import math
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from .models import PuzzleRecord, Scenario


DIFFICULTY_BOUNDS = {
    "easy": (10, 27),
    "medium": (28, 44),
    "hard": (45, 64),
    "expert": (65, 100),
}

PUZZLE_ROUTE_CATEGORIES = {
    "next_best_move", "validity_check", "rules_explanation", "solve_puzzle",
    "hint", "mistake_correction", "technique_discussion", "beginner_question",
    "advanced_question", "general_chat", "solve_row_column_box", "cage_analysis",
    "run_analysis", "region_analysis", "clue_analysis", "duplicate_analysis",
    "island_analysis",
    "legal_move_check", "board_analysis", "mobility_analysis", "corner_strategy",
    "endgame_analysis", "solve_board", "probability_analysis", "flag_analysis",
    "chord_analysis", "next_best_guess", "feedback_check", "solve_word",
    "candidate_analysis", "opener_strategy", "hard_mode_check",
    "rectangle_analysis", "inequality_analysis", "weighted_sum_analysis",
    "subset_analysis",
}
PUZZLE_ROUTE_EDGES = {
    "none", "incorrect_assumption", "malformed_input", "invalid_board",
    "unsolvable_board", "ambiguous_board", "invalid_cages", "invalid_clues",
    "invalid_regions", "invalid_grid", "unsolvable_puzzle", "ambiguous_puzzle",
    "illegal_move", "forced_pass", "terminal_position", "contradictory_input",
    "contradictory_flags", "impossible_state", "no_forced_move",
    "loss_state", "invalid_guess", "contradictory_feedback", "impossible_history",
    "hard_mode_violation", "repeated_guess", "exhausted_attempts",
    "invalid_constraints", "invalid_targets",
}
PUZZLE_ROUTE_MODES = {
    "none", "candidate_scan", "row_column_box_check", "solution_verification",
    "cage_candidate_scan", "cage_constraint_check", "run_candidate_scan",
    "sum_constraint_check", "constraint_check", "line_candidate_scan",
    "duplicate_scan", "deduction_scan",
    "legal_move_scan", "move_application", "positional_analysis", "search",
    "frontier_scan", "probability_analysis", "flag_validation",
    "feedback_scoring", "candidate_filter", "entropy_analysis", "hard_mode_validation",
    "rectangle_candidate_scan", "inequality_scan", "weighted_sum_scan",
    "subset_sum_scan",
}


@dataclass(frozen=True)
class ToolSpec:
    """Inspectable contract for a deterministic domain tool."""

    name: str
    description: str
    task_categories: tuple[str, ...] = ("*",)
    edge_cases: tuple[str, ...] = ("*",)
    output_purpose: str = "verified puzzle evidence"
    executor: str = "_run_tool"


def make_tool_catalog(domain: str, descriptions: dict[str, str]) -> dict[str, ToolSpec]:
    return {
        name: ToolSpec(
            name=name,
            description=description,
            output_purpose=description,
        )
        for name, description in descriptions.items()
    }


def validate_tool_catalog(adapter: Any) -> None:
    catalog = getattr(adapter, "tool_catalog", None)
    if not isinstance(catalog, dict):
        raise ValueError(f"{adapter.name} adapter must expose a tool_catalog mapping")
    minimum = int(adapter.config.get("puzzles", {}).get("min_tool_catalog_size", 10))
    if len(catalog) < minimum:
        raise ValueError(f"{adapter.name} tool catalog has {len(catalog)} tools; at least {minimum} are required")
    if len(catalog) != len(set(catalog)):
        raise ValueError(f"{adapter.name} tool catalog contains duplicate names")
    prefix = f"{adapter.name}_"
    for name, spec in catalog.items():
        if name != spec.name or not name.startswith(prefix):
            raise ValueError(f"invalid {adapter.name} tool catalog entry: {name}")
        if not spec.description.strip() or not spec.output_purpose.strip():
            raise ValueError(f"tool {name} requires a description and output purpose")
        executor = getattr(adapter, spec.executor, None)
        if not callable(executor):
            raise ValueError(f"tool {name} has no callable executor {spec.executor}")
    route_names = set(adapter.catalog_route_names())
    unknown = route_names - set(catalog)
    unreachable = set(catalog) - route_names
    if unknown:
        raise ValueError(f"{adapter.name} routes unregistered tools: {', '.join(sorted(unknown))}")
    if unreachable:
        raise ValueError(f"{adapter.name} catalog has unreachable tools: {', '.join(sorted(unreachable))}")


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
        self._accepted_puzzle_ids: set[str] = set()
        self._leased_puzzle_ids: set[str] = set()
        self._reserved_puzzle_ids: set[str] = set()
        self._puzzle_selection_counts: dict[str, int] = {}
        self._exhausted_variant_pools: dict[tuple[str, str, str, str], list[PuzzleRecord]] = {}
        validate_tool_catalog(self)

    def catalog_route_names(self) -> set[str]:
        """Enumerate the supported route universe without selecting or solving a puzzle.

        Runtime configuration may intentionally narrow generation to one task or
        mode; that must not make the adapter's other registered capabilities
        invalid or prevent construction.
        """
        scenario_config = self.config.get("scenario", {})
        categories = PUZZLE_ROUTE_CATEGORIES | set(scenario_config.get("task_categories", []))
        edges = PUZZLE_ROUTE_EDGES | set(scenario_config.get("edge_cases", ["none"]))
        modes = PUZZLE_ROUTE_MODES | set(scenario_config.get("tool_usage_modes", ["none"]))
        routed: set[str] = set()
        for category in categories:
            for edge_case in edges:
                for tool_usage in modes:
                    scenario = SimpleNamespace(
                        task_category=category,
                        edge_case=edge_case,
                        tool_usage=tool_usage,
                    )
                    routed.update(self._tool_bundle(scenario))
        return routed

    def prepare_job(self, *, job_dir: Any, accepted_history: list[dict[str, Any]], rejected_history: list[dict[str, Any]]) -> None:
        del job_dir, rejected_history
        # Only accepted dataset rows consume uniqueness. Rejected attempts are
        # not part of the dataset and must not permanently shrink the pool on
        # resume.
        for record in accepted_history:
            puzzle_id = record.get("puzzle_id") or record.get("puzzle_metadata", {}).get("puzzle_id")
            if puzzle_id:
                key = str(puzzle_id)
                self._accepted_puzzle_ids.add(key)
                self._reserved_puzzle_ids.add(key)
                self._puzzle_selection_counts[key] = self._puzzle_selection_counts.get(key, 0) + 1

    def _select_varied_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        route = (
            str(scenario.difficulty),
            str(scenario.edge_case),
            str(scenario.task_category),
            str(scenario.tool_usage),
        )
        exhausted_pool = self._exhausted_variant_pools.get(route)
        if exhausted_pool:
            return self._select_balanced_reuse(scenario, exhausted_pool)

        scan_limit = max(1, int(self.config.get("puzzles", {}).get("variant_scan_limit", 256)))
        discovered: dict[str, PuzzleRecord] = {}
        for offset in range(scan_limit):
            puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index + offset)
            discovered.setdefault(puzzle.puzzle_id, deepcopy(puzzle))
            if puzzle.puzzle_id in self._accepted_puzzle_ids or puzzle.puzzle_id in self._leased_puzzle_ids:
                continue
            return self._finalize_problem_selection(scenario, puzzle, reused=False)

        if not discovered:
            raise RuntimeError(f"{self.name} puzzle manager returned no variants for the selected route.")
        if not bool(self.config.get("puzzles", {}).get("allow_balanced_reuse", True)):
            raise RuntimeError(f"{self.name} puzzle variants exhausted and balanced reuse is disabled.")
        pool = list(discovered.values())
        self._exhausted_variant_pools[route] = pool
        return self._select_balanced_reuse(scenario, pool)

    def _select_balanced_reuse(self, scenario: Scenario, pool: list[PuzzleRecord]) -> PuzzleRecord:
        puzzle = deepcopy(min(
            pool,
            key=lambda item: (
                self._puzzle_selection_counts.get(item.puzzle_id, 0),
                getattr(self.puzzle_manager, "usage_stats", {}).get(item.puzzle_id, 0),
                item.puzzle_id,
            ),
        ))
        return self._finalize_problem_selection(scenario, puzzle, reused=True)

    def _finalize_problem_selection(self, scenario: Scenario, puzzle: PuzzleRecord, *, reused: bool) -> PuzzleRecord:
        previous_selections = self._puzzle_selection_counts.get(puzzle.puzzle_id, 0)
        self._puzzle_selection_counts[puzzle.puzzle_id] = previous_selections + 1
        self._leased_puzzle_ids.add(puzzle.puzzle_id)
        self._reserved_puzzle_ids.add(puzzle.puzzle_id)
        puzzle.metadata.update({
            "reused_in_job": reused,
            "job_selection_number": previous_selections + 1,
        })
        add_complexity_metadata(puzzle)
        scenario.difficulty = puzzle.difficulty
        return puzzle

    def release_problem(self, puzzle: PuzzleRecord) -> None:
        """Release a rejected attempt; only accepted rows consume uniqueness."""
        self._leased_puzzle_ids.discard(puzzle.puzzle_id)
        if puzzle.puzzle_id not in self._accepted_puzzle_ids:
            self._reserved_puzzle_ids.discard(puzzle.puzzle_id)

    def accept_problem(self, puzzle: PuzzleRecord) -> None:
        """Promote a temporary selection lease to accepted job history."""
        self._leased_puzzle_ids.discard(puzzle.puzzle_id)
        self._accepted_puzzle_ids.add(puzzle.puzzle_id)
        self._reserved_puzzle_ids.add(puzzle.puzzle_id)

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
            "reused_in_job": puzzle.metadata.get("reused_in_job", False),
            "job_selection_number": puzzle.metadata.get("job_selection_number", 1),
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
        unknown = [name for name in used if name not in self.tool_catalog]
        if unknown:
            errors.append(f"tool bundle contains unregistered tools: {', '.join(unknown)}")
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
