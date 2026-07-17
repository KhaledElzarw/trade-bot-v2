# DataBroker & Local LLM

## DataBroker (deny-by-default)

The local model never supplies a raw URL. It names a `source_id` + dataset; the
broker builds the request from the allowlist in
[policy.py](../tradebot/infrastructure/data_broker/policy.py). The model cannot
add hosts, paths, methods, or ports — the allowlist changes only through a
source release + tests.

### Allowlisted hosts (all GET unless noted)

| Host | Purpose | Paths |
|------|---------|-------|
| `data-api.binance.vision` | BTCUSDT public spot data | `/api/v3/{exchangeInfo,klines,ticker,trades,aggTrades,depth}` |
| `api.stlouisfed.org` | FRED/ALFRED macro | `/fred/**` |
| `api.bls.gov` | CPI/PPI/employment (GET+POST) | `/publicAPI/**` |
| `apps.bea.gov` | GDP/PCE/national accounts | `/api/**` |
| `federalreserve.gov` | FOMC statements/calendars/RSS | `/**` |
| `cftc.gov` | Commitments of Traders | `/**` |
| `community-api.coinmetrics.io` | BTC on-chain/network | `/v4/timeseries/**`, `/v4/reference-data/**`, `/v4/catalog/**` |
| `mempool.space` | mempool/fees/blocks | `/api/**` |
| `api.coingecko.com` | independent BTC price | `/api/**` |
| `data.sec.gov` | ETF/issuer filings | `/submissions/**`, `/api/xbrl/**` |
| `www.coindesk.com`, `cointelegraph.com`, `decrypt.co`, `www.theblock.co` | secondary news (RSS only) | feed paths |
| `172.29.72.68:18081` (**http**) | local llama.cpp — the **only** private destination | `/health`, `/v1/models`, `/v1/chat/completions` |

### SSRF controls (enforced on the initial request and every redirect)

- Exact scheme + host + port + method + path-prefix match; HTTPS required
  except the single local-llm HTTP exception.
- Userinfo in URLs rejected; non-standard ports rejected.
- DNS resolved and blocked if loopback / link-local / RFC1918 / multicast /
  reserved / unspecified / `169.254.169.254` / IPv6 private — defeating DNS
  rebinding. The local llm host is the sole private-IP exception.
- Redirects revalidated against the allowlist; off-allowlist redirects blocked;
  redirect count capped.
- Response size cap (8 MiB), MIME allowlist, HTML/XML sanitized (scripts,
  styles, forms, comments, and all tags stripped). External text is **data,
  never instructions**. Secondary news is flagged `is_secondary`.
- Raw and normalized response hashes preserved for provenance.

## Local LLM (llama.cpp)

Provider `llama_cpp`. Config from environment / typed config, never dashboard
state:

```
TRADEBOT_LLM_PROVIDER=llama_cpp
TRADEBOT_LLM_BASE_URL=http://172.29.72.68:18081/v1
TRADEBOT_LLM_HEALTH_URL=http://172.29.72.68:18081/health
TRADEBOT_LLM_EXPECTED_MODEL_ARTIFACT=Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf
```

The served model id is discovered via `/v1/models` — it is **not** assumed to
equal the GGUF filename. Every response is validated against a Pydantic schema;
schema failures retry with a bounded repair prompt; deterministic temperature
default (0.0). Model failure **degrades** (returns `None` + a run record) and
never raises into the trading loop or stops existing active strategies. Every
attempt yields an `LlmRun` (model id, prompt hash, schemas, attempts, status)
for the caller to persist.
