from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, make_tool_catalog
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator
from ..starbattle import StarBattlePuzzleManager, parse_cell, parse_puzzle, solve_starbattle, validate_structure


class StarBattleScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {
            "next_best_move": "ask_for_next_star_deduction",
            "validity_check": "verify_star_placement",
            "rules_explanation": "learn_star_battle_rules",
            "solve_puzzle": "request_solution_guidance",
            "region_analysis": "analyze_region_candidates",
            "hint": "ask_for_hint",
            "mistake_correction": "debug_touching_or_count_mistake",
            "technique_discussion": "discuss_star_battle_strategy",
            "beginner_question": "learn_star_battle_basics",
            "advanced_question": "analyze_region_line_interactions",
            "general_chat": "general_star_battle_help",
        }
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = [
            "Use the supplied Star Battle region layout exactly unless malformed input is explicitly required.",
            "Ground every claim in row, column, region, and non-touching constraints.",
        ]
        if task_category == "hint":
            constraints.append("Prefer one elimination or forced placement over revealing the full solution.")
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Star Battle assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed layout before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class StarBattleValidator:
    def validate(
        self,
        output: dict[str, Any],
        scenario: Scenario,
        puzzle: PuzzleRecord,
    ) -> ValidationResult:
        errors: list[str] = []
        if output.get("conversation_type") != scenario.conversation_type:
            errors.append("conversation_type does not match scenario")
        if output.get("category") != scenario.task_category:
            errors.append("category does not match scenario task_category")
        if scenario.conversation_type == "single_turn":
            if not str(output.get("prompt", "")).strip():
                errors.append("single-turn prompt is empty")
            if not str(output.get("response", "")).strip():
                errors.append("single-turn response is empty")
        else:
            messages = output.get("messages", [])
            if not isinstance(messages, list) or not messages:
                errors.append("multi-turn messages list is empty")
            else:
                for index, message in enumerate(messages):
                    if not str(message.get("user", "")).strip():
                        errors.append(f"message {index} is missing user text")
                    if not str(message.get("response", "")).strip():
                        errors.append(f"message {index} is missing response text")

        board = output.get("board", "")
        if scenario.edge_case == "malformed_input":
            if not board:
                errors.append("malformed_input scenario requires a malformed puzzle string")
        elif board != puzzle.rendered_board:
            errors.append("output board does not match selected puzzle")

        size, stars_per_unit, regions = parse_puzzle(puzzle.puzzle)
        structural = validate_structure(size, stars_per_unit, regions)
        truth = puzzle.ground_truth
        if scenario.edge_case == "none":
            if structural:
                errors.append("standard scenario has structurally invalid regions")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique Star Battle puzzle")
        if truth.get("solution") != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_regions" and not structural:
            errors.append("invalid_regions scenario has no structural violation")
        if (
            scenario.edge_case == "unsolvable_puzzle"
            and truth.get("solvability_status") != "unsolvable"
        ):
            errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if (
            scenario.edge_case == "ambiguous_puzzle"
            and truth.get("solvability_status") != "ambiguous"
        ):
            errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class StarBattleDomainAdapter(VariedDomainSupport):
    name = "starbattle"
    tool_catalog = make_tool_catalog(name, {
        "starbattle_candidate_analysis": "Forced stars, forced empty cells, and a deduction.", "starbattle_constraint_validation": "Region, quota, adjacency, structure, and solver validation.",
        "starbattle_solution_verification": "Complete solver-backed star-grid verification.", "starbattle_rules_reference": "Canonical quota and non-touching rules.", "starbattle_puzzle_summary": "Size, star quota, regions, and difficulty.",
        "starbattle_row_quota_analysis": "Forced and available star capacity for every row.", "starbattle_column_quota_analysis": "Forced and available star capacity for every column.",
        "starbattle_region_quota_analysis": "Forced and available star capacity for every region.", "starbattle_adjacency_exclusion_scan": "Cells excluded by forced stars, including diagonals.",
        "starbattle_solution_space_analysis": "Capped solution count and ambiguous witness differences.",
    })

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = StarBattleScenarioGenerator(config)
        self.puzzle_manager = StarBattlePuzzleManager(config)
        self.validator = StarBattleValidator()
        self._initialize_variety_support()

    def generate_scenario(
        self,
        *,
        sample_index: int,
        conversation_type: str,
        max_turns: int,
        distribution_stats: dict[str, Any],
    ) -> Scenario:
        return self.scenario_generator.generate(
            sample_index=sample_index,
            conversation_type=conversation_type,
            max_turns=max_turns,
            distribution_stats=distribution_stats,
        )

    def select_problem(self, scenario: Scenario, sample_index: int) -> PuzzleRecord:
        return self._select_varied_problem(scenario, sample_index)

    def mark_problem_used(self, puzzle: PuzzleRecord) -> None:
        self.puzzle_manager.mark_used(puzzle)

    def maybe_use_tool(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "size": puzzle.metadata.get("size"), "stars_per_unit": puzzle.metadata.get("stars_per_unit"), "regions": puzzle.metadata.get("regions", [])}, output_for=lambda name: self._run_tool(name, puzzle))

    def validate(
        self,
        output: dict[str, Any],
        scenario: Scenario,
        puzzle: PuzzleRecord,
    ) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        return {
            "puzzle_id": puzzle.puzzle_id,
            "size": puzzle.metadata.get("size"),
            "stars_per_unit": puzzle.metadata.get("stars_per_unit"),
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
            "transformation": puzzle.transformation,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        return {
            "solution_grid": truth.get("solution_grid"),
            "validity_status": truth.get("validity_status"),
            "solvability_status": truth.get("solvability_status"),
            "unique_solution_status": truth.get("unique_solution_status"),
            "solution_count": truth.get("solution_count"),
            "structural_violations": truth.get("structural_violations", [])[:20],
            "solution_violations": truth.get("solution_violations", [])[:20],
            "forced_stars": truth.get("forced_stars", [])[:12],
            "suggested_move": truth.get("suggested_move"),
        }

    def prompt_context(
        self,
        scenario: Scenario,
        puzzle: PuzzleRecord,
        tool_usage: dict[str, Any],
    ) -> dict[str, Any]:
        del scenario
        tool_input = dict(tool_usage.get("tool_input") or {})
        tool_input.pop("regions", None)
        output = dict(tool_usage.get("tool_output") or {})
        output["forced_stars"] = output.get("forced_stars", [])[:12]
        output["forced_empty"] = output.get("forced_empty", [])[:12]
        if tool_usage.get("tool_name") == "starbattle_solution_verification":
            output.pop("solution", None)
            output.pop("solution_grid", None)
        return {
            "domain": self.name,
            "tool_usage": {
                "used": bool(tool_usage.get("used")),
                "tool_name": tool_usage.get("tool_name"),
                "tool_input": tool_input or None,
                "tool_output": output,
                "reason": tool_usage.get("reason"),
            },
            "calls": [
                {
                    "tool_name": call["tool_name"],
                    "input": {key: value for key, value in call["input"].items() if key != "regions"},
                    "output": {key: value for key, value in call["output"].items() if key not in {"solution", "solution_grid"}},
                    "reason": call["reason"],
                }
                for call in tool_usage.get("calls", [])
            ],
        }

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Star Battle region layout exactly. Enforce the stated number of stars in "
            "every row, column, and region, and remember that stars cannot touch orthogonally or diagonally."
        )

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {})
        row["domain"] = sample.get("domain", self.name)
        row["grid_size"] = metadata.get("size")
        row["stars_per_unit"] = metadata.get("stars_per_unit")
        row["regions"] = metadata.get("regions", [])
        row["tool_used"] = sample.get("tool_used", False)
        row["tool_name"] = sample.get("tool_usage_details", {}).get("tool_name")
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["starbattle_constraint_validation", "starbattle_puzzle_summary", "starbattle_rules_reference"]
        if scenario.edge_case in {"invalid_regions", "unsolvable_puzzle", "ambiguous_puzzle"}: return ["starbattle_constraint_validation", "starbattle_region_quota_analysis", "starbattle_solution_space_analysis"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["starbattle_rules_reference", "starbattle_puzzle_summary", "starbattle_region_quota_analysis"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}: return ["starbattle_row_quota_analysis", "starbattle_column_quota_analysis", "starbattle_region_quota_analysis", "starbattle_adjacency_exclusion_scan"]
        primary = self._tool_name_for_scenario(scenario)
        if primary == "starbattle_solution_verification": return ["starbattle_candidate_analysis", "starbattle_region_quota_analysis", "starbattle_solution_space_analysis", primary]
        if primary == "starbattle_candidate_analysis": return [primary, "starbattle_row_quota_analysis", "starbattle_column_quota_analysis", "starbattle_adjacency_exclusion_scan"]
        if primary == "starbattle_constraint_validation": return [primary, "starbattle_region_quota_analysis", "starbattle_solution_space_analysis"]
        return [primary or "starbattle_puzzle_summary", "starbattle_constraint_validation"]

    def _tool_name_for_scenario(self, scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_regions", "unsolvable_puzzle", "ambiguous_puzzle"}:
            return "starbattle_constraint_validation"
        if scenario.tool_usage == "candidate_scan" or scenario.task_category in {
            "hint",
            "next_best_move",
            "region_analysis",
        }:
            return "starbattle_candidate_analysis"
        if scenario.tool_usage == "constraint_check" or scenario.task_category in {
            "validity_check",
            "mistake_correction",
        }:
            return "starbattle_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "starbattle_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        if tool_name == "starbattle_candidate_analysis":
            return {
                "forced_stars": truth.get("forced_stars", []),
                "forced_empty": truth.get("forced_empty", []),
                "suggested_move": truth.get("suggested_move"),
            }
        if tool_name == "starbattle_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "solution_violations": truth.get("solution_violations", []),
                "region_evaluations": truth.get("region_evaluations", []),
            }
        if tool_name == "starbattle_solution_verification":
            return {
                "solution": truth.get("solution"),
                "solution_grid": truth.get("solution_grid"),
                "unique_solution_status": truth.get("unique_solution_status"),
            }
        if tool_name == "starbattle_rules_reference": return {"rules": ["Place the required stars in every row, column, and region.", "Stars cannot touch, including diagonally."]}
        if tool_name == "starbattle_puzzle_summary": return {"size": puzzle.metadata.get("size"), "stars_per_unit": puzzle.metadata.get("stars_per_unit"), "region_count": len(puzzle.metadata.get("regions", [])), "difficulty": puzzle.difficulty}
        size, stars, regions = parse_puzzle(puzzle.puzzle); forced_stars = set(truth.get("forced_stars", [])); forced_empty = set(truth.get("forced_empty", []))
        if tool_name == "starbattle_row_quota_analysis": return {"rows": [self._quota(f"row_{r + 1}", [f"r{r + 1}c{c + 1}" for c in range(size)], stars, forced_stars, forced_empty) for r in range(size)]}
        if tool_name == "starbattle_column_quota_analysis": return {"columns": [self._quota(f"column_{c + 1}", [f"r{r + 1}c{c + 1}" for r in range(size)], stars, forced_stars, forced_empty) for c in range(size)]}
        if tool_name == "starbattle_region_quota_analysis": return {"regions": [self._quota(f"region_{region['id']}", region["cells"], stars, forced_stars, forced_empty) for region in regions]}
        if tool_name == "starbattle_adjacency_exclusion_scan": return {"excluded_by_adjacency": self._adjacency_exclusions(size, forced_stars)}
        if tool_name == "starbattle_solution_space_analysis":
            solutions = solve_starbattle(size, stars, regions, limit=2) if not validate_structure(size, stars, regions) else []
            differences = [f"r{r + 1}c{c + 1}" for r in range(size) for c in range(size) if len(solutions) > 1 and solutions[0][r][c] != solutions[1][r][c]]
            return {"solution_count_capped": len(solutions), "cap": 2, "status": truth.get("solvability_status"), "witness_differences": differences}
        return {}

    @staticmethod
    def _quota(unit: str, cells: list[str], required: int, forced_stars: set[str], forced_empty: set[str]) -> dict[str, Any]:
        placed = len(set(cells) & forced_stars); available = sorted(set(cells) - forced_empty - forced_stars)
        return {"unit": unit, "required": required, "forced_star_count": placed, "remaining_stars": max(0, required - placed), "available_cells": available, "capacity_ok": placed + len(available) >= required}

    @staticmethod
    def _adjacency_exclusions(size: int, stars: set[str]) -> list[dict[str, str]]:
        result: dict[str, str] = {}
        for star in stars:
            row, col = parse_cell(star)
            for rr in range(max(0, row-1), min(size, row+2)):
                for cc in range(max(0, col-1), min(size, col+2)):
                    cell = f"r{rr + 1}c{cc + 1}"
                    if cell != star: result[cell] = star
        return [{"cell": cell, "excluded_by": result[cell]} for cell in sorted(result)]

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "starbattle_candidate_analysis": (
                "The scenario needs forced-star or non-touching candidate analysis."
            ),
            "starbattle_constraint_validation": (
                "The scenario needs deterministic row, column, region, and adjacency checks."
            ),
            "starbattle_solution_verification": (
                "The scenario needs solver-backed solution verification."
            ),
        }
        return reasons.get(
            tool_name,
            f"The {scenario.task_category} scenario requested Star Battle tool support.",
        )
