from __future__ import annotations

import json
from typing import Any

import requests


class ModelClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.api_key = config.get("api_key")
        self.base_url = config.get("base_url")
        self.endpoint = config.get("endpoint")
        self.provider = config.get("provider", "openai")

    def generate(self, prompt: str) -> str:
        if self.provider == "openai" and self.api_key:
            return self._openai_generate(prompt)

        if not self.endpoint:
            return self._mock_generate(prompt)

        response = requests.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={
                "model": self.config.get("model_name", "gpt-4o-mini"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.config.get("temperature", 0.8),
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("choices", [{}])[0].get("message", {}).get("content", "")

    def _openai_generate(self, prompt: str) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The 'openai' package is required for OpenAI API generation.") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = OpenAI(**client_kwargs)
        request_kwargs: dict[str, Any] = {
            "model": self.config.get("model_name", "gpt-4o-mini"),
            "input": prompt,
        }

        temperature = self.config.get("temperature")
        if temperature is not None:
            request_kwargs["temperature"] = temperature

        max_tokens = self.config.get("max_tokens")
        if max_tokens is not None:
            request_kwargs["max_output_tokens"] = max_tokens

        response = client.responses.create(**request_kwargs)
        return getattr(response, "output_text", "") or ""

    def _mock_generate(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        category = "general_chat"
        if "category:" in prompt_lower:
            category = prompt.split("Category:", 1)[1].splitlines()[0].strip()

        if "multi_turn" in prompt_lower or "multi-turn" in prompt_lower or "turns" in prompt_lower:
            payload = {
                "conversation_type": "multi_turn",
                "category": category,
                "messages": [
                    {
                        "user": "Can you give me a hint for this Sudoku puzzle without solving it?",
                        "response": "Sure - start by checking the row, column, and box that contain the fewest possibilities. Look for a number that can only go in one spot.",
                    },
                ],
                "board": "",
            }
        else:
            payload = {
                "conversation_type": "single_turn",
                "category": category,
                "prompt": "What is the next best move in this Sudoku puzzle?",
                "response": "A strong next move is to scan the row, column, and box for the missing value that has only one possible placement. That usually reveals the safest step.",
                "board": "",
            }
        return json.dumps(payload, ensure_ascii=False)
