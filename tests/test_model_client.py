from src.generator.model_client import ModelClient


class DummyResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_custom_chat_provider_sends_expected_payload(monkeypatch):
    captured: dict = {}

    def fake_post(url: str, headers: dict, json: dict, timeout: int):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"conversation_type":"single_turn","prompt":"P","response":"R"}'
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("src.generator.model_client.requests.post", fake_post)

    client = ModelClient(
        {
            "provider": "custom_chat",
            "endpoint": "http://172.17.99.11:30000/v1/chat/completions",
            "api_key": "custom-key",
            "model_name": "gpt-oss-120b",
            "temperature": 0.1,
            "max_tokens": 256,
            "reasoning": "Low",
            "enable_thinking": False,
        }
    )

    content = client.generate("Write a python code to add two numbers?")

    assert content == '{"conversation_type":"single_turn","prompt":"P","response":"R"}'
    assert captured["url"] == "http://172.17.99.11:30000/v1/chat/completions"
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer custom-key",
    }
    assert captured["json"]["model"] == "gpt-oss-120b"
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "Reasoning: Low"},
        {"role": "user", "content": "Write a python code to add two numbers?"},
    ]
    assert captured["json"]["temperature"] == 0.1
    assert captured["json"]["max_tokens"] == 256
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["timeout"] == 60
