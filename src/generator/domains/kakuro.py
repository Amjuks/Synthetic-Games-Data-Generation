from __future__ import annotations

from typing import Any

from ..domain_support import VariedDomainSupport, build_tool_usage, make_tool_catalog
from ..kakuro import KakuroPuzzleManager, parse_puzzle, solve_kakuro, validate_structure
from ..models import PuzzleRecord, Scenario, ValidationResult
from ..scenario import ScenarioGenerator


class KakuroScenarioGenerator(ScenarioGenerator):
    def _derive_user_intent(self, task_category: str, edge_case: str) -> str:
        intents = {
            "next_best_move": "ask_for_next_cross_sum_deduction",
            "validity_check": "verify_digit_or_run",
            "rules_explanation": "learn_kakuro_rules",
            "solve_puzzle": "request_solution_guidance",
            "run_analysis": "analyze_run_combinations",
            "hint": "ask_for_hint",
            "mistake_correction": "debug_sum_or_repeat_mistake",
            "technique_discussion": "discuss_kakuro_strategy",
            "beginner_question": "learn_kakuro_basics",
            "advanced_question": "analyze_crossing_run_constraints",
            "general_chat": "general_kakuro_help",
        }
        base = intents.get(task_category, task_category)
        return f"{base}_{edge_case}" if edge_case != "none" else base

    def _build_constraints(self, task_category: str, edge_case: str, tool_usage: str) -> list[str]:
        constraints = [
            "Use the supplied Kakuro grid and clues exactly unless malformed input is explicitly required.",
            "Ground every claim in distinct-digit run sums and crossing-run constraints.",
        ]
        if task_category == "hint":
            constraints.append("Prefer a constrained run or candidate elimination over revealing the full solution.")
        if edge_case == "incorrect_assumption":
            constraints.append("Correct the user's Kakuro assumption politely.")
        if edge_case == "malformed_input":
            constraints.append("Acknowledge the malformed grid before attempting to help.")
        if tool_usage != "none":
            constraints.append(f"Reference the requested tool usage mode: {tool_usage}.")
        return constraints


class KakuroValidator:
    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
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

        height, width, blocks, runs = parse_puzzle(puzzle.puzzle)
        violations = validate_structure(height, width, blocks, runs)
        truth = puzzle.ground_truth
        if scenario.edge_case == "none":
            if violations:
                errors.append("standard scenario has structurally invalid Kakuro clues")
            if truth.get("solvability_status") != "unique" or not puzzle.unique_solution_status:
                errors.append("standard scenario requires a unique Kakuro puzzle")
        if truth.get("solution") != puzzle.solution:
            errors.append("ground truth solution does not match puzzle solution")
        if scenario.edge_case == "invalid_clues" and not violations:
            errors.append("invalid_clues scenario has no structural violation")
        if scenario.edge_case == "unsolvable_puzzle" and truth.get("solvability_status") != "unsolvable":
            errors.append("unsolvable_puzzle scenario is not solver-confirmed unsolvable")
        if scenario.edge_case == "ambiguous_puzzle" and truth.get("solvability_status") != "ambiguous":
            errors.append("ambiguous_puzzle scenario is not solver-confirmed ambiguous")
        return ValidationResult(is_valid=not errors, errors=errors)


