# Changelog

## 0.3.1 — 2026-10-28

### Fixed

- `dv prove` accepts unchanged tracked test-support symlinks that directly resolve to regular files inside both snapshots with identical target bytes and modes. Keep these links from base instead of overlaying them from head. Added, deleted or changed links, escaping targets, chains, loops and links to changed production files still fail before either phase.
- Pytest green validation uses probe report objects instead of printable terminal summaries. Passing runs with warnings, quiet output or no terminal reporter qualify; strict/non-strict XPASS and runner errors do not. The probe records expected failures and lifecycle/collection errors independently, and delivers its nonce-bound report through a temporary file outside the execution checkout. A digest sent by the probe through an inherited pipe detects later report-file rewrites before the parent binds evidence. Printed, missing or rewritten phase records fail closed.

## 0.3.0 — 2026-10-27

### Added

- Root GitHub composite action for `dv prove`, with explicit or event-derived base branches, release-wheel or local-source installation through pipx, per-phase timeouts, job summaries, outputs and uploaded evidence. Failed or incomplete proof fails the action; existing proof checks are unchanged.
- Action self-tests on Ubuntu and macOS for a qualifying pytest regression and a test that passes without the fix, plus English and Chinese CI examples for pytest and Vitest.

## 0.2.0 — 2026-10-27

### Added

- `dv prove` supports Vitest 3.2.x, tested with Vite 6.3.5, 6.4.5 and 7.3.7. Direct `vitest`, `npx vitest`, `pnpm vitest` and `pnpm exec vitest` commands are detected. Jest remains unsupported, and the legacy `dv redgreen` path rejects Vitest with `dv prove` guidance.
- Red qualifies only when tests fail on test-body assertions, classified from the live error (Chai, `node:assert`, snapshot mismatch). Hook, cleanup-callback, setup, import, suite, unhandled and late shutdown errors fail closed; retries, repeats and `test.fails` are rejected.
- Inline and external snapshots, read-only in both phases. Each test's consumed snapshot entries (file, key, expected content) must match between red and green, and snapshot-bearing tests need unique full names.
- Harness identity: the inputs Vite and Vitest report (config files and their dependencies, setup and workspace files, env files) and the effective resolved configuration must be identical in both phases, so a change to any of them fails closed. Files read by plugins or other code that Vite does not report count as part of the change under test.
- Vitest command-line options are an allowlist; others, such as `--diff`, are rejected before any phase runs.

### Changed

- Proof worktrees for every runner run in a temporary directory outside the repository; evidence stays under `.git/dv/proofs`.
- Node runners get per-phase copy-on-write clones of `node_modules` (APFS clonefile, Linux FICLONE), with a free-space check before falling back to a copy. pytest and unittest commands run as in 0.1.0.
- Each phase uses its own temporary root, removed after success, failure and timeout.
- Test-support directories (`tests/`, `__tests__/`, test-utils, fixtures, `__mocks__`) and `conftest.py` are retained from head for every runner.

## 0.1.0 — 2026-10-26

### Added

- `dv prove --test COMMAND`: one-command proof of committed base/head snapshots in separate detached worktrees, without item setup or a clean caller checkout.
- Positive green execution receipts for unittest, pytest and Node TAP; reject missing reports, all-skipped/expected-failure green, and abnormal execution.
- Per-attempt local logs, input hashes and recovery records; retain test bytes/modes, reject tracked-input mutations, and clean only owned worktrees and caches without checkout hooks or smudge filters.
- Explicit `--hygiene` and `--require-review` checks on the proof-only path; legacy delivery gates and hooks keep their existing requirements.
- A pinned, Apache-2.0 synthetic handbook covering hollow tests, wrong-reason failures, missed execution and stale reviews, with a replay driver and packaged resources. Source replay passed 8/8 combinations at implementation `cf4dbf8`.

- Local `dv` CLI for initializing work items, regression proof, hygiene checks, review records, readiness gates, and explicit human approval.
- Red-to-green execution that retains regression tests, temporarily removes production changes, records both runs, and attempts worktree restoration on failures.
- Hygiene checks for new comments, authorship footers, debugging remnants, focused and weak tests, lockfiles, unfinished work, and whitespace; separate source/test/generated size counts and Rust inline-test recognition.
- Evidence bound to the full commit ID, merge base, effective configuration, work-item inputs, delivery text, and runtime attachments. Approval also binds the exact review.
- Optional project-local Claude Code Stop and PreToolUse hooks, with visible exemptions and bounded Stop retries.
- Repository-only TOML configuration, JSON output, command logs, and defined exit codes.
- English and Simplified Chinese guides, an offline throwaway-repository demo, contribution and security guidance, and Python 3.10–3.13 CI.

### Fixed

- Clear inherited Git environment overrides in proof subprocesses so ordinary index operations affect only the execution worktree.
- Exclude Node TAP file-only successes from green test-body counts; top-level absolute-path test names remain ambiguous and are conservatively excluded.
- Resolve relative source import paths before handbook subprocesses change cwd; exercise the exact documented loop in regression tests.
- Clarify installation prerequisites, provide a venv/pip recipe, and document pipx/uv installation and PATH setup. Add version-pinned GitHub Release and Git installation paths.

- Preserve unittest subtest failure phases before Python 3.10 defers result callbacks; accept test-body assertions without accepting setup, teardown, cleanup, or runtime failures.
- Require test-body phase evidence from the bundled Python runner probe; reject unittest setup, teardown and synchronous/asynchronous cleanup assertions, including when run through pytest. Uninstrumented Python wrappers now fail closed.
- Reconcile nested Node TAP failures with parent tests and suite containers while requiring assertion failures in the children.
- Normalize sdist owner fields and archive metadata, and set a stable `SOURCE_DATE_EPOCH` for reproducible local release builds.
- Bind all evidence to a location-independent digest of checker sources; reject missing or stale checker bindings, including after upgrades.
- Require assertion-only red output from unittest, pytest, or Node TAP; reject import, runtime, collection, and fixture errors even when green passes. Revalidate saved red logs at the gate. Other reporters fail closed.
- Include complete tests, fixtures, demo, and referenced documentation in source distributions and wheel resources.

- Preserve ignored local files at deleted source paths; invalidate proof at attempt start and reject incomplete proof or recovery plans.
- Separate Python bytecode caches between proof phases; retain content/type/mode mutation evidence for baseline-only files.
- Fail closed on hook interruption, abnormal exit, and timeout; require Claude Code 2.1.310+ and omit existing settings from installer output.
- Disable Git lazy fetching and transport protocols, including partial clones with missing objects.
- Recognize JavaScript callback bodies and focused parameterized tests, Python behavior predicates, Go Testify aliases, and Rust test attributes without conflating strings and comments with code.
- Allow removal of committed junk files, preserve exact-line exemptions, and clarify the mixed Rust proof limitation.

### Scope

- Python 3.10+ standard-library runtime; Git 2.38+ required for gates. Red/green process control and hook integration target POSIX systems.
- Independent review and human approval are supplied attestations. No model calls, network services, authenticated identities, or publishing operations.
- Native Windows red/green, syntax-aware mixed Rust source/test rewriting, and arbitrary shell enforcement are outside this version's scope.
