import json

from src.generator.generator import ConversationGenerator


class StubModelClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def generate(self, prompt: str) -> str:
        return json.dumps(self.payload)


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
