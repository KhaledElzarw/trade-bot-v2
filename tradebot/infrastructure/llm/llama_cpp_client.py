"""Typed llama.cpp client (closes A09-adjacent config exposure; product rule 18).

Configuration comes from the environment / typed config, never editable
dashboard state. The client discovers the served model id via ``/v1/models``
(the API id is NOT assumed to equal the GGUF filename), reports degraded status
when unavailable, validates every response against a Pydantic schema, and
retries schema failures with a bounded repair prompt. Every attempt is
persisted by the caller via the returned LlmRun record.

Transport is injected so the normal test suite never calls the real server.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_BASE_URL = "http://172.29.72.68:18081/v1"
DEFAULT_HEALTH_URL = "http://172.29.72.68:18081/health"
EXPECTED_MODEL_ARTIFACT = "Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf"
PROVIDER = "llama_cpp"

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class LlmConfig:
    base_url: str = DEFAULT_BASE_URL
    health_url: str = DEFAULT_HEALTH_URL
    expected_model_artifact: str = EXPECTED_MODEL_ARTIFACT
    temperature: float = 0.0  # deterministic default
    max_repair_attempts: int = 1


@dataclass(slots=True)
class LlmRun:
    model_id: str
    prompt_hash: str
    request_schema: str
    response_schema: str
    attempts: int
    status: str  # ok | schema_error | transport_error | degraded
    error_category: str | None = None


class LlmTransport(Protocol):
    def get(self, url: str) -> tuple[int, dict]: ...
    def post(self, url: str, payload: dict) -> tuple[int, dict]: ...


class LlmUnavailable(Exception):
    pass


@dataclass(slots=True)
class LlamaCppClient:
    transport: LlmTransport
    config: LlmConfig = field(default_factory=LlmConfig)
    _model_id: str | None = None

    # -- readiness -----------------------------------------------------------

    def health(self) -> bool:
        try:
            status, _ = self.transport.get(self.config.health_url)
        except Exception:
            return False
        return status == 200

    def discover_model(self) -> str:
        """Resolve the served model id via /v1/models (not the GGUF filename)."""

        status, body = self.transport.get(f"{self.config.base_url}/models")
        if status != 200 or "data" not in body or not body["data"]:
            raise LlmUnavailable("model discovery failed")
        self._model_id = body["data"][0]["id"]
        return self._model_id

    # -- structured inference ------------------------------------------------

    def generate_structured(
        self,
        schema: type[T],
        messages: list[dict],
        *,
        repair_prompt: Callable[[str], dict] | None = None,
    ) -> tuple[T | None, LlmRun]:
        """Call chat/completions and validate the JSON body against ``schema``.

        On schema failure, retry up to ``max_repair_attempts`` with an appended
        repair message. Returns (validated_model | None, run_record). A model
        failure never raises into the caller's control flow — it degrades.
        """

        model_id = self._model_id or "unknown"
        prompt_hash = hashlib.sha256(
            json.dumps(messages, sort_keys=True, default=str).encode()
        ).hexdigest()
        run = LlmRun(
            model_id=model_id,
            prompt_hash=prompt_hash,
            request_schema="chat.completions.v1",
            response_schema=schema.__name__,
            attempts=0,
            status="ok",
        )

        convo = list(messages)
        last_error = ""
        for attempt in range(self.config.max_repair_attempts + 1):
            run.attempts += 1
            try:
                status, body = self.transport.post(
                    f"{self.config.base_url}/chat/completions",
                    {
                        "model": model_id,
                        "messages": convo,
                        "temperature": self.config.temperature,
                        "response_format": {"type": "json_object"},
                    },
                )
            except Exception as exc:
                run.status = "transport_error"
                run.error_category = type(exc).__name__
                return None, run
            if status != 200:
                run.status = "degraded"
                run.error_category = f"http_{status}"
                return None, run

            content = _extract_content(body)
            try:
                parsed = json.loads(content)
                model = schema.model_validate(parsed)
                run.status = "ok"
                return model, run
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:300]
                run.status = "schema_error"
                run.error_category = type(exc).__name__
                if attempt < self.config.max_repair_attempts:
                    repair = (repair_prompt(last_error) if repair_prompt else {
                        "role": "user",
                        "content": f"Your last reply failed schema validation: "
                                   f"{last_error}. Reply with valid JSON only.",
                    })
                    convo = convo + [repair]
        return None, run


def _extract_content(body: dict) -> str:
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
