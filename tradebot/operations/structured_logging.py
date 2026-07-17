"""Structured JSON logging with the mandated correlation fields.

Secrets and full untrusted external content are never logged: values for keys
matching the redaction list are replaced, and long payloads are truncated.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

LOG_SCHEMA_VERSION = "log-v1"

CONTEXT_FIELDS = (
    "correlation_id", "service", "instance_id", "wallet_id",
    "strategy_version_id", "market_snapshot_id", "order_id", "fill_id",
    "evaluation_window", "job_id", "promotion_batch_id", "model_run_id",
    "error_category",
)

REDACT_KEY_PATTERN = re.compile(
    r"(token|secret|password|api[_-]?key|authorization|cookie|credential)",
    re.IGNORECASE,
)
REDACTED = "***redacted***"
MAX_VALUE_CHARS = 512


def redact(value: Any, key: str = "") -> Any:
    """Redact secret-ish keys; truncate long values (no full external content)."""

    if REDACT_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + f"…(+{len(value) - MAX_VALUE_CHARS})"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "schema": LOG_SCHEMA_VERSION,
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact(value, field)
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload["context"] = redact(extra)
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_category"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str, sort_keys=True)


def configure(level: int = logging.INFO) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("tradebot")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False
    return handler


# -- metrics ------------------------------------------------------------------

METRIC_NAMES = (
    "engine_tick_duration_seconds",
    "strategy_worker_duration_seconds",
    "strategy_worker_timeouts_total",
    "market_data_freshness_seconds",
    "data_broker_source_health",
    "llm_health",
    "llm_latency_seconds",
    "database_transaction_latency_seconds",
    "wallet_invariant_failures_total",
    "rejected_intents_total",
    "promotion_success_total",
    "promotion_failure_total",
    "quarantines_total",
    "report_generation_failures_total",
    "active_wallet_count",
    "shadow_wallet_count",
)


class Metrics:
    """Minimal in-process metric sink (a real exporter plugs in behind this)."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], float] = {}
        self._gauges: dict[tuple[str, str], float] = {}

    @staticmethod
    def _key(name: str, label: str) -> tuple[str, str]:
        if name not in METRIC_NAMES:
            raise ValueError(f"unknown metric: {name}")
        return (name, label)

    def increment(self, name: str, value: float = 1.0, label: str = "") -> None:
        key = self._key(name, label)
        self._counters[key] = self._counters.get(key, 0.0) + value

    def observe(self, name: str, value: float, label: str = "") -> None:
        self._gauges[self._key(name, label)] = value

    def counter(self, name: str, label: str = "") -> float:
        return self._counters.get((name, label), 0.0)

    def gauge(self, name: str, label: str = "") -> float | None:
        return self._gauges.get((name, label))

    def snapshot(self) -> dict[str, float]:
        out = {f"{n}|{lbl}": v for (n, lbl), v in self._counters.items()}
        out.update({f"{n}|{lbl}": v for (n, lbl), v in self._gauges.items()})
        return out
