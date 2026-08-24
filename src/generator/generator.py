from __future__ import annotations

import hashlib
import json
import re
import traceback
from typing import Any

from .config import get_config
from .diversity import SimilarityDiversityChecker
from .domains import get_domain_adapter
from .generation_log import GenerationEventLogger
from .jobs import JobManager
from .model_client import ModelClient
from .models import DatasetSample, PuzzleRecord, Scenario, utc_now_iso
from .storage import DatasetStorage


class ConversationGenerator:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or get_config()
        self.domain_name = self.config.get("domain", "sudoku")
        self.domain = get_domain_adapter(self.domain_name, self.config)
        self.prompts = self.domain.prompts
        self.model_client = ModelClient(self.config.get("model", {}))
        self.diversity_checker = SimilarityDiversityChecker(self.config)
        self.generation_model = self.config.get("model", {}).get("model_name", "unknown-model")
        self.max_regeneration_attempts = int(self.config.get("generation", {}).get("max_regeneration_attempts", 3))
        self.max_prompt_characters = int(
            self.config.get("generation", {}).get("max_prompt_characters", 50_000)
        )
        self.event_logger: GenerationEventLogger | None = None
        self.last_model_error_details: dict[str, Any] | None = None

    def run(
        self,
        samples: int,
        conversation_type: str = "both",
        max_turns: int = 6,
        job_name: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        if domain and domain != self.domain_name:
            raise ValueError(f"Generator was initialized for domain '{self.domain_name}', not '{domain}'.")
        job_manager = JobManager(job_name=job_name, config=self.config)
        self.event_logger = GenerationEventLogger(job_manager.job_dir, self.config)
        self.last_model_error_details = None
        status = job_manager.get_status()
        target_total = self._resolve_target_total(status, samples)
        resume_configuration_changes = self._validate_resume_config(
            status,
            conversation_type,
            max_turns,
            self.domain_name,
            target_total,
        )
        configuration_history = list(status.get("configuration_history", []))
        if resume_configuration_changes:
            configuration_history.append({
                "changed_at": utc_now_iso(),
                "completed_sample_indexes": int(status.get("completed", 0) or 0),
                "changes": resume_configuration_changes,
            })

        storage = DatasetStorage(job_manager.job_dir, self.config, domain_adapter=self.domain)
        history = storage.get_history()
        prepare_job = getattr(self.domain, "prepare_job", None)
        if callable(prepare_job):
            prepare_job(
                job_dir=job_manager.job_dir,
                accepted_history=history,
                rejected_history=storage.get_rejected_history(),
            )
        distribution_stats = self.diversity_checker.summarize_distribution(history)
        resume_state = job_manager.get_resume_state()
        completed = resume_state["completed"]
        accepted_samples = max(int(status.get("accepted_samples", 0) or 0), len(history))
        rejected_samples = max(int(status.get("rejected_samples", 0) or 0), storage.get_rejected_count())
        start_index = resume_state["next_sample_index"]

        job_manager.save_status(
            status="running",
            total=target_total,
            completed=completed,
            current_stage="generating",
            domain=self.domain_name,
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
            last_error_traceback=None,
            last_error_details=None,
            events_log_path=str(self.event_logger.events_path),
            generation_log_path=str(self.event_logger.text_path),
            resume_configuration_changes=resume_configuration_changes,
            configuration_history=configuration_history,
        )
        self.event_logger.log(
            "job_started",
            job_name=job_manager.job_name,
            domain=self.domain_name,
            requested_samples=samples,
            target_total=target_total,
            resume_sample_index=start_index,
            conversation_type=conversation_type,
            max_turns=max_turns,
            resume_configuration_changes=resume_configuration_changes,
        )
        if resume_configuration_changes:
            self.event_logger.log(
                "resume_configuration_changed",
                level="WARNING",
                job_name=job_manager.job_name,
                changes=resume_configuration_changes,
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
            self.event_logger.log("job_interrupted", level="WARNING", completed=completed)
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
            failure_traceback = traceback.format_exc()
            failure_event = self.event_logger.log(
                "job_failed",
                level="ERROR",
                completed=completed,
                error_type=type(exc).__name__,
                error=str(exc),
                model_error_details=self.last_model_error_details,
                traceback=failure_traceback,
            )
            error_details = dict(self.last_model_error_details or {})
            error_details.setdefault("event_id", failure_event["event_id"])
            error_details["events_log_path"] = str(self.event_logger.events_path)
            error_details["generation_log_path"] = str(self.event_logger.text_path)
            job_manager.save_status(
                status="failed",
                current_stage="error",
                completed=completed,
                next_sample_index=completed,
                accepted_samples=accepted_samples,
                rejected_samples=rejected_samples,
                distribution_stats=distribution_stats,
                last_error=f"{type(exc).__name__}: {exc}",
                last_error_traceback=failure_traceback,
                last_error_details=error_details,
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
            last_error_traceback=None,
            last_error_details=None,
        )
        self.event_logger.log(
            "job_completed",
            completed=completed,
            accepted_samples=accepted_samples,
            rejected_samples=rejected_samples,
        )
        return {
            "job_name": job_manager.job_name,
            "job_dir": str(job_manager.job_dir),
            "domain": self.domain_name,
            "completed": completed,
            "total": target_total,
            "accepted_samples": accepted_samples,
            "rejected_samples": rejected_samples,
            "metadata_path": str(storage.metadata_path),
            "events_log_path": str(self.event_logger.events_path),
            "generation_log_path": str(self.event_logger.text_path),
        }

    def generate_single_turn(self, sample_index: int) -> dict[str, Any]:
        scenario = self.domain.generate_scenario(
            sample_index=sample_index,
            conversation_type="single_turn",
            max_turns=1,
            distribution_stats={},
        )
        puzzle = self.domain.select_problem(scenario, sample_index)
        tool_usage = self.domain.maybe_use_tool(scenario, puzzle)
        output = self._generate_output(scenario, puzzle, tool_usage)
        return output

    def generate_multi_turn(self, sample_index: int) -> dict[str, Any]:
        scenario = self.domain.generate_scenario(
            sample_index=sample_index,
            conversation_type="multi_turn",
            max_turns=self.config.get("max_turns", 6),
            distribution_stats={},
        )
        puzzle = self.domain.select_problem(scenario, sample_index)
        tool_usage = self.domain.maybe_use_tool(scenario, puzzle)
        output = self._generate_output(scenario, puzzle, tool_usage)
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
        already_accepted = self._accepted_generation_types_for_index(history, sample_index)

        for generation_type in generation_types:
            if generation_type in already_accepted:
                if self.event_logger is not None:
                    self.event_logger.log(
                        "generation_type_already_accepted",
                        sample_index=sample_index,
                        generation_type=generation_type,
                    )
                continue
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

    def _accepted_generation_types_for_index(
        self,
        history: list[dict[str, Any]],
        sample_index: int,
    ) -> set[str]:
        accepted: set[str] = set()
        for sample in history:
            recorded_index = sample.get("metadata", {}).get("dataset_sample_index")
            if recorded_index is None:
                scenario_index = sample.get("scenario", {}).get("metadata", {}).get("sample_index")
                if scenario_index is None:
                    continue
                recorded_index = int(scenario_index) // self.max_regeneration_attempts
            if int(recorded_index) == sample_index:
                accepted.add(str(sample.get("conversation_type", "")))
        return accepted

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
            scenario = self.domain.generate_scenario(
                sample_index=sample_index * self.max_regeneration_attempts + attempt,
                conversation_type=generation_type,
                max_turns=max_turns,
                distribution_stats=distribution_stats,
            )
            puzzle = self.domain.select_problem(scenario, sample_index * self.max_regeneration_attempts + attempt)
            tool_usage = self.domain.maybe_use_tool(scenario, puzzle)
            scenario_sample_index = sample_index * self.max_regeneration_attempts + attempt
            output = self._generate_output(
                scenario,
                puzzle,
                tool_usage,
                request_context={
                    "sample_index": sample_index,
                    "scenario_sample_index": scenario_sample_index,
                    "attempt": attempt,
                    "generation_type": generation_type,
                },
            )
            validation_result = self.domain.validate(output, scenario, puzzle)
            tool_validator = getattr(self.domain, "tool_validation_errors", None)
            if callable(tool_validator):
                validation_result.errors.extend(tool_validator(puzzle))
                validation_result.is_valid = not validation_result.errors
            if not validation_result.is_valid:
                self._release_problem(puzzle)
                rejected_count += 1
                rejection = self._build_rejection_record(
                    sample_index=sample_index,
                    attempt=attempt,
                    scenario=scenario,
                    puzzle=puzzle,
                    tool_usage=tool_usage,
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
                domain=self.domain_name,
                scenario=scenario,
                puzzle=puzzle,
                conversation=conversation,
                output=output,
                tool_usage_details=tool_usage,
                similarity_score=0.0,
                generation_model=self.generation_model,
                validation_status="passed",
            )
            candidate_dict = candidate_sample.to_dict()
            candidate_dict["metadata"] = {
                **candidate_dict.get("metadata", {}),
                "dataset_sample_index": sample_index,
                "generation_attempt": attempt,
            }
            metadata_projector = getattr(self.domain, "sample_metadata", None)
            if callable(metadata_projector):
                candidate_dict["metadata"].update(metadata_projector(scenario, puzzle, tool_usage))
            similarity_result = self.diversity_checker.assess(candidate_dict, history)
            if not similarity_result.accepted:
                self._release_problem(puzzle)
                rejected_count += 1
                rejection = self._build_rejection_record(
                    sample_index=sample_index,
                    attempt=attempt,
                    scenario=scenario,
                    puzzle=puzzle,
                    tool_usage=tool_usage,
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
            accept = getattr(self.domain, "accept_problem", None)
            if callable(accept):
                accept(puzzle)
            self.domain.mark_problem_used(puzzle)
            return candidate_dict, rejected_count

        raise RuntimeError(
            f"Unable to generate a valid diverse {generation_type} sample for sample_index={sample_index} "
            f"after {self.max_regeneration_attempts} attempts. "
            f"Last rejection: {self._rejection_summary(rejection_reasons)}. "
            f"See {storage.rejected_path} for complete details."
        )

    def _release_problem(self, puzzle: PuzzleRecord) -> None:
        release = getattr(self.domain, "release_problem", None)
        if callable(release):
            release(puzzle)

    def _rejection_summary(self, rejections: list[dict[str, Any]]) -> str:
        if not rejections:
            return "unknown"
        rejection = rejections[-1]
        return (
            f"type={rejection.get('rejection_type', 'unknown')}, "
            f"attempt={rejection.get('attempt', 'unknown')}, "
            f"reasons={rejection.get('reasons', [])}"
        )

    def _generate_output(
        self,
        scenario: Scenario,
        puzzle: PuzzleRecord,
        tool_usage: dict[str, Any],
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = self._build_generation_prompt(scenario, puzzle, tool_usage)
        context = {
            **(request_context or {}),
            "domain": self.domain_name,
            "scenario_id": scenario.scenario_id,
            "task_category": scenario.task_category,
            "edge_case": scenario.edge_case,
            "puzzle_id": puzzle.puzzle_id,
            "parent_puzzle_id": puzzle.parent_puzzle_id,
        }
        prompt_details = {
            "prompt_characters": len(prompt),
            "prompt_bytes_utf8": len(prompt.encode("utf-8")),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }
        request = self._describe_model_request(prompt)
        if self.event_logger is not None:
            self.event_logger.log(
                "model_request_started",
                context=context,
                **prompt_details,
                request=request,
            )
        try:
            raw_output = self.model_client.generate(prompt)
        except Exception as exc:
            error_traceback = traceback.format_exc()
            if self.event_logger is not None:
                event = self.event_logger.log(
                    "model_request_failed",
                    level="ERROR",
                    context=context,
                    **prompt_details,
                    request=request,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    traceback=error_traceback,
                )
                event_id = event["event_id"]
            else:
                event_id = None
            self.last_model_error_details = {
                "event_id": event_id,
                "context": context,
                **prompt_details,
                "provider": request.get("provider"),
                "model": request.get("model"),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            raise
        if self.event_logger is not None:
            self.event_logger.log(
                "model_request_succeeded",
                context=context,
                **prompt_details,
                response_characters=len(raw_output),
                raw_response=raw_output,
            )
        output = self._parse_output(raw_output, scenario, puzzle)
        normalize_output = getattr(self.domain, "normalize_output", None)
        if callable(normalize_output):
            output = normalize_output(output, scenario, puzzle)
        return output

    def _describe_model_request(self, prompt: str) -> dict[str, Any]:
        describe = getattr(self.model_client, "describe_request", None)
        if callable(describe):
            return describe(prompt)
        model_config = self.config.get("model", {})
        return {
            "provider": model_config.get("provider", "test_or_custom"),
            "model": model_config.get("model_name", self.generation_model),
            "payload": {"input": prompt},
        }

    def _build_generation_prompt(self, scenario: Scenario, puzzle: PuzzleRecord, tool_usage: dict[str, Any]) -> str:
        output_schema = (
            "Return minified JSON with keys: prompt, response."
            if scenario.conversation_type == "single_turn"
            else "Return minified JSON with key: messages. "
                 "messages must be a list of objects with keys: user, response."
        )
        prompt_template = (
            self.prompts.get("single_turn_prompt", "")
            if scenario.conversation_type == "single_turn"
            else self.prompts.get("multi_turn_prompt", "").format(max_turns=scenario.num_turns)
        )
        problem_projector = getattr(self.domain, "prompt_problem", None)
        if callable(problem_projector):
            prompt_problem = problem_projector(puzzle)
        else:
            prompt_problem = puzzle.to_dict()
            prompt_problem.pop("ground_truth", None)
        truth_projector = getattr(self.domain, "prompt_ground_truth", None)
        prompt_ground_truth = (
            truth_projector(puzzle) if callable(truth_projector) else puzzle.ground_truth
        )
        prompt = (
            f"{self.prompts.get('system_prompt', '')}\n\n"
            f"{prompt_template}\n\n"
            f"Domain: {self.domain_name}\n\n"
            f"Scenario JSON:\n{json.dumps(scenario.to_dict(), ensure_ascii=False)}\n\n"
            f"Puzzle JSON:\n{json.dumps(prompt_problem, ensure_ascii=False)}\n\n"
            f"Ground Truth JSON:\n{json.dumps(prompt_ground_truth, ensure_ascii=False)}\n\n"
            f"Tool Context JSON:\n{json.dumps(self.domain.prompt_context(scenario, puzzle, tool_usage), ensure_ascii=False)}\n\n"
            f"Puzzle board:\n{puzzle.rendered_board}\n\n"
            f"Output schema:\n{output_schema}\n"
            f"{self.domain.generation_guidance()}\n"
            f"Diversity key: {scenario.scenario_id}. Use this key only to choose distinctive wording, "
            "an uncommon but natural opening, and a different explanation order or conversational progression. "
            "Do not copy the key into the output. Avoid generic reusable boilerplate.\n"
            "Do not return the puzzle board, category, or conversation type; the pipeline attaches those fields.\n"
        )
        if self.max_prompt_characters > 0 and len(prompt) > self.max_prompt_characters:
            message = (
                "Prompt exceeds the configured local context budget: "
                f"domain={self.domain_name}, puzzle_id={puzzle.puzzle_id}, "
                f"scenario_id={scenario.scenario_id}, characters={len(prompt)}, "
                f"limit={self.max_prompt_characters}. The model API was not called."
            )
            if self.event_logger is not None:
                self.event_logger.log(
                    "prompt_budget_exceeded",
                    level="ERROR",
                    domain=self.domain_name,
                    puzzle_id=puzzle.puzzle_id,
                    scenario_id=scenario.scenario_id,
                    task_category=scenario.task_category,
                    edge_case=scenario.edge_case,
                    prompt_characters=len(prompt),
                    max_prompt_characters=self.max_prompt_characters,
                    prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    prompt=prompt,
                )
            raise ValueError(message)
        return prompt

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
                "category": scenario.task_category,
                "messages": self._normalize_messages(payload),
                "board": puzzle.rendered_board,
            }
        else:
            payload = {
                "conversation_type": "single_turn",
                "category": scenario.task_category,
                "prompt": payload.get("prompt", payload.get("user", "")),
                "response": payload.get("response", payload.get("assistant", "")),
                "board": puzzle.rendered_board,
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
        tool_usage: dict[str, Any],
        output: dict[str, Any],
        reasons: list[str],
        rejection_type: str,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "timestamp": utc_now_iso(),
            "sample_index": sample_index,
            "attempt": attempt,
            "domain": self.domain_name,
            "rejection_type": rejection_type,
            "reasons": reasons,
            "metrics": metrics or {},
            "scenario": scenario.to_dict(),
            "puzzle_metadata": puzzle.to_dict(),
            "ground_truth": puzzle.ground_truth,
            "tool_used": bool(tool_usage.get("used", False)),
            "tool_usage_details": tool_usage,
            "output": output,
        }

    def _resolve_target_total(self, status: dict[str, Any], requested_samples: int) -> int:
        completed = int(status.get("completed", 0) or 0)
        return max(completed, requested_samples)

    def _validate_resume_config(
        self,
        status: dict[str, Any],
        conversation_type: str,
        max_turns: int,
        domain: str,
        target_total: int,
    ) -> dict[str, dict[str, Any]]:
        """Describe resume-setting changes without preventing the job from continuing."""
        requested = {
            "domain": domain,
            "conversation_type": conversation_type,
            "max_turns": int(max_turns),
            "total": int(target_total),
        }
        changes: dict[str, dict[str, Any]] = {}
        for key, current_value in requested.items():
            previous_value = status.get(key)
            if previous_value is None:
                continue
            comparable_previous = int(previous_value) if key in {"max_turns", "total"} else previous_value
            if comparable_previous != current_value:
                changes[key] = {"previous": previous_value, "current": current_value}
        return changes
