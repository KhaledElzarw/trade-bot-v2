# Refactor Roadmap

This roadmap records the next safe modernization phase after repository
hardening. It is documentation only. It does not authorize application code
changes, test changes, runtime path changes, file moves, trading behavior
changes, or order execution changes.

## Current Hardening Summary

The repository now has a foundation for cautious modernization:

- Repository setup, safety, operations, architecture, runtime-artifact, and
  security documentation are in place.
- Generated dashboard intelligence and honest grid replay outputs are classified
  as runtime artifacts.
- Legacy and research candidates are inventoried without moving or deleting
  them.
- Ruff `E` and `F` checks run in CI, with intentional suppressions documented
  for late-bound dashboard globals and late dashboard route imports.
- Low-risk and audited unused imports were removed or marked intentional.
- Unused Binance client constructions were retained and marked intentional
  where construction may have side effects or validates environment setup.
- Coverage reporting runs in CI with no enforced minimum yet.
- Dashboard route and dashboard contract tests now protect the most visible
  monitoring/control payloads.
- Runtime artifact ignore policy has been tightened without moving runtime
  paths.

This foundation reduces accidental churn, but it is not enough coverage for
engine, order, persistence, wrapper, or migration refactors.

## Current CI Quality Gates

CI currently runs on push and pull request with Python 3.11:

- Install development dependencies from `requirements-dev.txt`.
- Run tests through coverage: `python -m coverage run -m pytest -q`.
- Report coverage: `python -m coverage report -m`.
- Run Ruff: `python -m ruff check .`.
- Compile Python files: `python -m compileall -q .`.

Ruff is configured for Python 3.10 compatibility with `E` and `F` selected.
`E501` is intentionally ignored until long lines are reduced through small,
reviewable branches. The current deferred `E501` count is 197 findings when
checked explicitly with `python -m ruff check . --select E501`.

## Current Coverage Baseline

Current local audit results:

- Tests: 69 passed.
- Coverage: 34% total.
- Ruff: all configured checks passed.
- Compileall: passed.
- Deferred `E501` findings: 197 when checked explicitly with
  `python -m ruff check . --select E501`.

Coverage is informational only. It is low because much of the repository is
still made of root-level runtime modules, service entry points, trading loops,
wrappers, migration tools, legacy engines, and integration-oriented flows. Many
of those areas perform environment loading, file I/O, process management,
network calls, or trading state transitions at boundaries that need
characterization before refactor.

Coverage should remain informational until high-risk behavior is protected by
targeted tests.

## Deferred Risks

The following risks are intentionally deferred:

- Engine strategy and accounting behavior are not fully characterized.
- Order execution and exchange API semantics are not protected enough for
  refactor.
- SQLite snapshot, event, history, and compatibility mirror behavior needs more
  persistence characterization.
- AI sidecar prompts, schema normalization, fallback behavior, and signal output
  semantics need targeted tests before cleanup.
- Detached wrappers and orchestrator process semantics overlap and need contract
  tests before deduplication.
- Legacy and research scripts may still represent local operator workflows or
  historical comparison tools.
- Broad `E501` cleanup would create noisy diffs if done in one branch.
- A `src/` layout migration would affect imports, entry points, runtime paths,
  wrappers, docs, and deployment assumptions.

## Do-Not-Touch-Yet List

Do not change these areas until the prerequisites below are complete and a
branch explicitly authorizes the work:

- Trading strategy behavior.
- Order execution behavior.
- Engine main loop.
- Exchange API semantics.
- Runtime persistence semantics.
- AI decision semantics consumed by the engine.
- Dashboard mutation/control contract.
- Telegram operator command behavior.
- Runtime file paths.
- Legacy/research script locations.
- Migration tool behavior.
- `src/` package layout.
- CI enforcement thresholds.

## Next Branch Sequence

Recommended next-phase branches, in order:

1. `chore/ruff-e501-tests`
   - Wrap long lines in tests only.
   - Preserve assertions, fixtures, payloads, and expected data.

