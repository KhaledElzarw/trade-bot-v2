# Security Policy

This repository may be used with exchange credentials, dashboard access
controls, local databases, logs, AI artifacts, screenshots, ZIP archives, and
runtime state files. Treat all of those materials as sensitive. Real secrets
must never be committed.

Security work in this project prioritizes preventing credential exposure,
keeping local runtime state out of source control, and making public repository
usage safe by default. Do not publish operational secrets in commits, issues,
pull requests, screenshots, ZIP files, logs, or support requests.

## Security Model - TL;DR

- Operate as a local or private deployment with one trusted operator first.
- Start with paper/testnet and localhost-only dashboard access.
- Keep Binance keys out of Git and never grant withdrawal permissions to bot
  keys.
- Set `TRADEBOT_DASHBOARD_TOKEN` before exposing the dashboard beyond
  localhost.
- Treat SQLite files, WAL/SHM sidecars, logs, JSON/JSONL state, AI decisions,
  screenshots, and ZIP archives as private runtime data.
- AI/model output is advisory and may be wrong, stale, manipulated by context,
  or unsuitable for live trading.
- If a secret or runtime archive leaks, rotate credentials first and clean
  history/artifacts second.

## Supported Security Boundary

Tradebot's documented security boundary is intentionally narrow:

- Local or single trusted-operator use.
- Paper/testnet operation before any live-risk workflow.
- Localhost dashboard operation by default, with token controls when access is
  not localhost-only.
- Runtime files stored locally, privately, or in deployment-managed private
  volumes.
- Secrets stored in local `.env` files or deployment secret storage, never in
  source control.

Within this boundary, the repository aims to make accidental exposure harder
and operator responsibilities clearer.

## Not a Supported Boundary

The following are not supported security boundaries:

- Public unauthenticated dashboard exposure.
- Shared adversarial multi-user operation.
- A live trading safety guarantee.
- Public or semi-public runtime archives, screenshots, logs, SQLite files, or
  JSON/JSONL state.
- AI/model output as an authority for safe or profitable trading.
- Binance keys with withdrawal permissions.

## Sensitive Assets

Treat these assets as sensitive:

- Binance API keys and secrets.
- Dashboard tokens or passwords.
- `.env` and `.env.*` files.
- SQLite databases, including `*.sqlite3`, `*.sqlite3-wal`, and
  `*.sqlite3-shm`.
- Logs, `*.nohup.out`, and process output.
- JSON and JSONL runtime state.
- AI decisions, memory, signals, prompt context, and generated review output.
- Screenshots that show dashboard state, logs, credentials, account data,
  tokens, balances, orders, or runtime paths.
- ZIP archives, backups, exports, and accounting archives.

## Threat Surfaces

### Exchange Credentials

Binance API keys can authorize account reads and trading actions. Store them
only in local environment files or deployment secret storage. Prefer restricted
keys with only the permissions required by the running mode. Do not grant
withdrawal permissions to bot keys.

### Dashboard Mutation Endpoints

The dashboard provides local visibility and selected controls. Mutation
endpoints are sensitive operational controls. If the dashboard is reachable
from an untrusted network, require an access token or equivalent
authentication, use HTTPS at the network boundary, and avoid exposing mutation
endpoints directly to the public internet.

### Local Runtime Files

SQLite DBs, WAL/SHM sidecars, logs, PID files, and JSON/JSONL mirrors may
contain operational history, account metadata, strategy context, model output,
or local paths. Keep them private and outside Git.

### Logs and Screenshots

Logs and screenshots can expose credentials, tokens, order history, balances,
runtime paths, model prompts, or enough context to reconstruct private
operation. Review and redact before sharing.

### AI/Model Output and Prompt Context

AI decisions, memory, signals, and prompt payloads may include account state,
strategy assumptions, recent market context, or operator intent. Treat them as
runtime data. Prompt injection is primarily a security issue when it crosses a
dashboard, authentication, filesystem, credential, or data-exposure boundary.

### Local Network Exposure

Binding the dashboard to `0.0.0.0` exposes it to the network. Use
localhost-only binding unless remote access is intentional, and set
`TRADEBOT_DASHBOARD_TOKEN` before any non-localhost exposure.

## Secure Configuration Checklist

- Run paper/testnet first.
- Keep real secrets out of commits, docs, tests, issue comments, commit
  messages, CI logs, screenshots, ZIP files, and chat transcripts.
- Store secrets in local `.env` files or deployment secret storage.
- Keep `.env.example` placeholder-only, with secret values blank.
- Use restricted Binance keys with only the permissions required for the
  operating mode.
- Disable withdrawal permissions on bot keys.
- Bind the dashboard to localhost for local operation when possible.
- Set `TRADEBOT_DASHBOARD_TOKEN` before binding the dashboard to a non-localhost
  interface.
- Keep SQLite DBs, WAL/SHM files, logs, PID files, JSON/JSONL mirrors,
  screenshots, and ZIP archives out of Git.
- Back up runtime files only to private local storage or deployment-managed
  private volumes.
- Review AI output and prompt context as sensitive runtime data.
- Rotate any credential that was committed, logged, screenshotted, shared in a
  ZIP, or exposed outside the trusted operating environment.

## Local Security Validation

