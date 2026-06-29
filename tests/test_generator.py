import csv
import json
import re

from src.generator.generator import ConversationGenerator


class StubModelClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def generate(self, prompt: str) -> str:
        return json.dumps(self.payload)


class IndexedModelClient:
    def __init__(self, payloads: dict[int, dict], fail_on_index: int | None = None):
        self.payloads = payloads
        self.fail_on_index = fail_on_index
        self.sorted_keys = sorted(payloads)

    def generate(self, prompt: str) -> str:
        match = re.search(r'"sample_index":\s*(\d+)', prompt)
        if not match:
            raise AssertionError("sample_index not found in prompt")
        sample_index = int(match.group(1))
        if self.fail_on_index is not None and sample_index == self.fail_on_index:
            raise RuntimeError("synthetic failure")
        payload_key = sample_index if sample_index in self.payloads else self.sorted_keys[sample_index % len(self.sorted_keys)]
        payload = dict(self.payloads[payload_key])
        category_match = re.search(r'"task_category":\s*"([^"]+)"', prompt)
        if category_match:
            payload["category"] = category_match.group(1)
        return json.dumps(payload)


def make_config(output_path: str) -> dict:
    return {
        "output_path": output_path,
        "output_dir": output_path,
        "max_turns": 4,
        "generation": {"random_seed": 17, "max_regeneration_attempts": 2},
        "model": {"model_name": "test-model"},
        "storage": {
            "metadata_filename": "samples.jsonl",
            "rejected_filename": "rejected_samples.jsonl",
            "stats_filename": "dataset_stats.json",
        },
        "puzzles": {
            "persistent_bank_filename": "puzzle_bank.jsonl",
            "usage_stats_filename": "puzzle_usage.json",
        },
        "similarity": {
            "max_history": 5000,
            "ngram_size": 3,
            "exact_duplicate_threshold": 1.0,
            "normalized_duplicate_threshold": 1.0,
            "ngram_overlap_threshold": 1.1,
            "embedding_similarity_threshold": 1.1,
            "structural_similarity_threshold": 1.1,
            "scenario_similarity_threshold": 1.1,
            "puzzle_similarity_threshold": 1.1,
        },
        "scenario": {
            "task_categories": ["next_best_move", "hint"],
            "difficulty_levels": ["easy", "medium"],
            "user_expertise_levels": ["beginner", "advanced"],
            "user_personalities": ["careful", "curious"],
            "assistant_styles": ["coaching", "concise"],
            "tones": ["neutral", "friendly"],
            "edge_cases": ["none", "incorrect_assumption"],
            "tool_usage_modes": ["none", "candidate_scan"],
        },
        "prompts": {
            "system_prompt": "system",
            "single_turn_prompt": "single",
            "multi_turn_prompt": "multi {max_turns}",
        },
    }


def test_single_turn_normalizes_to_prompt_response_and_preserves_board(tmp_path):
    generator = ConversationGenerator(make_config(str(tmp_path)))
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
    assert " | " in row["board"]
    assert row["conversation_type"] == "single_turn"


def test_multi_turn_normalizes_turns_to_message_pairs(tmp_path):
    generator = ConversationGenerator(make_config(str(tmp_path)))
    generator.model_client = StubModelClient(
        {
            "conversation_type": "multi_turn",
            "category": "hint",
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


def test_single_turn_parses_fenced_json_payload(tmp_path):
    generator = ConversationGenerator(make_config(str(tmp_path)))
    generator.model_client = StubModelClient(
        {
            "unused": "payload",
        }
    )

    scenario = generator.scenario_generator.generate(
        sample_index=0,
        conversation_type="single_turn",
        max_turns=1,
        distribution_stats={},
    )
    puzzle = generator.puzzle_manager.select_puzzle(scenario, 0)
    output = generator._parse_output(
        """```json
{"prompt":"Is r1c1 valid?","response":"No, because of the column.","conversation_type":"single_turn","category":"next_best_move","board":"BOARD"}
```""",
        scenario,
        puzzle,
    )

    assert output["prompt"] == "Is r1c1 valid?"
    assert output["response"] == "No, because of the column."


def test_run_writes_metadata_and_csv_incrementally(tmp_path):
    config = make_config(str(tmp_path))
    generator = ConversationGenerator(config)
    generator.model_client = IndexedModelClient(
        {
            0: {
                "conversation_type": "single_turn",
                "category": "next_best_move",
                "prompt": "P0",
                "response": "R0",
            },
            1: {
                "conversation_type": "single_turn",
                "category": "hint",
                "prompt": "P1",
                "response": "R1",
            },
        }
    )

    result = generator.run(samples=2, conversation_type="single_turn", job_name="resume-job")

    metadata_path = tmp_path / "resume-job" / "samples.jsonl"
    single_turn_csv = tmp_path / "resume-job" / "single_turn.csv"
    progress = json.loads((tmp_path / "resume-job" / "progress.json").read_text(encoding="utf-8"))

    with metadata_path.open("r", encoding="utf-8") as f:
        metadata_rows = [json.loads(line) for line in f if line.strip()]
    with single_turn_csv.open("r", encoding="utf-8", newline="") as f:
        csv_rows = list(csv.DictReader(f))

    assert result["completed"] == 2
    assert result["accepted_samples"] == 2
    assert len(metadata_rows) == 2
    assert len(csv_rows) == 2
    assert progress["status"] == "completed"
    assert progress["accepted_samples"] == 2
    assert progress["rejected_samples"] == 0


def test_run_resumes_after_failure(tmp_path):
    config = make_config(str(tmp_path))
    generator = ConversationGenerator(config)
    generator.model_client = IndexedModelClient(
        {
            0: {
                "conversation_type": "single_turn",
                "category": "next_best_move",
                "prompt": "P0",
                "response": "R0",
            },
            1: {
                "conversation_type": "single_turn",
                "category": "hint",
                "prompt": "P1",
                "response": "R1",
            },
            2: {
                "conversation_type": "single_turn",
                "category": "next_best_move",
                "prompt": "P2",
                "response": "R2",
            },
        },
        fail_on_index=2,
    )

    try:
        generator.run(samples=3, conversation_type="single_turn", job_name="resume-job")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected the first run to fail")

    progress_path = tmp_path / "resume-job" / "progress.json"
    metadata_path = tmp_path / "resume-job" / "samples.jsonl"
    first_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    with metadata_path.open("r", encoding="utf-8") as f:
        first_rows = [json.loads(line) for line in f if line.strip()]

    assert first_progress["status"] == "failed"
    assert first_progress["completed"] == 1
    assert first_progress["accepted_samples"] == 1
    assert len(first_rows) == 1

    resumed_generator = ConversationGenerator(config)
    resumed_generator.model_client = IndexedModelClient(
        {
            0: {
                "conversation_type": "single_turn",
                "category": "next_best_move",
                "prompt": "P0",
                "response": "R0",
            },
            1: {
                "conversation_type": "single_turn",
                "category": "hint",
                "prompt": "P1",
                "response": "R1",
            },
            2: {
                "conversation_type": "single_turn",
                "category": "next_best_move",
                "prompt": "P2",
                "response": "R2",
            },
        }
    )
    resumed_generator.run(samples=3, conversation_type="single_turn", job_name="resume-job")

    final_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    with metadata_path.open("r", encoding="utf-8") as f:
        final_rows = [json.loads(line) for line in f if line.strip()]

    assert final_progress["status"] == "completed"
    assert final_progress["completed"] == 3
    assert final_progress["accepted_samples"] == 3
    assert len(final_rows) == 3
