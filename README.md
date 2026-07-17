# Evolutionary Multi-Wallet Trading Platform

**A self-evolving BTCUSDT paper-trading research platform.** Twenty-five isolated
wallets compete every week. Losers are eliminated and permanently banned. A local
LLM writes their replacements. Nothing is trusted — not the model, not the market
data, not the generated code.

> **Paper trading only.** No real exchange orders are ever placed and no funds are
> held. Nothing here is a profitability claim or investment advice.

![Portfolio dashboard](docs/screenshots/01-portfolio-dashboard.png)

---

## What it is

| | |
|---|---|
| **12 active wallets** | Twelve structurally distinct strategies, 10,000 USDT each |
| **12 shadow wallets** | Virtual evaluation capital, **never mixed** into active totals |
| **1 Dark Horse** | Permanent wallet, never reset, exempt from elimination |
| **130,000 USDT** | Active baseline (12 × 10k + Dark Horse) |
| **Local Qwen** | Writes and mutates strategies autonomously via llama.cpp |

Every week: rank by profit → eliminate every loser and every zero-trade strategy →
permanently ban their code **and structure** → generate `ceil(n/2)` novel +
`floor(n/2)` mutation replacements → promote atomically.

## Screenshots

| Active portfolio | Shadow (virtual capital) |
|---|---|
| ![Active](docs/screenshots/01-portfolio-dashboard.png) | ![Shadow](docs/screenshots/02-shadow-wallets.png) |

| Dark Horse | All 25 wallets |
|---|---|
| ![Dark Horse](docs/screenshots/03-dark-horse.png) | ![All](docs/screenshots/04-all-25-wallets.png) |

Active, shadow and Dark Horse capital are rendered in **visually distinct panels**
so virtual money can never be misread as real equity.

## The twelve strategies

Not twelve presets of one grid — twelve materially different signal engines, each
with its own `signal()` and conceptual family. This is **proven by test**: all
twelve score pairwise below the 0.65 structural-similarity threshold under the
same novelty policy that governs new candidates.

| # | Strategy | Distinctive characteristic |
|---|----------|---------------------------|
| 1 | Volatility-Adaptive Inventory Grid | Multi-level inventory-managed range |
| 2 | Bollinger Z-Score Reversion | Statistical deviation, with falling-knife veto |
| 3 | Rolling VWAP Deviation | Volume-weighted fair-value reversion |
| 4 | RSI/Stochastic Exhaustion | Oscillator exhaustion + recovery trigger |
| 5 | Donchian Breakout | Price-channel break, volume-confirmed |
| 6 | EMA Trend Pullback | Trend continuation after a controlled dip |
| 7 | MACD Histogram Momentum | Momentum acceleration/deceleration |
| 8 | Bollinger–Keltner Squeeze | Volatility compression → expansion |
| 9 | Chandelier Trend Follower | Long-horizon ATR trailing |
| 10 | Multi-Timeframe Momentum | Return momentum across 4 horizons, inverse-vol sized |
| 11 | OBV / Relative-Volume Breakout | Volume-flow-confirmed accumulation |
| 12 | Regime-Switching Ensemble | Deterministic regime → independent subpolicies |

## Engineering guarantees

Enforced in code and locked by tests — not aspirations.

