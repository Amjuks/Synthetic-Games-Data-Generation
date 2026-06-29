from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Scenario:
    scenario_id: str
    user_intent: str
    conversation_type: str
    num_turns: int
    user_expertise: str
    user_personality: str
    assistant_style: str
    tone: str
    difficulty: str
    task_category: str
    edge_case: str
    tool_usage: str
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PuzzleRecord:
    puzzle_id: str
    puzzle: str
    solution: str
    difficulty: str
    num_clues: int
    required_strategies: list[str]
    unique_solution_status: bool
    source: str
    canonical_signature: str
    usage_count: int
    parent_puzzle_id: str | None = None
    transformation: str = "identity"
    rendered_board: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimilarityResult:
    accepted: bool
    similarity_score: float
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetSample:
    sample_id: str
    scenario_id: str
    puzzle_id: str
    parent_puzzle_id: str | None
    conversation_type: str
    task: str
    difficulty: str
    turns: int
    similarity_score: float
    generation_model: str
    timestamp: str
    validation_status: str
    scenario: dict[str, Any]
    puzzle_metadata: dict[str, Any]
    conversation: dict[str, Any]
    output: dict[str, Any]
    edge_case: str
    user_expertise: str
    assistant_style: str
    tool_usage: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        sample_id: str,
        scenario: Scenario,
        puzzle: PuzzleRecord,
        conversation: dict[str, Any],
        output: dict[str, Any],
        similarity_score: float,
        generation_model: str,
        validation_status: str,
    ) -> "DatasetSample":
        turns = len(output.get("messages", [])) if output.get("conversation_type") == "multi_turn" else 1
        return cls(
            sample_id=sample_id,
            scenario_id=scenario.scenario_id,
            puzzle_id=puzzle.puzzle_id,
            parent_puzzle_id=puzzle.parent_puzzle_id,
            conversation_type=output.get("conversation_type", scenario.conversation_type),
            task=scenario.task_category,
            difficulty=scenario.difficulty,
            turns=turns,
            similarity_score=similarity_score,
            generation_model=generation_model,
            timestamp=utc_now_iso(),
            validation_status=validation_status,
            scenario=scenario.to_dict(),
            puzzle_metadata=puzzle.to_dict(),
            conversation=conversation,
            output=output,
            edge_case=scenario.edge_case,
            user_expertise=scenario.user_expertise,
            assistant_style=scenario.assistant_style,
            tool_usage=scenario.tool_usage,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
