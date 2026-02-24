"""Optional LLM client adapters for Step8 normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class LLMClientError(RuntimeError):
    """Raised when LLM gateway communication fails."""


class OpenClawLLM:
    """
    Minimal OpenAI-compatible gateway client.

    Default target is OpenClaw gateway (`http://localhost:4141`).
    """

    def __init__(
        self,
        gateway_url: str,
        model: str,
        timeout_seconds: float = 20.0,
    ):
        self.gateway_url = str(gateway_url or "").rstrip("/")
        self.model = str(model or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.session = requests.Session()

        if not self.gateway_url:
            raise LLMClientError("gateway_url이 비어 있습니다.")
        if not self.model:
            raise LLMClientError("model이 비어 있습니다.")

    def chat(self, prompt: str) -> str:
        url = f"{self.gateway_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": str(prompt or "")}],
        }
        headers = {"Content-Type": "application/json", "Authorization": "Bearer openclaw"}

        try:
            response = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LLMClientError(f"LLM 게이트웨이 호출 실패: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMClientError("LLM 게이트웨이 JSON 파싱 실패") from exc

        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("LLM 응답 포맷 오류 (choices/message/content 없음)") from exc


@dataclass
class BudgetedLLMUsage:
    enabled: bool
    max_calls: int
    calls_used: int
    calls_blocked: int
    calls_failed: int


class BudgetedLLMClient:
    """
    Wrapper that enforces LLM call budget.

    - disabled mode or exhausted budget returns "{}" without remote call
    - gateway errors return "{}" and increment failure count
    """

    def __init__(
        self,
        base_client: Any,
        enabled: bool,
        max_calls: int,
    ):
        self.base_client = base_client
        self.enabled = bool(enabled)
        self.max_calls = max(0, int(max_calls))
        self.calls_used = 0
        self.calls_blocked = 0
        self.calls_failed = 0

    def chat(self, prompt: str) -> str:
        if not self.enabled:
            self.calls_blocked += 1
            return "{}"
        if self.calls_used >= self.max_calls:
            self.calls_blocked += 1
            return "{}"

        try:
            result = self.base_client.chat(prompt)
        except Exception:  # pylint: disable=broad-except
            self.calls_failed += 1
            return "{}"

        self.calls_used += 1
        return str(result)

    def usage(self) -> BudgetedLLMUsage:
        return BudgetedLLMUsage(
            enabled=self.enabled,
            max_calls=self.max_calls,
            calls_used=self.calls_used,
            calls_blocked=self.calls_blocked,
            calls_failed=self.calls_failed,
        )
