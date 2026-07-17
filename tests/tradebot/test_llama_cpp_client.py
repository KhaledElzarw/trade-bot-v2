"""llama.cpp client contract tests (Phase 7). No real model calls."""

from pydantic import BaseModel

from tradebot.infrastructure.llm.llama_cpp_client import LlamaCppClient, LlmConfig


class LessonOut(BaseModel):
    observation: str
    confidence: float


def chat_response(content: str):
    return 200, {"choices": [{"message": {"content": content}}]}


class FakeTransport:
    def __init__(self, *, health=200, models=None, posts=None):
        self._health = health
        self._models = models
        self._posts = list(posts or [])
        self.post_calls = []

    def get(self, url):
        if url.endswith("/health"):
            return self._health, {}
        if url.endswith("/models"):
            return 200, self._models
        raise AssertionError(url)

    def post(self, url, payload):
        self.post_calls.append(payload)
        return self._posts.pop(0)


def test_health_ok_and_down():
    assert LlamaCppClient(FakeTransport(health=200)).health() is True
    assert LlamaCppClient(FakeTransport(health=503)).health() is False


def test_discover_model_uses_served_id_not_gguf_name():
    t = FakeTransport(models={"data": [{"id": "qwen3vl-30b"}]})
    client = LlamaCppClient(t)
    assert client.discover_model() == "qwen3vl-30b"
    # It is NOT assumed to equal the configured GGUF artifact filename.
    assert client.discover_model() != client.config.expected_model_artifact


def test_structured_generation_valid():
    t = FakeTransport(posts=[chat_response('{"observation": "range-bound", "confidence": 0.7}')])
    client = LlamaCppClient(t)
    model, run = client.generate_structured(LessonOut, [{"role": "user", "content": "x"}])
    assert model.observation == "range-bound"
    assert run.status == "ok"
    assert run.attempts == 1


def test_schema_repair_retry_succeeds_second_attempt():
    t = FakeTransport(posts=[
        chat_response("not json at all"),
        chat_response('{"observation": "ok", "confidence": 0.5}'),
    ])
    client = LlamaCppClient(t, LlmConfig(max_repair_attempts=1))
    model, run = client.generate_structured(LessonOut, [{"role": "user", "content": "x"}])
    assert model is not None
    assert run.attempts == 2
    # A repair message was appended to the conversation.
    assert len(t.post_calls[1]["messages"]) > len(t.post_calls[0]["messages"])


def test_schema_failure_after_retries_degrades_not_raises():
    t = FakeTransport(posts=[chat_response("bad"), chat_response("still bad")])
    client = LlamaCppClient(t, LlmConfig(max_repair_attempts=1))
    model, run = client.generate_structured(LessonOut, [{"role": "user", "content": "x"}])
    assert model is None
    assert run.status == "schema_error"


def test_transport_error_degrades():
    class Boom:
        def get(self, url):
            raise ConnectionError("down")

        def post(self, url, payload):
            raise ConnectionError("down")

    model, run = LlamaCppClient(Boom()).generate_structured(
        LessonOut, [{"role": "user", "content": "x"}])
    assert model is None
    assert run.status == "transport_error"
    assert run.error_category == "ConnectionError"


def test_http_error_status_degrades():
    class ErrTransport(FakeTransport):
        def post(self, url, payload):
            return 500, {}

    model, run = LlamaCppClient(ErrTransport()).generate_structured(
        LessonOut, [{"role": "user", "content": "x"}])
    assert model is None
    assert run.status == "degraded"
    assert run.error_category == "http_500"


def test_deterministic_temperature_default():
    t = FakeTransport(posts=[chat_response('{"observation": "x", "confidence": 0.1}')])
    client = LlamaCppClient(t)
    client.generate_structured(LessonOut, [{"role": "user", "content": "x"}])
    assert t.post_calls[0]["temperature"] == 0.0