2. `test/core-engine-characterization`
   - Characterize pure engine/accounting/runtime helper behavior before touching
     trading code.
   - Avoid live exchange calls and avoid changing engine behavior.

3. `test/persistence-characterization`
   - Protect SQLite snapshot, event, history, and runtime compatibility behavior.
   - Include JSON/JSONL mirror assumptions where they affect migration or
     recovery.

4. `test/ai-sidecar-characterization`
   - Protect AI sidecar payload construction, schema normalization, fallback,
     stale-signal, and write-signal behavior.
   - Keep AI output advisory unless existing configuration consumes it.

5. `test/wrapper-orchestrator-contracts`
   - Protect process start, stop, status, PID, log, and command construction
     behavior for wrappers and the orchestrator.

6. `chore/ruff-e501-helpers`
   - Wrap long lines in helper modules after characterization coverage improves.
   - Avoid engine, execution, migration, and legacy script behavior changes.

7. `chore/ruff-e501-migration-tools`
   - Wrap migration tooling separately.
   - Preserve payload shapes, CLI behavior, and documented manual commands.

8. `refactor/wrapper-runner-dedup`
   - First low-risk refactor after tests.
   - Deduplicate wrapper process-management code only after wrapper/orchestrator
     contracts are covered.

9. `docs/src-layout-migration-plan`
   - Planning only.
   - Do not move files or change imports yet.

## Branches Before Any Application Refactor

Complete these before any behavior-preserving application refactor:

- `chore/ruff-e501-tests`
- `test/core-engine-characterization`
- `test/persistence-characterization`
- `test/ai-sidecar-characterization`
- `test/wrapper-orchestrator-contracts`

The first real refactor should be limited to wrapper-runner deduplication after
wrapper and orchestrator contracts are protected.

## Prerequisites Before Refactoring `engine.py`

Before refactoring `engine.py`, add characterization coverage for:

- Runtime state loading and update decisions.
- Grid/accounting calculations that can be tested without exchange access.
- Status, cumulative, trade, and event output shapes.
- AI signal consumption boundaries and stale-signal behavior.
- Pause, resume, panic, and risk-control state interactions.
- File persistence calls or adapter boundaries used by the engine.

Do not refactor the engine main loop, trading strategy, order execution, or
exchange semantics until these tests exist and pass in CI.

## Prerequisites Before Moving Legacy or Research Scripts

Before moving legacy or research scripts:

- Confirm operator usage with the repository owner or operators.
- Check docs, shell workflows, service managers, Telegram commands, and imports.
- Add characterization tests for any retained behavior.
- Preserve documented command compatibility or provide an explicit replacement.
- Keep runtime archives and generated outputs out of Git.
- Move files only in a dedicated branch with no logic changes.

`engine_trend.py`, `advisor.py`, `grid_engine_honest.py`,
`grid_engine_honest_v2.py`, and migration tools should remain in place until
their ownership and expected behavior are confirmed. Legacy Baserow tooling has
been removed.

## Prerequisites Before `src/` Layout Migration

Before a `src/` migration:

- Document the intended package structure and import boundaries.
- Confirm entry points for engine, dashboard, Telegram, AI sidecar, wrappers,
  smoke checks, and migration commands.
- Confirm runtime file path compatibility and operator workflows.
- Update tests to import through stable package paths.
- Decide whether root-level compatibility wrappers are required.
- Confirm packaging/dependency metadata and CI commands.
- Avoid mixing file moves with behavior changes.

The next `src/` branch should be planning-only.

## Success Criteria for the Next Phase

The next phase is successful when:

- CI remains green after every branch.
- Coverage increases through characterization tests around high-risk behavior.
- `E501` findings are reduced by area without broad formatting churn.
- Engine, persistence, AI sidecar, and wrapper contracts are documented by tests.
- No branch changes trading strategy, order execution, engine loop behavior, or
  runtime persistence semantics unless explicitly authorized.
- Legacy/research ownership is clearer before any quarantine or move.
- The first refactor is small, behavior-preserving, and backed by tests.
- A future `src/` migration has a written plan before any files move.
