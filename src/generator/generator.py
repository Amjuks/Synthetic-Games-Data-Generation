from __future__ import annotations

import hashlib
import json
import re
import traceback
from typing import Any

from .config import get_config
from .diversity import SimilarityDiversityChecker
from .jobs import JobManager
from .model_client import ModelClient
from .models import DatasetSample, PuzzleRecord, Scenario, utc_now_iso
from .puzzles import PuzzleManager
from .scenario import ScenarioGenerator
from .storage import DatasetStorage
from .validation import SampleValidator


class ConversationGenerator:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or get_config()
        self.prompts = self.config.get("prompts", {})
        self.model_client = ModelClient(self.config.get("model", {}))
        self.scenario_generator = ScenarioGenerator(self.config)
        self.puzzle_manager = PuzzleManager(self.config)
        self.validator = SampleValidator()
        self.diversity_checker = SimilarityDiversityChecker(self.config)
        self.generation_model = self.config.get("model", {}).get("model_name", "unknown-model")
        self.max_regeneration_attempts = int(self.config.get("generation", {}).get("max_regeneration_attempts", 3))

    def run(self, samples: int, conversation_type: str = "both", max_turns: int = 6, job_name: str | None = None) -> dict[str, Any]:
        job_manager = JobManager(job_name=job_name, config=self.config)
        status = job_manager.get_status()
        target_total = self._resolve_target_total(status, samples)
        self._validate_resume_config(status, conversation_type, max_turns)

        storage = DatasetStorage(job_manager.job_dir, self.config)
        history = storage.get_history()
        distribution_stats = self.diversity_checker.summarize_distribution(history)
        resume_state = job_manager.get_resume_state()
        completed = resume_state["completed"]
        accepted_samples = int(status.get("accepted_samples", len(history)) or len(history))
        rejected_samples = max(int(status.get("rejected_samples", 0) or 0), storage.get_rejected_count())
        start_index = resume_state["next_sample_index"]

        job_manager.save_status(
            status="running",
            total=target_total,
            completed=completed,
            current_stage="generating",
            conversation_type=conversation_type,
            max_turns=max_turns,
            next_sample_index=start_index,
            accepted_samples=accepted_samples,
            rejected_samples=rejected_samples,
            metadata_path=str(storage.metadata_path),
            rejected_path=str(storage.rejected_path),
            single_turn_path=str(storage.single_turn_path),
            multi_turn_path=str(storage.multi_turn_path),
            distribution_stats=distribution_stats,
            last_error=None,
        )

        try:
            for sample_index in range(start_index, target_total):
                generated_records = self._generate_records_for_index(
                    sample_index=sample_index,
                    conversation_type=conversation_type,
                    max_turns=max_turns,
                    distribution_stats=distribution_stats,
                    history=history,
                    storage=storage,
                )
                history.extend(generated_records["accepted"])
                accepted_samples += len(generated_records["accepted"])
                rejected_samples = storage.get_rejected_count()
                distribution_stats = self.diversity_checker.summarize_distribution(history)

                completed = sample_index + 1
                job_manager.save_status(
                    completed=completed,
                    next_sample_index=completed,
                    accepted_samples=accepted_samples,
                    rejected_samples=rejected_samples,
                    distribution_stats=distribution_stats,
                    status="running",
                    current_stage="generating",
                )
        except KeyboardInterrupt:
            rejected_samples = storage.get_rejected_count()
            job_manager.save_status(
                status="stopped",
                current_stage="interrupted",
                completed=completed,
                next_sample_index=completed,
                accepted_samples=accepted_samples,
                rejected_samples=rejected_samples,
                distribution_stats=distribution_stats,
                last_error="Generation stopped by user.",
            )
            raise
        except Exception as exc:
            rejected_samples = storage.get_rejected_count()
            job_manager.save_status(
                status="failed",
                current_stage="error",
                completed=completed,
                next_sample_index=completed,
                accepted_samples=accepted_samples,
                rejected_samples=rejected_samples,
                distribution_stats=distribution_stats,
                last_error=f"{type(exc).__name__}: {exc}",
                last_error_traceback=traceback.format_exc(),
            )
            raise

        job_manager.save_status(
            status="completed",
            total=target_total,
            completed=completed,
            next_sample_index=completed,
            current_stage="finished",
            accepted_samples=accepted_samples,
            rejected_samples=rejected_samples,
            distribution_stats=distribution_stats,
            last_error=None,
        )
        return {
            "job_name": job_manager.job_name,
            "job_dir": str(job_manager.job_dir),
            "completed": completed,
            "total": target_total,
            "accepted_samples": accepted_samples,
            "rejected_samples": rejected_samples,
            "metadata_path": str(storage.metadata_path),
        }

    def generate_single_turn(self, sample_index: int) -> dict[str, Any]:
        scenario = self.scenario_generator.generate(
            sample_index=sample_index,
            conversation_type="single_turn",
            max_turns=1,
            distribution_stats={},
        )
        puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index)
        output = self._generate_output(scenario, puzzle)
        return output

    def generate_multi_turn(self, sample_index: int) -> dict[str, Any]:
        scenario = self.scenario_generator.generate(
            sample_index=sample_index,
            conversation_type="multi_turn",
            max_turns=self.config.get("max_turns", 6),
            distribution_stats={},
        )
        puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index)
        output = self._generate_output(scenario, puzzle)
        return output

    def _generate_records_for_index(
        self,
        *,
        sample_index: int,
        conversation_type: str,
        max_turns: int,
        distribution_stats: dict[str, Any],
        history: list[dict[str, Any]],
        storage: DatasetStorage,
    ) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected = 0
        generation_types = ["single_turn", "multi_turn"] if conversation_type == "both" else [conversation_type]

        for generation_type in generation_types:
            accepted_sample, rejected_count = self._generate_validated_sample(
                sample_index=sample_index,
                generation_type=generation_type,
                max_turns=max_turns,
                distribution_stats=distribution_stats,
                history=history + accepted,
                storage=storage,
            )
            accepted.append(accepted_sample)
            rejected += rejected_count

        return {"accepted": accepted, "rejected": rejected}

    def _generate_validated_sample(
        self,
        *,
        sample_index: int,
        generation_type: str,
        max_turns: int,
        distribution_stats: dict[str, Any],
        history: list[dict[str, Any]],
        storage: DatasetStorage,
    ) -> tuple[dict[str, Any], int]:
        rejected_count = 0
        rejection_reasons: list[dict[str, Any]] = []

        for attempt in range(self.max_regeneration_attempts):
            scenario = self.scenario_generator.generate(
                sample_index=sample_index * self.max_regeneration_attempts + attempt,
                conversation_type=generation_type,
                max_turns=max_turns,
                distribution_stats=distribution_stats,
            )
            puzzle = self.puzzle_manager.select_puzzle(scenario, sample_index * self.max_regeneration_attempts + attempt)
            output = self._generate_output(scenario, puzzle)
            validation_result = self.validator.validate(output, scenario, puzzle)
            if not validation_result.is_valid:
                rejected_count += 1
                rejection = self._build_rejection_record(
                    sample_index=sample_index,
                    attempt=attempt,
                    scenario=scenario,
                    puzzle=puzzle,
                    output=output,
                    reasons=validation_result.errors,
                    rejection_type="validation",
                )
                storage.append_rejected_sample(rejection)
                rejection_reasons.append(rejection)
                continue

            conversation = self._conversation_payload(output)
            sample_id = self._build_sample_id(sample_index, generation_type, scenario, puzzle)
            candidate_sample = DatasetSample.create(
                sample_id=sample_id,
                scenario=scenario,
                puzzle=puzzle,
                conversation=conversation,
                output=output,
                similarity_score=0.0,
                generation_model=self.generation_model,
                validation_status="passed",
            )
            candidate_dict = candidate_sample.to_dict()
            similarity_result = self.diversity_checker.assess(candidate_dict, history)
            if not similarity_result.accepted:
                rejected_count += 1
                rejection = self._build_rejection_record(
                    sample_index=sample_index,
                    attempt=attempt,
                    scenario=scenario,
                    puzzle=puzzle,
                    output=output,
                    reasons=similarity_result.reasons,
                    rejection_type="similarity",
                    metrics=similarity_result.metrics,
                )
                storage.append_rejected_sample(rejection)
                rejection_reasons.append(rejection)
                continue

            candidate_dict["similarity_score"] = similarity_result.similarity_score
            storage.append_sample(candidate_dict)
            self.puzzle_manager.mark_used(puzzle)
            return candidate_dict, rejected_count

        raise RuntimeError(
            f"Unable to generate a valid diverse {generation_type} sample for sample_index={sample_index} "
            f"after {self.max_regeneration_attempts} attempts. Last rejection: {rejection_reasons[-1] if rejection_reasons else 'unknown'}"
        )

    def _generate_output(self, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        prompt = self._build_generation_prompt(scenario, puzzle)
        raw_output = self.model_client.generate(prompt)
        return self._parse_output(raw_output, scenario, puzzle)

    def _build_generation_prompt(self, scenario: Scenario, puzzle: PuzzleRecord) -> str:
        output_schema = (
            "Return minified JSON with keys: prompt, response, conversation_type, category, board."
            if scenario.conversation_type == "single_turn"
            else "Return minified JSON with keys: messages, conversation_type, category, board. "
                 "messages must be a list of objects with keys: user, response."
        )
        prompt_template = (
            self.prompts.get("single_turn_prompt", "")
            if scenario.conversation_type == "single_turn"
            else self.prompts.get("multi_turn_prompt", "").format(max_turns=scenario.num_turns)
        )
        return (
            f"{self.prompts.get('system_prompt', '')}\n\n"
            f"{prompt_template}\n\n"
            f"Scenario JSON:\n{json.dumps(scenario.to_dict(), ensure_ascii=False)}\n\n"
            f"Puzzle JSON:\n{json.dumps(puzzle.to_dict(), ensure_ascii=False)}\n\n"
            f"Puzzle board:\n{puzzle.rendered_board}\n\n"
            f"Output schema:\n{output_schema}\n"
            "Use the supplied puzzle exactly and never invent or modify the board unless the scenario explicitly requires malformed input.\n"
            "Keep the task category aligned with the scenario.\n"
        )

    def _parse_output(self, raw_output: str, scenario: Scenario, puzzle: PuzzleRecord) -> dict[str, Any]:
        payload = self._load_json_payload(raw_output)
        if payload is None:
            payload = {
                "conversation_type": scenario.conversation_type,
                "category": scenario.task_category,
                "prompt": raw_output,
                "response": raw_output,
                "messages": [{"user": raw_output, "response": raw_output}],
                "board": puzzle.rendered_board,
            }

        if scenario.conversation_type == "multi_turn":
            payload = {
                "conversation_type": "multi_turn",
                "category": payload.get("category", scenario.task_category),
                "messages": self._normalize_messages(payload),
                "board": payload.get("board", puzzle.rendered_board),
            }
        else:
            payload = {
                "conversation_type": "single_turn",
                "category": payload.get("category", scenario.task_category),
                "prompt": payload.get("prompt", payload.get("user", "")),
                "response": payload.get("response", payload.get("assistant", "")),
                "board": payload.get("board", puzzle.rendered_board),
            }
        return payload

    def _load_json_payload(self, raw_output: str) -> dict[str, Any] | None:
        candidates = [raw_output.strip()]
        fenced_match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", raw_output, flags=re.DOTALL)
        if fenced_match:
            candidates.append(fenced_match.group(1).strip())

        start_object = raw_output.find("{")
        end_object = raw_output.rfind("}")
        if start_object != -1 and end_object != -1 and end_object > start_object:
            candidates.append(raw_output[start_object:end_object + 1].strip())

        for candidate in candidates:
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _normalize_messages(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        messages = payload.get("messages")
        if isinstance(messages, list):
            normalized = [self._normalize_message_pair(item) for item in messages]
            normalized = [item for item in normalized if item["user"] or item["response"]]
            if normalized:
                return normalized

        turns = payload.get("turns")
        if isinstance(turns, list):
            normalized_turns: list[dict[str, str]] = []
            pending_user = ""
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                role = str(turn.get("role", "")).lower()
                content = str(turn.get("content", ""))
                if role == "user":
                    if pending_user:
                        normalized_turns.append({"user": pending_user, "response": ""})
                    pending_user = content
                elif role in {"assistant", "response"}:
                    normalized_turns.append({"user": pending_user, "response": content})
                    pending_user = ""
            if pending_user:
                normalized_turns.append({"user": pending_user, "response": ""})
            if normalized_turns:
                return normalized_turns

        prompt = payload.get("prompt", payload.get("user", ""))
        response = payload.get("response", payload.get("assistant", ""))
        return [{"user": str(prompt), "response": str(response)}]

    def _normalize_message_pair(self, item: Any) -> dict[str, str]:
        if not isinstance(item, dict):
            return {"user": str(item), "response": ""}
        if "user" in item or "response" in item:
            return {
                "user": str(item.get("user", "")),
                "response": str(item.get("response", "")),
            }
        return {
            "user": str(item.get("prompt", item.get("assistant", "" if "role" in item else ""))),
            "response": str(item.get("response", item.get("content", "" if "role" in item else ""))),
        }

    def _conversation_payload(self, output: dict[str, Any]) -> dict[str, Any]:
        if output.get("conversation_type") == "multi_turn":
            return {"messages": output.get("messages", [])}
        return {"prompt": output.get("prompt", ""), "response": output.get("response", "")}

    def _build_sample_id(self, sample_index: int, generation_type: str, scenario: Scenario, puzzle: PuzzleRecord) -> str:
        return hashlib.sha1(
            f"{sample_index}|{generation_type}|{scenario.scenario_id}|{puzzle.puzzle_id}|{utc_now_iso()}".encode("utf-8")
        ).hexdigest()[:20]

    def _build_rejection_record(
        self,
        *,
        sample_index: int,
        attempt: int,
        scenario: Scenario,
        puzzle: PuzzleRecord,
        output: dict[str, Any],
        reasons: list[str],
        rejection_type: str,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "timestamp": utc_now_iso(),
            "sample_index": sample_index,
            "attempt": attempt,
            "rejection_type": rejection_type,
            "reasons": reasons,
            "metrics": metrics or {},
            "scenario": scenario.to_dict(),
            "puzzle_metadata": puzzle.to_dict(),
            "output": output,
        }

    def _resolve_target_total(self, status: dict[str, Any], requested_samples: int) -> int:
        existing_total = int(status.get("total", 0) or 0)
        if existing_total > requested_samples:
            return existing_total
        return requested_samples

    def _validate_resume_config(self, status: dict[str, Any], conversation_type: str, max_turns: int) -> None:
        existing_conversation_type = status.get("conversation_type")
        if existing_conversation_type and existing_conversation_type != conversation_type:
            raise ValueError(
                f"Job '{status.get('job_name')}' was started with conversation_type="
                f"'{existing_conversation_type}', not '{conversation_type}'."
            )

        existing_max_turns = status.get("max_turns")
        if existing_max_turns is not None and int(existing_max_turns) != int(max_turns):
            raise ValueError(
                f"Job '{status.get('job_name')}' was started with max_turns={existing_max_turns}, not {max_turns}."
            )
