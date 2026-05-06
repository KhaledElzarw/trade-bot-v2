# Security Policy

This repository may be used with exchange credentials, dashboard access
controls, local databases, logs, and runtime state files. Treat all of those
materials as sensitive. Real secrets must never be committed.

## Security Policy

Security work in this project prioritizes preventing credential exposure,
keeping local runtime state out of source control, and making public repository
usage safe by default. Do not publish operational secrets in commits, issues,
pull requests, screenshots, ZIP files, logs, or support requests.

## Secret Handling Rules

- Real secrets must never be committed.
- Do not paste secret values into documentation, tests, issue comments, commit
  messages, CI logs, or chat transcripts.
- Keep secrets in local environment files or a secrets manager controlled by the
  deployment environment.
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

## Binance API Key Safety

Binance API keys can authorize account reads and trading actions. Store them
only in local environment files or deployment secret storage. Prefer restricted
keys with only the permissions required by the running mode. Do not grant
withdrawal permissions to bot keys. Rotate keys immediately if they were
committed, copied into a public location, exposed in logs, or shared in a ZIP.

## Dashboard Exposure Warning

The dashboard should not be exposed publicly without authentication/token
controls. If the dashboard is reachable from an untrusted network, require an
access token or equivalent authentication, use HTTPS at the network boundary,
and avoid exposing mutation endpoints directly to the public internet.

## What To Do If Secrets Were Committed

Do not assume deleting the file or reverting the commit makes the secret safe.
Git history, forks, caches, ZIP archives, CI logs, and local clones may still
contain the value.

1. Rotate every exposed key or token immediately.
2. Revoke the old credential in the upstream provider.
3. Audit recent provider activity for unexpected access or trades.
4. Replace local environment files with the rotated values.
5. Review the repository, open pull requests, issues, logs, and shared archives
   for additional exposure.
6. Document the incident and the rotation date without recording secret values.

## Rotation Checklist

- Binance API keys rotated and old keys revoked.
- Binance key permissions reviewed and withdrawal access disabled.
- Dashboard token or password rotated if exposed.
- Deployment secrets updated.
- Local `.env` files updated.
- CI or hosting environment variables updated.
- Logs, JSONL files, screenshots, and shared ZIP files reviewed for exposure.
- Team members notified not to reuse old credentials.

## Reporting Security Issues

Report suspected vulnerabilities or accidental secret exposure privately to the
repository owner or maintainer. Do not open public issues containing secret
values, exploit details, account identifiers, screenshots with tokens, or live
operational state. Include only enough context to reproduce or assess the issue
without disclosing credentials.

## Local-Only Runtime Files That Must Never Be Committed

SQLite DBs, logs, JSONL trade logs, and runtime state files are local runtime
artifacts. They may contain operational history, account metadata, model output,
or other sensitive context and must stay out of source control.

Examples include current runtime files and legacy local artifacts retained in
`.gitignore` after workflow removal:

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