class KakuroDomainAdapter(VariedDomainSupport):
    name = "kakuro"
    tool_catalog = make_tool_catalog(name, {
        "kakuro_run_analysis": "Distinct-digit run combinations, cell candidates, and a deduction.", "kakuro_constraint_validation": "Clue structure, sums, repeats, and solver validation.",
        "kakuro_solution_verification": "Complete solver-backed grid verification.", "kakuro_rules_reference": "Canonical sum and no-repeat rules.", "kakuro_puzzle_summary": "Dimensions, run count, and difficulty.",
        "kakuro_run_topology": "Across/down run membership for every answer cell.", "kakuro_crossing_analysis": "Candidate values surviving intersecting runs.",
        "kakuro_run_feasibility": "Candidate counts and feasibility for every clue run.", "kakuro_move_impact_analysis": "Peer eliminations caused by the suggested digit.",
        "kakuro_solution_space_analysis": "Capped solution count and ambiguous witness differences.",
    })

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.prompts = config.get("prompts", {})
        self.scenario_generator = KakuroScenarioGenerator(config)
        self.puzzle_manager = KakuroPuzzleManager(config)
        self.validator = KakuroValidator()
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
        return build_tool_usage(scenario=scenario, puzzle=puzzle, tool_names=self._tool_bundle(scenario), input_for=lambda name: {"puzzle_id": puzzle.puzzle_id, "task_category": scenario.task_category, "edge_case": scenario.edge_case, "height": puzzle.metadata.get("height"), "width": puzzle.metadata.get("width"), "runs": puzzle.metadata.get("runs", [])}, output_for=lambda name: self._run_tool(name, puzzle))

    def validate(self, output: dict[str, Any], scenario: Scenario, puzzle: PuzzleRecord) -> ValidationResult:
        return self.validator.validate(output, scenario, puzzle)

    def prompt_problem(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        return {
            "puzzle_id": puzzle.puzzle_id,
            "puzzle": puzzle.puzzle,
            "difficulty": puzzle.difficulty,
            "required_strategies": puzzle.required_strategies,
        }

    def prompt_ground_truth(self, puzzle: PuzzleRecord) -> dict[str, Any]:
        return {
            key: value
            for key, value in puzzle.ground_truth.items()
            if key != "run_candidate_combinations"
        }

    def prompt_context(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain": self.name,
            "height": puzzle.metadata.get("height"),
            "width": puzzle.metadata.get("width"),
            "tools_required": puzzle.metadata.get("tools_required", []),
            "calls": [self._compact_tool_usage({"used": True, "tool_name": call["tool_name"], "tool_input": call["input"], "tool_output": call["output"], "reason": call["reason"]}) for call in tool_usage.get("calls", [])],
        }

    def generation_guidance(self) -> str:
        return (
            "Use the supplied Kakuro layout and across/down clues exactly. Every answer must respect each "
            "run's target sum, the no-repeat rule, and crossing-run candidates."
        )

    def flatten_sample_row(self, row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        metadata = sample.get("puzzle_metadata", {}).get("metadata", {})
        row["domain"] = sample.get("domain", self.name)
        row["grid_height"] = metadata.get("height")
        row["grid_width"] = metadata.get("width")
        row["runs"] = metadata.get("runs", [])
        row["tool_used"] = sample.get("tool_used", False)
        row["tool_name"] = sample.get("tool_usage_details", {}).get("tool_name")
        self._add_flat_tool_metadata(row, sample)
        return row

    def _tool_bundle(self, scenario: Scenario) -> list[str]:
        if scenario.edge_case == "malformed_input": return ["kakuro_constraint_validation", "kakuro_puzzle_summary", "kakuro_rules_reference"]
        if scenario.edge_case in {"invalid_clues", "unsolvable_puzzle", "ambiguous_puzzle"}: return ["kakuro_constraint_validation", "kakuro_run_feasibility", "kakuro_solution_space_analysis"]
        if scenario.task_category in {"rules_explanation", "beginner_question", "general_chat"}: return ["kakuro_rules_reference", "kakuro_puzzle_summary", "kakuro_run_topology"]
        if scenario.task_category in {"technique_discussion", "advanced_question"}: return ["kakuro_run_topology", "kakuro_crossing_analysis", "kakuro_run_feasibility", "kakuro_move_impact_analysis"]
        primary = self._tool_name_for_scenario(scenario)
        if primary == "kakuro_solution_verification": return ["kakuro_run_analysis", "kakuro_crossing_analysis", "kakuro_solution_space_analysis", primary]
        if primary == "kakuro_run_analysis": return [primary, "kakuro_run_feasibility", "kakuro_crossing_analysis", "kakuro_move_impact_analysis"]
        if primary == "kakuro_constraint_validation": return [primary, "kakuro_run_topology", "kakuro_solution_space_analysis"]
        return [primary or "kakuro_puzzle_summary", "kakuro_constraint_validation"]

    def _tool_name_for_scenario(self, scenario: Scenario) -> str | None:
        if scenario.edge_case in {"invalid_clues", "unsolvable_puzzle", "ambiguous_puzzle"}:
            return "kakuro_constraint_validation"
        if scenario.tool_usage == "run_candidate_scan" or scenario.task_category in {"hint", "next_best_move", "run_analysis"}:
            return "kakuro_run_analysis"
        if scenario.tool_usage == "sum_constraint_check" or scenario.task_category in {"validity_check", "mistake_correction"}:
            return "kakuro_constraint_validation"
        if scenario.tool_usage == "solution_verification" or scenario.task_category == "solve_puzzle":
            return "kakuro_solution_verification"
        return None

    def _run_tool(self, tool_name: str, puzzle: PuzzleRecord) -> dict[str, Any]:
        truth = puzzle.ground_truth
        if tool_name == "kakuro_run_analysis":
            return {
                "run_candidate_combinations": truth.get("run_candidate_combinations", {}),
                "cell_candidates": truth.get("cell_candidates", {}),
                "suggested_move": truth.get("suggested_move"),
            }
        if tool_name == "kakuro_constraint_validation":
            return {
                "validity_status": truth.get("validity_status"),
                "solvability_status": truth.get("solvability_status"),
                "structural_violations": truth.get("structural_violations", []),
                "run_evaluations": truth.get("run_evaluations", []),
            }
        if tool_name == "kakuro_solution_verification":
            return {
                "solution": truth.get("solution"),
                "solution_grid": truth.get("solution_grid"),
                "unique_solution_status": truth.get("unique_solution_status"),
            }
        if tool_name == "kakuro_rules_reference": return {"rules": ["Digits in each run sum to its clue.", "Digits cannot repeat within a run."]}
        if tool_name == "kakuro_puzzle_summary": return {"height": puzzle.metadata.get("height"), "width": puzzle.metadata.get("width"), "run_count": len(puzzle.metadata.get("runs", [])), "difficulty": puzzle.difficulty}
        height, width, blocks, runs = parse_puzzle(puzzle.puzzle)
        combinations = truth.get("run_candidate_combinations", {}); cell_candidates = truth.get("cell_candidates", {})
        if tool_name == "kakuro_run_topology":
            topology: dict[str, list[dict[str, Any]]] = {}
            for run in runs:
                for cell in run["cells"]: topology.setdefault(cell, []).append({"run_id": run["id"], "direction": run["direction"], "target": run["target"]})
            return {"cells": topology}
        if tool_name == "kakuro_crossing_analysis": return {"cell_candidates": cell_candidates, "crossing_cells": sorted(cell for cell in cell_candidates if sum(cell in run["cells"] for run in runs) > 1)}
        if tool_name == "kakuro_run_feasibility": return {"runs": [{"run_id": run["id"], "target": run["target"], "length": len(run["cells"]), "candidate_count": len(combinations.get(run["id"], [])), "feasible": bool(combinations.get(run["id"], []))} for run in runs]}
        if tool_name == "kakuro_move_impact_analysis": return {"suggested_move": truth.get("suggested_move"), "eliminations": self._move_impact(runs, cell_candidates, truth.get("suggested_move") or {})}
        if tool_name == "kakuro_solution_space_analysis":
            solutions = solve_kakuro(height, width, blocks, runs, limit=2) if not validate_structure(height, width, blocks, runs) else []
            differences = sorted(cell for cell in set((solutions[0] if solutions else {})) | set((solutions[1] if len(solutions) > 1 else {})) if len(solutions) > 1 and solutions[0].get(cell) != solutions[1].get(cell))
            return {"solution_count_capped": len(solutions), "cap": 2, "status": truth.get("solvability_status"), "witness_differences": differences}
        return {}

    @staticmethod
    def _move_impact(runs: list[dict[str, Any]], candidates: dict[str, list[int]], move: dict[str, Any]) -> list[dict[str, Any]]:
        cell, value = move.get("cell"), move.get("value")
        if not cell or value is None: return []
        peers = {peer for run in runs if cell in run["cells"] for peer in run["cells"] if peer != cell}
        return [{"cell": peer, "removed_value": value, "reason": "no_repeat_in_run"} for peer in sorted(peers) if value in candidates.get(peer, [])]

    def _compact_tool_usage(self, tool_usage: dict[str, Any]) -> dict[str, Any]:
        compact = dict(tool_usage)
        output = dict(compact.get("tool_output") or {})
        combinations = output.get("run_candidate_combinations")
        if isinstance(combinations, dict):
            output["run_candidate_combinations"] = {
                run_id: {"count": len(values), "examples": values[:20]}
                for run_id, values in combinations.items()
            }
        compact["tool_output"] = output
        return compact

    def _tool_reason(self, tool_name: str, scenario: Scenario) -> str:
        reasons = {
            "kakuro_run_analysis": "The scenario needs distinct-digit combinations or a crossing-run deduction.",
            "kakuro_constraint_validation": "The scenario needs deterministic clue, sum, and no-repeat checks.",
            "kakuro_solution_verification": "The scenario needs solver-backed solution verification.",
        }
        return reasons.get(tool_name, f"The {scenario.task_category} scenario requested Kakuro tool support.")