- **Fixed-point money everywhere.** `float` is *rejected at the boundary* in both
  the domain (`money.py`) and the database (money is stored as exact decimal
  **text**, because SQLite's `Numeric` silently round-trips through binary float).
- **Wallet isolation.** Cross-wallet postings are structurally impossible; each
  wallet only mutates itself.
- **Fees counted exactly once** — acquisition into cost basis, disposal from
  proceeds. A flat round trip yields exactly `-(fees)`.
- **No same-candle churn.** A per-wallet candle watermark makes repeated fills
  against one open candle impossible.
- **Deterministic + bit-reproducible.** The same seed replays to identical
  ledgers, and active/shadow wallets running the same strategy evolve identically.
- **Profit is the only ranking value.** No Sharpe, drawdown, or committee vote can
  alter rank — enforced by schema validators, not convention.

## Security posture

Generated strategy code is treated as **hostile**.

- **Never imported into any core process.** Each tick runs in a `python -I`
  subprocess: sanitized environment, temp cwd, hard timeout, POSIX rlimits.
- **AST deny-by-default**, hardened after an independent verifier proved the
  original was escapable: `getattr` + string dunders reached
  `object.__subclasses__()` (299 classes, incl. `os` gadgets). Now *all* dunder
  access and every reflection builtin are rejected.
- **SSRF-resistant DataBroker.** Deny-by-default allowlist; DNS-resolved private/
  link-local/metadata IPs blocked; every redirect revalidated; the model cannot
  add hosts.
- **Fail-closed API.** Mutations require a token (401/403/400/422); errors are
  redacted to a correlation ID; zero unsafe DOM sinks in the frontend.
- **Identity-verified process control.** A recycled PID receives *no signal at all*.

See [`docs/threat-model.md`](docs/threat-model.md) and
[`docs/audits/phase13-verification.md`](docs/audits/phase13-verification.md) — the
latter records every defect independent verifiers found, including two severe ones.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt

# Dashboard + API with a seeded 25-wallet portfolio (synthetic market, no network)
.venv/Scripts/python -m tradebot.api.devserver --port 5555
# -> http://127.0.0.1:5555/
```

## Going live

### Market data — **no API key needed**

Public BTCUSDT data requires no credentials. This is deliberate: requiring
exchange keys for paper trading was audit finding **A10**. The DataBroker
allowlist already permits `data-api.binance.vision` (GET only,
`/api/v3/klines`, `/api/v3/exchangeInfo`, …). To go live, point the market
adapter at it and replace the devserver's synthetic feed.

**If you later add private endpoints** (not required, and not recommended for a
paper platform), credentials go in `.env` — never in the dashboard, never in git:

```bash
# .env  (gitignored; CI Gate 1 fails the build if it is ever tracked)
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

### Local LLM

```bash
TRADEBOT_LLM_PROVIDER=llama_cpp
TRADEBOT_LLM_BASE_URL=http://172.29.72.68:18081/v1
TRADEBOT_LLM_HEALTH_URL=http://172.29.72.68:18081/health
TRADEBOT_LLM_EXPECTED_MODEL_ARTIFACT=Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf
```

The served model ID is **discovered** via `/v1/models` — never assumed to equal
the GGUF filename. (It currently resolves to `Qwen3-VL-30B-A3B-Instruct`.) If the
model is down the platform reports **degraded** and keeps trading; it never
fabricates analysis.

## Quality

| Gate | Result |
|------|--------|
| Tests | **430** new-package / **833** full suite |
| Coverage (`tradebot/*`) | **97%** (ratchet; see [docs/testing.md](docs/testing.md)) |
| Ruff / Mypy | clean (63 files) |
| Bandit / pip-audit | **0 issues** / no known vulnerabilities |
| Tick performance | **0.62 ms** for 24 wallets (10 ms budget) |

CI runs 8 gates: hygiene, correctness, security, database, frontend, deterministic
replay, performance, release candidate.

## Documentation

[Architecture](docs/architecture.md) · [Accounting](docs/accounting-model.md) ·
[Execution](docs/execution-model.md) · [Plugin SDK](docs/strategy-plugin-sdk.md) ·
[Evolution policy](docs/evolution-policy.md) · [Dark Horse](docs/dark-horse.md) ·
[DataBroker](docs/data-broker.md) · [Threat model](docs/threat-model.md) ·
[Testing](docs/testing.md) · [Release checklist](docs/release-checklist.md)

## Honest status

The new `tradebot` package is a release candidate, not a finished replacement:

- Legacy event **import is not implemented** — the platform starts fresh.
- Coverage is **97%, not 100%**.
- Frontend has static safety analysis; no jsdom/Playwright suite yet.
- The legacy flat modules still exist and still carry their original findings.
- The devserver's market is **synthetic**, not live Binance.

Full detail in [`docs/release-checklist.md`](docs/release-checklist.md).