Run the local validation suite before publishing security-sensitive
documentation or operational changes:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q .
.venv/bin/python -m ruff check .
git diff --check
```

Future secret scanning can be added separately without changing this local
baseline.

## Secret Handling Rules

- Real secrets must never be committed.
- Do not paste secret values into documentation, tests, issue comments, commit
  messages, CI logs, or chat transcripts.
- Keep secrets in local environment files or a secrets manager controlled by
  the deployment environment.
- Use the minimum permissions needed for each token or API key.
- Treat committed secrets as compromised, even if the commit was later removed.
- If real secrets were committed or shared in a ZIP, rotate them.

## .env and .env.example Rules

Local `.env` files are for private machine-specific configuration only. They
must stay untracked and must not be included in archives shared outside the
trusted operating environment.

`.env.example` must contain placeholders only. It may document variable names,
expected formats, and safe example values, but it must not contain real API
keys, tokens, account identifiers, webhook URLs, private URLs, or production
configuration values.

## Reporting Security Issues

Report suspected vulnerabilities or accidental secret exposure privately to the
repository owner or maintainer. Do not open public issues containing secret
values, exploit details, account identifiers, screenshots with tokens, or live
operational state.

Include as much of the following as you can without disclosing credentials or
private runtime data:

- Affected path, route, function, command, or documentation section.
- Version, branch, or commit SHA.
- Reproduction steps using redacted placeholders.
- Expected result and actual result.
- Security impact and who could exploit it.
- Whether credentials, dashboard tokens, runtime files, logs, screenshots, or
  ZIP archives were exposed.
- Whether any live account, order, balance, or runtime state was affected.
- Suggested remediation, if known.

## What Usually Is Not a Security Bug

These reports may still be operationally useful, but they are usually not
security bugs by themselves:

- Trading loss from market behavior, strategy behavior, latency, partial fills,
  slippage, or operator decisions.
- Expected behavior from intentionally enabled live orders.
- Public unauthenticated dashboard exposure is a serious deployment risk. It is
  not necessarily a code vulnerability unless a documented boundary is
  bypassed.
- Prompt injection or model manipulation that does not cross a dashboard,
  authentication, filesystem, credential, runtime-data, or external-exposure
  boundary.
- Scanner-only reports without a demonstrated impact, reachable path, or
  affected configuration.
- Reports that require committed secrets, public runtime archives, or
  intentionally unsafe local configuration to become exploitable.

## Incident Response: Exposed Binance Key

Do not assume deleting a file or reverting a commit makes the key safe. Git
history, forks, caches, ZIP archives, CI logs, local clones, and screenshots may
still contain the value.

1. Revoke the exposed Binance key immediately.
2. Create a replacement key with the minimum required permissions.
3. Confirm withdrawal permissions are disabled.
4. Update local `.env` files and deployment secret storage.
5. Restart affected services.
6. Audit recent account activity, orders, balances, IP access, and API usage.
7. Search open pull requests, issues, logs, screenshots, and shared archives for
   additional exposure.
8. Document the incident and rotation date without recording secret values.

## Incident Response: Exposed Dashboard Token

1. Stop or firewall the dashboard if it may be reachable from an untrusted
   network.
2. Rotate `TRADEBOT_DASHBOARD_TOKEN`.
3. Update local `.env` files and deployment secret storage.
4. Restart the dashboard or orchestrated services.
5. Review dashboard activity, logs, and mutation endpoints for unexpected use.
6. Review screenshots, ZIPs, logs, and support messages for additional token
   exposure.
7. Document the rotation date without recording the token value.

## Incident Response: Leaked Runtime Archive, Log, or ZIP

1. Assume exposed runtime files may contain account metadata, trade history,
   dashboard state, AI prompt context, model output, local paths, and secrets.
2. Remove access to the archive or shared file where possible.
3. Rotate any credentials or dashboard tokens that may have appeared in the
   archive.
4. Audit exchange and dashboard activity for unexpected access or actions.
5. Replace the archive with a redacted version only if sharing is still needed.
6. Review related pull requests, issues, support messages, and CI logs for
   copied content.
7. Document what was exposed and what was rotated without recording secret
   values.

## Rotation Checklist

- Binance API keys rotated and old keys revoked.
- Binance key permissions reviewed and withdrawal access disabled.
- Dashboard token or password rotated if exposed.
- Deployment secrets updated.
- Local `.env` files updated.
- CI or hosting environment variables updated.
- Logs, JSONL files, AI artifacts, screenshots, and shared ZIP files reviewed
  for exposure.
- Team members notified not to reuse old credentials.

## Local-Only Runtime Files That Must Never Be Committed

SQLite DBs, logs, JSONL trade logs, AI artifacts, and runtime state files are
local runtime artifacts. They may contain operational history, account metadata,
model output, or other sensitive context and must stay out of source control.

Current runtime files:

- `.env`
- `.env.*`
- `*.sqlite3`
- `*.sqlite3-wal`
- `*.sqlite3-shm`
- `*.db`
- `*.log`
- `*.pid`
- `*.nohup.out`
- `ai_signal.json`
- `ai_decisions.jsonl`
- `ai_memory.json`
- `cumulative.json`
- `cumulative_trend.json`
- `dashboard_history.json`
- `engine_status.json`
- `engine_status_trend.json`
- `runtime_state.json`
- `state.json`
- `state_trend.json`
- `trades.jsonl`
- `trades_trend.jsonl`

Legacy local artifacts retained in `.gitignore` after workflow removal should
also stay private if old copies exist:

- `advisor.log`
- `grid_honest_replay*.json`
- `engine_trend.log`
- `*.bak`
- `*.bak_*`
- `accounting_archive_*`

For the full source-vs-runtime policy, see
[docs/runtime-artifacts.md](docs/runtime-artifacts.md).
