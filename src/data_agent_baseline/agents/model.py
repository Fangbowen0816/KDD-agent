from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str


class ModelAdapter(Protocol):
    def complete(self, messages: list[ModelMessage]) -> str:
        raise NotImplementedError


class OpenAIModelAdapter:
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float,
        request_timeout_seconds: float = 120.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max(max_retries, 0)
        self.retry_backoff_seconds = max(retry_backoff_seconds, 0.0)

    def complete(self, messages: list[ModelMessage]) -> str:
        if not self.api_key:
            raise RuntimeError("Missing model API key in config.agent.api_key.")

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.request_timeout_seconds,
            max_retries=0,
        )

        last_error: Exception | None = None
        for attempt_index in range(self.max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                    temperature=self.temperature,
                )
                break
            except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
                last_error = exc
                if attempt_index >= self.max_retries:
                    raise RuntimeError(
                        f"Model request failed after {attempt_index + 1} attempts: {exc}"
                    ) from exc
                delay = self.retry_backoff_seconds * (2**attempt_index)
                if delay > 0:
                    time.sleep(delay)
        else:
            raise RuntimeError(f"Model request failed: {last_error}")

        choices = response.choices or []
        if not choices:
            raise RuntimeError("Model response missing choices.")
        content = choices[0].message.content
        if not isinstance(content, str):
            raise RuntimeError("Model response missing text content.")
        return content


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[ModelMessage]) -> str:
        del messages
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
