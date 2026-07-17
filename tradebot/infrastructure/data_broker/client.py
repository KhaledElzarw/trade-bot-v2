"""DataBroker: the only component that reaches the external allowlist.

Transport is injected (a real httpx client in production, a fake in tests) so
the normal test suite never touches the network. Every request and every
redirect hop is revalidated through ``policy.validate_request``. Responses are
size-capped, MIME-checked, and HTML/XML is sanitized — external text is treated
as data, never as instructions.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .policy import (
    SECONDARY_NEWS_HOSTS,
    HostPolicy,
    PolicyViolation,
    validate_request,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
ALLOWED_MIME_PREFIXES = ("application/json", "text/html", "text/xml",
                         "application/xml", "application/rss+xml", "text/plain",
                         "application/atom+xml")


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    location: str | None = None  # redirect target if 3xx


class Transport(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str]) -> RawResponse: ...


@dataclass(frozen=True, slots=True)
class BrokerResult:
    source_id: str
    final_url: str
    status: int
    raw_hash: str
    normalized_hash: str
    payload: str
    is_secondary: bool
    mime: str


_SCRIPT_RE = re.compile(r"<(script|style|iframe|object|embed|form)[^>]*>.*?</\1>",
                        re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def sanitize_markup(text: str) -> str:
    """Strip scripts/styles/forms/comments and all tags; collapse whitespace.

    Result is inert plain text — safe to hand to a prompt as *data*.
    """

    text = _COMMENT_RE.sub(" ", text)
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(slots=True)
class DataBroker:
    transport: Transport
    resolver: Callable[[str], list[str]] = field(default=None)  # type: ignore[assignment]
    user_agent: str = "tradebot-research/1.0 (contact: operator)"

    def fetch(self, source_id: str, url: str, method: str = "GET") -> BrokerResult:
        """Validate, fetch (following revalidated redirects), sanitize, hash."""

        kwargs = {} if self.resolver is None else {"resolver": self.resolver}
        policy: HostPolicy = validate_request(url, method, **kwargs)  # type: ignore[arg-type]

        current_url = url
        hops = 0
        while True:
            resp = self.transport.request(method, current_url,
                                          {"User-Agent": self.user_agent})
            if resp.status in (301, 302, 303, 307, 308):
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise PolicyViolation("too many redirects")
                if not resp.location:
                    raise PolicyViolation("redirect without Location")
                # Revalidate the redirect target against the allowlist.
                policy = validate_request(resp.location, method, **kwargs)  # type: ignore[arg-type]
                current_url = resp.location
                continue
            break

        if len(resp.body) > MAX_RESPONSE_BYTES:
            raise PolicyViolation("response exceeds size limit")

        mime = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime and not any(mime.startswith(p) for p in ALLOWED_MIME_PREFIXES):
            raise PolicyViolation(f"disallowed content-type: {mime}")

        raw_hash = hashlib.sha256(resp.body).hexdigest()
        text = resp.body.decode("utf-8", errors="replace")
        is_secondary = policy.host in SECONDARY_NEWS_HOSTS
        if mime.startswith("application/json") or mime == "text/plain":
            payload = text
        else:
            payload = sanitize_markup(text)
        normalized_hash = hashlib.sha256(payload.encode()).hexdigest()

        return BrokerResult(
            source_id=source_id,
            final_url=current_url,
            status=resp.status,
            raw_hash=raw_hash,
            normalized_hash=normalized_hash,
            payload=payload,
            is_secondary=is_secondary,
            mime=mime,
        )
