# Changelog

## 0.1.0 — Unreleased

### Added

- Local `dv` CLI for initializing work items, regression proof, hygiene checks, review records, readiness gates, and explicit human approval.
- Red-to-green execution that retains regression tests, temporarily removes production changes, records both runs, and attempts worktree restoration on failures.
- Hygiene checks for new comments, authorship footers, debugging remnants, focused and weak tests, lockfiles, unfinished work, and whitespace; separate source/test/generated size counts and Rust inline-test recognition.
- Evidence bound to the full commit ID, merge base, effective configuration, work-item inputs, delivery text, and runtime attachments. Approval also binds the exact review.
- Optional project-local Claude Code Stop and PreToolUse hooks, with visible exemptions and bounded Stop retries.
- Repository-only TOML configuration, JSON output, command logs, and defined exit codes.
- English and Simplified Chinese guides, an offline throwaway-repository demo, contribution and security guidance, and Python 3.10–3.13 CI.

### Fixed

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
