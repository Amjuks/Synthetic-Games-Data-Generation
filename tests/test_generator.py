import csv
import json

from src.generator.generator import ConversationGenerator


class StubModelClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def generate(self, prompt: str) -> str:
        return json.dumps(self.payload)


class SequenceModelClient:
    def __init__(self, payloads: list[dict], fail_on_call: int | None = None):
        self.payloads = payloads
        self.fail_on_call = fail_on_call
        self.call_count = 0

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise RuntimeError("synthetic failure")
        payload = self.payloads[(self.call_count - 1) % len(self.payloads)]
        return json.dumps(payload)


def make_config() -> dict:
    return {
        "max_turns": 4,
        "model": {},
        "prompts": {
            "system_prompt": "system",
            "single_turn_prompt": "single",
            "multi_turn_prompt": "multi {max_turns}",
            "conversation_categories": ["next_best_move"],
        },
    }


def test_single_turn_normalizes_to_prompt_response_and_preserves_board():
    generator = ConversationGenerator(make_config())
    generator.model_client = StubModelClient(
        {
            "conversation_type": "single_turn",
            "category": "next_best_move",
            "user": "What should I fill next?",
            "assistant": "Check row 1, column 3.",
        }
    )

    row = generator.generate_single_turn(0)

    assert row["prompt"] == "What should I fill next?"
    assert row["response"] == "Check row 1, column 3."
    assert "5 3 ." in row["board"]
    assert row["conversation_type"] == "single_turn"


def test_multi_turn_normalizes_turns_to_message_pairs():
    generator = ConversationGenerator(make_config())
    generator.model_client = StubModelClient(
        {
            "conversation_type": "multi_turn",
            "category": "next_best_move",
            "turns": [
                {"role": "user", "content": "What should I inspect first?"},
                {"role": "assistant", "content": "Start with the top-left box."},
                {"role": "user", "content": "What number looks constrained there?"},
                {"role": "assistant", "content": "Check where 9 can go."},
            ],
        }
    )

    row = generator.generate_multi_turn(1)

    assert row["conversation_type"] == "multi_turn"
    assert row["messages"] == [
        {"user": "What should I inspect first?", "response": "Start with the top-left box."},
        {"user": "What number looks constrained there?", "response": "Check where 9 can go."},
    ]
    assert "------+-------+------" in row["board"]


def test_single_turn_prompt_includes_full_board_when_category_needs_it():
    generator = ConversationGenerator(make_config())

    prompt = generator._build_single_turn_prompt(
        sample_index=0,
        category="next_best_move",
        board=generator._get_board_for_category("next_best_move", 0),
    )

    assert "Sudoku board:" in prompt
    assert "5 3 . | . 7 . | . . ." in prompt
    assert "6 . . | 1 9 5 | . . ." in prompt


def test_run_appends_each_sample_and_updates_progress(tmp_path):
    config = make_config() | {"output_path": str(tmp_path), "output_dir": str(tmp_path)}
    generator = ConversationGenerator(config)
    generator.model_client = SequenceModelClient(
        [
            {
                "conversation_type": "single_turn",
                "category": "next_best_move",
                "prompt": "P1",
                "response": "R1",
            }
        ]
    )
    result = generator.run(samples=2, conversation_type="single_turn", job_name="resume-job")

    single_turn_csv = tmp_path / "resume-job" / "single_turn.csv"
    progress = json.loads((tmp_path / "resume-job" / "progress.json").read_text(encoding="utf-8"))

    with single_turn_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert result["completed"] == 2
    assert progress["status"] == "completed"
    assert progress["completed"] == 2
    assert progress["next_sample_index"] == 2


def test_run_resumes_after_failure(tmp_path):
    config = make_config() | {"output_path": str(tmp_path), "output_dir": str(tmp_path)}
    generator = ConversationGenerator(config)

    payloads = [
        {"conversation_type": "single_turn", "category": "next_best_move", "prompt": "P0", "response": "R0"},
        {"conversation_type": "single_turn", "category": "next_best_move", "prompt": "P1", "response": "R1"},
        {"conversation_type": "single_turn", "category": "next_best_move", "prompt": "P2", "response": "R2"},
    ]
    failing_client = SequenceModelClient(payloads, fail_on_call=2)
    generator.model_client = failing_client

    try:
        generator.run(samples=3, conversation_type="single_turn", job_name="resume-job")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected the first run to fail")

    progress_path = tmp_path / "resume-job" / "progress.json"
    first_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    single_turn_csv = tmp_path / "resume-job" / "single_turn.csv"
    assert first_progress["status"] == "failed"
    assert first_progress["completed"] == 1
    with single_turn_csv.open("r", encoding="utf-8", newline="") as f:
        first_rows = list(csv.DictReader(f))

    assert len(first_rows) == 1

    resumed_generator = ConversationGenerator(config)
    resumed_generator.model_client = SequenceModelClient(payloads)
    resumed_generator.run(samples=3, conversation_type="single_turn", job_name="resume-job")

    final_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    with single_turn_csv.open("r", encoding="utf-8", newline="") as f:
        final_rows = list(csv.DictReader(f))

    assert final_progress["status"] == "completed"
    assert final_progress["completed"] == 3
    assert final_progress["single_turn_count"] == 3
    assert len(final_rows) == 3
