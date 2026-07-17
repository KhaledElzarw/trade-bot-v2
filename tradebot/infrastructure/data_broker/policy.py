"""Deny-by-default network policy for the DataBroker (closes A09 SSRF class).

The local model never supplies a raw URL — it names a ``source_id`` + dataset,
and the broker constructs the request from this allowlist. Every outbound URL
(initial and every redirect) is revalidated here:

* exact scheme + host + port + method + path-prefix match,
* HTTPS required except the single explicit local llama.cpp HTTP exception,
* userinfo in the URL rejected,
* non-standard ports rejected unless the allowlist entry declares them,
* resolved IPs blocked when loopback / link-local / private (RFC1918) /
  multicast / metadata-service / IPv6 private — defeating DNS rebinding,
* the local llama.cpp host is the ONLY permitted private-network destination.

The model cannot add hosts, paths, methods, or ports — the allowlist changes
only through a normal source release + tests.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

LOCAL_LLM_HOST = "172.29.72.68"
LOCAL_LLM_PORT = 18081


@dataclass(frozen=True, slots=True)
class HostPolicy:
    host: str
    scheme: str  # "https" or (for the local llm only) "http"
    port: int
    methods: frozenset[str]
    path_prefixes: tuple[str, ...]
    allow_private_ip: bool = False  # True ONLY for the local llm exception


def _p(host, scheme, port, methods, prefixes, allow_private=False) -> HostPolicy:
    return HostPolicy(host, scheme, port, frozenset(methods), tuple(prefixes),
                      allow_private)


# The complete external allowlist (product spec <data_broker>).
ALLOWLIST: dict[str, HostPolicy] = {
    hp.host: hp
    for hp in (
        _p("data-api.binance.vision", "https", 443, ["GET"],
           ["/api/v3/exchangeInfo", "/api/v3/klines", "/api/v3/ticker",
            "/api/v3/trades", "/api/v3/aggTrades", "/api/v3/depth"]),
        _p("api.stlouisfed.org", "https", 443, ["GET"], ["/fred/"]),
        _p("api.bls.gov", "https", 443, ["GET", "POST"], ["/publicAPI/"]),
        _p("apps.bea.gov", "https", 443, ["GET"], ["/api/"]),
        _p("federalreserve.gov", "https", 443, ["GET"], ["/"]),
        _p("cftc.gov", "https", 443, ["GET"], ["/"]),
        _p("community-api.coinmetrics.io", "https", 443, ["GET"],
           ["/v4/timeseries/", "/v4/reference-data/", "/v4/catalog/"]),
        _p("mempool.space", "https", 443, ["GET"], ["/api/"]),
        _p("api.coingecko.com", "https", 443, ["GET"], ["/api/"]),
        _p("data.sec.gov", "https", 443, ["GET"], ["/submissions/", "/api/xbrl/"]),
        _p("www.coindesk.com", "https", 443, ["GET"], ["/arc/outboundfeeds/",
                                                       "/feed"]),
        _p("cointelegraph.com", "https", 443, ["GET"], ["/rss"]),
        _p("decrypt.co", "https", 443, ["GET"], ["/feed"]),
        _p("www.theblock.co", "https", 443, ["GET"], ["/rss"]),
        # The ONLY permitted private-network destination.
        _p(LOCAL_LLM_HOST, "http", LOCAL_LLM_PORT, ["GET", "POST"],
           ["/health", "/v1/models", "/v1/chat/completions"], allow_private=True),
    )
}

SECONDARY_NEWS_HOSTS = frozenset(
    {"www.coindesk.com", "cointelegraph.com", "decrypt.co", "www.theblock.co"}
)


class PolicyViolation(Exception):
    """Raised when a URL/method is not permitted. Message is safe to log."""


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        # Cloud metadata service.
        or ip_str == "169.254.169.254"
    )


def default_resolver(host: str) -> list[str]:  # pragma: no cover - real DNS
    import socket

    infos = socket.getaddrinfo(host, None)
    # sockaddr[0] is the address for both AF_INET and AF_INET6.
    return [str(info[4][0]) for info in infos]


def validate_request(
    url: str,
    method: str,
    *,
    resolver: Callable[[str], list[str]] = default_resolver,
) -> HostPolicy:
    """Validate one outbound URL+method. Returns the matched policy or raises.

    Call this for the initial request AND for every redirect target.
    """

    parts = urlsplit(url)
    if parts.username or parts.password or "@" in parts.netloc:
        raise PolicyViolation("userinfo not allowed in URL")

    host = parts.hostname
    if host is None:
        raise PolicyViolation("missing host")
    policy = ALLOWLIST.get(host)
    if policy is None:
        raise PolicyViolation(f"host not allowlisted: {host}")

    if parts.scheme != policy.scheme:
        raise PolicyViolation(f"scheme {parts.scheme} not permitted for {host}")

    port = parts.port if parts.port is not None else (
        443 if parts.scheme == "https" else 80
    )
    if port != policy.port:
        raise PolicyViolation(f"port {port} not permitted for {host}")

    if method.upper() not in policy.methods:
        raise PolicyViolation(f"method {method} not permitted for {host}")

    path = parts.path or "/"
    if not any(path == pre or path.startswith(pre) for pre in policy.path_prefixes):
        raise PolicyViolation(f"path not permitted for {host}: {path}")

    # DNS resolution + private-range block (defeats rebinding). The local llm
    # host is the sole allowed private destination.
    resolved: list[str] = [str(ip) for ip in resolver(host)]
    if not resolved:
        raise PolicyViolation(f"could not resolve {host}")
    for ip_str in resolved:
        if _is_blocked_ip(ip_str) and not policy.allow_private_ip:
            raise PolicyViolation(f"resolved to blocked address: {host}")
    return policy
