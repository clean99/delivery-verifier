# delivery-verifier

## Problem and boundary

A coding agent can finish a change that looks convincing but is not ready to leave the machine. A regression test may pass without the fix. The same model may approve its own mistakes. An approval may survive an amended commit that the reviewer never saw. Debug statements, focused tests, generated attribution footers, or unfinished prose can remain in an otherwise passing change.

`dv` checks a local Git change and its evidence before a person shares it. It works with any coding agent that can run a command; Claude Code also gets optional hooks. It does not call a model, authenticate a reviewer, or publish anything.

The local commands, validation, evidence records, and optional hook adapters are implemented. Verification never substitutes a placeholder PASS for an unperformed check. The contracts below describe the implementation and its deliberate limits.

`dv` is not a security sandbox. It must not report a false PASS for hollow tests, wrong-reason failures, tests that never ran, skips, XPASS, errors, or results misreported through printed output, late rewrites of the report file in summary, unconfigure or exit hooks, or the process exit path. Test code, `conftest.py` files and plugins share the pytest process with the probe. Code there that rewrites pytest's own report objects (for example clearing `wasxfail` or changing `outcome` in `pytest_runtest_logreport`), or that tampers with `dv`'s probe objects or private pipe, is outside this threat model.

## Proof-only entry point

`dv prove --test COMMAND` executes committed snapshots in separate detached worktrees without creating an item. All runners use an owned OS temporary directory outside the caller's checkout and `.git`, so normal test collection and Git status cannot discover the execution copies. Logs and recovery records remain under Git's administrative `dv/proofs/` directory. Cleanup removes only the owned worktrees, caches and temporary root, including after failure or timeout; failed cleanup leaves exact paths in the recovery record. Hygiene and review are explicitly requested with `--hygiene` and `--require-review ITEM`. The command does not substitute for a delivery gate or weaken its hooks.

Proof subprocesses discard inherited `GIT_*` variables, including repository/index locators, while retaining other environment settings. Ordinary Git operations therefore target the detached execution worktree; this is not protection against commands deliberately addressing another path. Node TAP file-only successes are excluded from green's executed/passed counts. Node 20 renders these like top-level tests named by absolute paths, so those ambiguous test names are excluded as well; suite containers remain excluded by Node's own counts.

Only Node TAP and Vitest proofs prepare Node dependencies; Python proofs do not inspect or clone unrelated `node_modules`. Installed Node dependencies are copied, not mounted writable or hard-linked: the root `node_modules` (including an ancestor workspace's root) and package-local `node_modules` found along tracked-file ancestors are materialized per phase. Existing `.vite`, `.vitest` and `.cache` directories are omitted. pnpm links are rebased into the copies; workspace links resolve to that phase's committed source, and pnpm executable shims have embedded dependency paths rebased too. Links outside the checkout and copied dependency trees fail closed. Regular files use copy-on-write clones through macOS `clonefile` or Linux `FICLONE`, never hard links. Unsupported cloning falls back to `copy2` only after checking the whole dependency tree's block-rounded size plus a 64 MiB reserve; each fallback file rechecks free space. Permission and other operational cloning errors do not silently fall back. Copies prevent cache and dependency writes through a link into the caller's checkout, while clone-capable filesystems avoid duplicating dependency data. Tracked `node_modules` files keep committed bytes while remaining installed files are merged around them. Only Node command executables (including PATH resolution) and recognized runner path operands within the caller checkout are rebased to existing execution-copy paths; missing or ambiguous mappings fail closed with repo-relative-path guidance. Vitest positional file filters and `-c`/`--config`, `-r`/`--root` and `--dir` are repository-local path operands. Test-name patterns and project names remain verbatim; unsupported options are rejected before either phase. Python commands are not remapped, including project-local virtualenv executables and activated PATH launchers. External global tools remain external, including their cache side effects. No dependency installation occurs. Python commands retain their interpreter/environment and per-phase bytecode cache settings. Deliberate absolute-path writes remain outside this non-sandbox guarantee.

The [first-usable contract and validation record](design/first-usable-version.md) defines ref resolution, raw-blob materialization, retained tests, execution counts, cleanup and installation acceptance. The [handbook](handbook/green-tests.md) provides pinned synthetic replays and remaining blind spots. The sections below continue to describe the separate item-based delivery workflow.

## GitHub Actions adapter

The root composite action is a separate network-enabled adapter around `dv prove`; CLI ref resolution and proof criteria stay unchanged. The CLI defaults to local `main`. The adapter instead fetches an explicit `origin` branch, the PR base branch on `pull_request`, or the repository default branch, then passes the fetched commit through `--base`. Every branch is qualified as `refs/heads/...` before fetching, so a same-named tag cannot select a different base. Tags, commit IDs and remote-tracking aliases are outside the adapter's input contract; missing branches fail without a fallback. A full checkout is required. The checked-out `HEAD` is used, including GitHub's merge commit for the default PR checkout.

The adapter sets up Python 3.12 without changing the test command's PATH. By default, pipx installs the action's own source at the pinned action commit; an explicit version selects a GitHub Release wheel. The installation environment is temporary and removed after execution. Inputs reach the adapter through environment variables and subprocess argument arrays. Project dependencies remain the caller's responsibility, and `working-directory` names a repository root rather than changing the focused command's working directory inside a repository.

A successful process exit, complete passing proof, qualifying red/green checks and matching saved evidence are all required for `ok=true`. Missing or inconsistent results remain failures. The adapter copies only that attempt's evidence to a runner temporary directory, writes a short job summary and outputs, uploads the directory, then fails the action if proof failed. Adapter errors retain diagnostics even when the CLI never created a proof. Artifact uploads change the evidence visibility: logs can contain secrets, so use disposable PR runners, read-only permissions and no secrets. This adapter adds neither sandboxing nor delivery approval.

## Verification ladder

| Level | Question | Evidence and gate behavior |
| --- | --- | --- |
| V0 claim | What does the implementer say changed? | Work-item summary and optional description. A claim alone never passes a check. |
| V1 static | Does the change meet the repository's mechanical rules? | Recomputed hygiene, declared lint/format/typecheck commands, and the repository's full unit-test commands. Every required command must finish successfully. |
| V2 behavior | Does the regression test distinguish broken from fixed? | The focused command fails with source reverted and the new tests retained, then passes with the complete change restored. Both logs are retained. |
| V3 runtime | Does it work in the running product? | Optional local runtime evidence referenced by the item and inspected by the independent reviewer. A missing or unperformed runtime check is not reported as passed. |
| V4 independent review | Did someone other than the implementer examine this exact change? | A review record identifies the model, full commit ID, scope, verdict, and a substantive report. |
| V5 human approval | Did the person approve this exact code and description? | An explicit approval record bound to the full commit ID and the reviewed inputs. Required by the delivery gate, not the readiness gate. |

The readiness gate requires V1, V2, and V4. A reasoned test waiver replaces V2 with a visible warning, not a V2 pass. V3 is required when `runtime_files` is nonempty and remains a reviewer attestation, not an automatically reproduced runtime result. Report individual levels rather than claiming an unbroken ladder when a level was skipped.

## Work item and local state

One ignored directory holds `item.json` and its evidence alongside the working change. The default is `.dv/item.json`. Paths in the item are relative to the repository root, not the shell's current directory. The item is located inside that repository; no clone path, account, upstream service, or owner profile is stored.

```json
{
  "schema_version": 1,
  "summary": "Return an empty list for empty input",
  "base_ref": "main",
  "kind": "small",
  "implementer": "model-a",
  "status": "preparing",
  "test_command": ["python", "-m", "unittest", "tests.test_parser"],
  "test_cwd": ".",
  "test_files": ["tests/test_parser.py"],
  "description_file": ".dv/description.txt",
  "runtime_files": [],
  "exceptions": {}
}
```

| Field | Contract |
| --- | --- |
| `schema_version` | Required integer `1`. Unsupported versions, duplicate JSON keys, invalid types, and unrecognized fields fail validation. No implicit migration. |
| `summary` | Required nonblank string describing the behavior being changed. |
| `base_ref` | Required local Git ref or full commit ID. Resolve it locally and calculate its merge base with `HEAD` on every invocation. A lower branch can be the base of a stacked change. Never fetch. |
| `kind` | `small` by default, or `large`. Selects the source-size cap and review-independence requirement. |
| `implementer` | Required nonblank model identifier or local human label. It is an assertion, not an authenticated identity. |
| `status` | `preparing`, `ready`, `approved`, or `blocked`; default `preparing`. It declares intent and never substitutes for a gate result. |
| `blocked_reason` | Nonblank string required only for `blocked`. Explains the next action or missing prerequisite. |
| `test_command` | Nonempty argument array for the focused regression command, required unless `exceptions.no_test_reason` is present. Executed without an implicit shell. |
| `test_runner` | `auto` (default), `unittest`, `pytest`, or `node-test` for the item workflow. `vitest` is recognized only to reject legacy red/green with `dv prove` guidance. Auto detects direct supported commands; wrappers must declare the reporter contract. Unsupported red output fails verification. |
| `test_cwd` | Existing relative directory; default `.`. Used only for the focused command. Static and full-suite commands run from the repository root. |
| `test_files` | Relative paths to existing test files at `HEAD`, default `[]`. Supplements conventional test-path detection; it must not classify the production fix itself as a retained test. |
| `description_file` | Optional relative path to the exact local delivery text, such as a proposed PR body or handoff note. No title or issue-reference format is imposed. Its bytes are checked and approval-bound. |
| `runtime_files` | Relative paths to runtime evidence, default `[]`. Every file must exist and be nonempty, and its content hash is bound to review and approval. No file list means V3 is not claimed. |
| `exceptions` | Object with optional nonblank string values: `no_test_reason`, `comment_justification`, `lockfile_reason`, and `weak_test_justification`. Each is printed in the gate and reviewed; a boolean is not a justification. |

Metadata, evidence, and delivery text live in ignored paths so writing a gate result cannot invalidate its own commit. `dv init` adds the default `.dv/` entry to the repository-local Git exclude file without changing existing entries. A custom item directory must already be ignored. Reject tracked metadata paths, path traversal, and symlinks that escape the repository; do not write through symlinked metadata files. Source and test paths remain ordinary tracked files.

State moves `preparing` to `ready` after a successful readiness gate, and to `approved` after explicit approval. A failed gate does not quietly downgrade a ready/approved claim: hooks must still surface the failure. To stop honestly when work cannot proceed, set `status` to `blocked` with a reason. The gate still reports missing checks if explicitly run on a blocked item.

### Evidence binding

The inspected scope is the full `HEAD` commit ID plus the current merge-base commit ID. Abbreviated IDs are accepted as Git input where a ref is expected. Stored evidence and the review/approval commands' `--head` require full IDs (40 hexadecimal characters for SHA-1, 64 for SHA-256); `prove --head` accepts a local ref and records its resolved full ID.

Work-item evidence records carry `schema_version`, `at` (UTC timestamp), `head_sha`, `base_sha`, `checker_sha256`, and `context_sha256`. The initialization result has no context hash; the item is still an incomplete template. The context hash covers canonical JSON of the validated item with defaults (excluding only `status` and `blocked_reason`), effective configuration, both commit IDs, the checker digest, and the content hashes of the description/runtime files. Canonical JSON uses sorted keys, compact separators, UTF-8, and rejects non-finite numbers. Optional absent files have a null hash, not an empty-file hash.

`checker_sha256` hashes a canonical map of package-relative Python and bundled JavaScript source names to their byte hashes, including the version definition and vendored parser. Paths outside the package, installation location, bytecode caches, timestamps, and interpreter version are excluded. Adding, removing, or editing a source file changes the digest. Scope rechecks recompute it before publishing results; prior red/green, review, and approval records become stale after a checker change or when the field is missing. This detects source drift, not malicious runtime monkeypatching or forged local evidence.

| Sidecar | Contents |
| --- | --- |
| `redgreen.json` | Scope, focused argv/cwd, retained tests, reverted paths, both exit codes, timeout/launch status, log hashes, and computed result. |
| `redgreen-red.log`, `redgreen-green.log` | Combined stdout and stderr, stored locally and never uploaded. |
| `review.json`, `review.txt` | Scope, reviewer model, fresh-context declaration, verdict (`pass`, `fix-needed`, `drop`), and copied report with its hash. |
| `approval.json` | Scope, description/runtime hashes, exact review-record/report hashes, `by: "human"`, and the person's nonblank decision note. |
| `hygiene.json`, `gate.json` | Recomputed check rows, counts, warnings, failures, and remediation. They are diagnostic outputs, never authorization inputs. |
| `checks/` | Logs of configured static and full-suite commands, referenced by the latest gate result. |

A new commit, amendment, changed merge base, test recipe, exception, configuration, description, or runtime attachment invalidates prior bound evidence. Editing a review after approval invalidates approval. Rerunning a gate with the same inputs does not invalidate approval merely because its timestamp changed. JSON sidecars use atomic replacement; concurrent mutations of one item or one worktree are rejected by a local lock. Recheck scope and cleanliness before writing a successful result.

These hashes detect accidental drift. Anyone who can edit the repository and evidence can forge records; local JSON is not a signature or a security boundary.

## Commands

All work-item commands take an explicit `ITEM` path to `item.json`, except `init`, whose path defaults to `.dv/item.json`. `--json` selects machine-readable output for the work-item commands. Human diagnostics otherwise show PASS/WARN/FAIL, the exact scope, and a concrete next action. Neither output mode hides warnings or exceptions.

| Command | Behavior |
| --- | --- |
| `dv init [ITEM] --implementer MODEL [--base REF] [--kind small\|large]` | Validate a local repository with an existing commit, create a preparing item, and print what to fill in. Default base is `main`; a missing base is an error, not an inferred remote. Never overwrite an existing item, change branches, or install hooks. |
| `dv redgreen ITEM [--timeout SECONDS]` | Run the focused red/green proof, restore the working tree, and record the result. Timeout defaults to configuration. |
| `dv hygiene ITEM` | Inspect committed changes since the merge base, commit messages, and optional delivery text. Record findings; do not edit code or prose. |
| `dv review record ITEM --reviewer MODEL --head SHA --verdict pass\|fix-needed\|drop --file REPORT [--fresh-context]` | Copy the report and bind it to the current scope. `--head` must equal the inspected `HEAD`. Record negative reviews too. Recording a review does not make it pass the gate. |
| `dv approve ITEM --head SHA --note TEXT` | Re-run the readiness gate, require `--head` to match, and record a human decision for the exact current inputs. This command records approval; it does not obtain consent on the person's behalf. |
| `dv gate ITEM [--require-approval]` | Recompute readiness checks. With `--require-approval`, also require current human approval; this is the delivery gate. Never push, publish, or submit. |
| `dv hooks install --agent claude-code [--dry-run]` | Preview or install only the project-local Stop and PreToolUse entries. No global settings, permissions, agent prompts, or Git hooks are changed. |

Example after implementation, inside a repository with a committed fix:

```sh
dv init --implementer model-a --base main
# Fill in .dv/item.json and the exact optional delivery text.
dv redgreen .dv/item.json
dv hygiene .dv/item.json
dv review record .dv/item.json --reviewer model-b \
  --head "$(git rev-parse HEAD)" --verdict pass --file .dv/reviewer-notes.txt
dv gate .dv/item.json
# The person inspects the diff, text, and gate before approving.
dv approve .dv/item.json --head "$(git rev-parse HEAD)" --note "Approved this change."
dv gate .dv/item.json --require-approval
```

`review record` takes a report from a separate reviewer; it never invokes one. Compare normalized model identifiers (trimmed, case-insensitive, first token with surrounding punctuation removed) rather than treating annotations as a different model. Different models satisfy independence. The same model with an explicit fresh context is a warning for a small item and a failure for a large item. Missing implementer/reviewer identifiers fail. A fresh-context declaration is not proof of isolation; the reviewer must inspect the diff, repository, and evidence without inheriting the implementer's reasoning, and must not edit the change.

## Red-to-green proof

1. Resolve repository, base, and `HEAD`. Require a clean index and worktree, including nonignored untracked files, with no merge/rebase in progress. Require at least one source change and a retained regression test. Refuse dirty state instead of stashing or switching branches.
2. List changes with NUL-delimited Git output and rename detection disabled. Keep test files and documentation from `HEAD`. Temporarily restore other changed files to the merge base; remove source files added by the change and restore source files deleted by it. Reuse the working tree's installed dependencies.
3. Run the focused argument array from `test_cwd`, capturing a red log. No shell expansion occurs unless the user explicitly chooses a shell executable.
4. In a `finally` path, restore every touched source path and file mode to `HEAD`, preserving the original index. Never use a broad hard reset or delete unrelated untracked files. Verify restoration before running green; if restoration fails, stop with an operational error and identify affected paths.
5. Run the identical command with the full change restored and capture green. Recheck index/worktree, commit IDs, and context before persisting success. Test-created nonignored files or tracked mutations fail the proof and remain for inspection.
6. Accept only red exit 1 with a complete assertion-only report from the selected runner and green exit zero. Import, runtime, collection, setup/teardown errors and unsupported reports fail the distinct `red assertion` check. Timeouts, signal termination, command-not-found/launch failures, and restoration failures are rejected. The default timeout is 1,800 seconds per phase. A timeout kills and reaps the child process group before restoration on supported POSIX systems.

Each attempt atomically replaces old proof with `state: "running"` and `ok: false` before planning or changing source. A completed run atomically publishes `state: "complete"` only after both phases and cleanup checks. The gate rejects incomplete attempts and leftover restoration plans, including when a test waiver is configured. Before mutation, paths absent from `HEAD` must also be absent locally, including ignored files, directories, and dangling symlinks.

Both phases receive separate fresh `PYTHONPYCACHEPREFIX` directories and `PYTHONDONTWRITEBYTECODE=1`. Existing user bytecode caches are neither read by ordinary Python imports nor removed. Explicit Python flags that override the environment, and non-Python build caches, remain the command author's responsibility. All reverted paths are compared by content, file type, and mode during red, including baseline-only files invisible to `git diff HEAD`. Mutations retain a Git patch plus `redgreen-mutations.json` with before/after states and base64-encoded content before restoration. Treat these files as sensitive local evidence.

Supported report contracts are [listed in the README](../README.md#focused-runner-contract). Unittest requires positive failures without errors and assertion tracebacks. Pytest also requires each failure traceback to end in `AssertionError`, because a runtime exception inside a test also exits 1 and counts as a failure. Both Python runners additionally require a complete `DV-RUNNER-PHASES` record matching the failure count. For direct commands, both runs use the same bundled unittest launcher or pytest plugin. The launcher observes unittest result callbacks and checks the actual dispatcher frames, including async cases and subtests. The plugin checks pytest's report phase and, for unittest cases, the unittest dispatcher too: pytest's `call` phase alone also includes unittest setup, teardown and cleanup. Unittest lifecycle assertions, missing dispatch evidence, and uninstrumented reports fail closed. Wrappers must explicitly load the probe; selecting `test_runner` alone is insufficient. The plugin is loaded through the child command's `PYTHONPATH`; no runner is installed by `dv`.

Pytest green counts come from report objects, independently of terminal output, including when `-qq` suppresses the summary or the terminal plugin is disabled. The receipt adds `xpassed`, `xfailed` and `errors`; expected failures remain part of `skipped`, and lifecycle errors may also appear in `failed`. Strict XPASS is identified from pytest's failed report representation. Green requires zero failures, XPASS and errors, complete setup/call/teardown reports, and at least one passed body. Warnings do not disqualify a passing run. Collection failures and internal errors are recorded; missing, duplicate or unfamiliar phase outcomes fail closed.

The pytest plugin writes a nonce-bearing JSON report to a fresh temporary directory outside the execution checkout. At emission it also sends the SHA-256 of those exact bytes through an inherited pipe, then closes its writer. The explicit plugin consumes the descriptor environment variable before repository conftests load. After the child exits, `dv` reads at most 65 bytes without blocking, requires exactly the report's 64-byte hexadecimal digest, and validates the nonce and exit status. Only then does it append the report as `DV-RUNNER-PHASES` and record `runner_report_sha256` in the phase metadata before hashing the log. Missing or extra receipt bytes and file rewrites fail closed, including rewrites during unconfigure or process exit. Both red and green validate the phase binding; a printed record cannot replace missing probe evidence, and duplicate record lines fail closed. Pipe descriptors and the temporary directory are cleaned up on completion or interruption. The probe and plugins share a process; deliberate in-process tampering with `dv`'s own probe objects or private pipe is outside the threat model described above.

Node TAP requires `ERR_ASSERTION` underlying failures and zero cancelled tests. Propagated `subtestsFailed` entries must have failed descendants. Failed parent tests count toward the summary, while entries marked `type: 'suite'` do not. Jest and other reporters remain unsupported. Incomplete reports and hidden tracebacks fail closed. The runner name and configured recipe are stored in `redgreen.json`; the gate verifies them, checks log hashes, and reparses red output including phase evidence rather than trusting saved result flags. Output fixtures in `tests/fixtures/runners` come from the real runners, with provenance alongside them. Real-runner regression tests exercise the probe and nested TAP reports.

Vitest is supported only through `dv prove`; legacy `redgreen` rejects it before locking or reverting the caller, and the legacy gate rejects saved Vitest evidence. Vitest accepts 3.2.x and exactly 4.1.12 with the stock runner and a forks/threads pool; other versions and custom runners, custom pools or browser mode fail closed. Instrumentation disables coverage with `--coverage.enabled=false`. Configured global setup/teardown is rejected in both the root configuration and every project, including a root that defines `test.projects`. On 3.2.x, extended test fixture initialization inside the body wrapper is excluded because it shares Vitest's test-function wrapper; the probe observes fixture context resolution through `getWorkerContext` in a per-test async context. On 4.1.12, source-checked instrumentation observes the original handler after fixture resolution; clean test/file/worker fixtures are supported, while fixture setup/teardown errors remain outside the body. Auto detection covers `vitest`, `npx vitest`, `pnpm vitest`, and `pnpm exec vitest`. Instrumentation forces one run and retains the default human reporter. The CLI allowlist accepts the `run` command, bare `--run` and `--globals`, positional file filters, `-t`/`--testNamePattern`, `--project`, `--maxWorkers`, `--minWorkers` (3.2.x only), `-c`/`--config`, `-r`/`--root` and `--dir`. Value options accept separate or `--option=value` forms. Every other option, including `--diff`, `--workspace`, watch, report and output overrides, is rejected before either phase; missing values are rejected too. `npx` uses `--no-install`.

Vitest uses harness identity rather than config-basename retention. Each phase loads its own committed config. The reporter records every resolved project's config file and `configFileDependencies`, resolved `setupFiles`, and Vitest's selected workspace file. Vite's module graph extends setup/workspace inputs to observed imports. For each resolved `mode` and `envDir`, it records `.env`, `.env.local`, `.env.<mode>` and `.env.<mode>.local`, including absent files. Repository-local inputs carry relative paths and SHA-256 of raw bytes. External inputs also carry raw-byte hashes, including an explicit absent state; they are never silently dropped. Their labels contain a SHA-256 of the absolute path and the filename, under `<home>` or `<absolute>` placeholders. Python checks local inputs against the materialized phase and its committed entries and rejects local symlinks. Both local and external project/path/hash sets must match between phases. PRs changing these reported phase inputs fail closed, including config imports, setup changes and environment-file additions/deletions. The diagnostic names the changed paths and explains why red cannot be attributed to product code.

The reporter also stores a SHA-256 per top-level key of the effective resolved test configuration, plus Vite `env`, `define` and `resolve`. Canonicalization sorts object keys, preserves scalar/container types, represents regular expressions and function source, and normalizes execution-checkout paths. RegExp sources normalize literal and escaped root paths to typed root markers while retaining flags and `lastIndex`. Instrumentation-owned `reporter`, `reporters` and `runner`, cache settings, API ports/generated tokens and probe environment fields are excluded. Unsupported object types and cycles fail closed. Vite `resolve` settings reject functions, including alias `customResolver` callbacks in function or object form, because source text does not identify a closure's resolution target. Declarative aliases remain supported. Per-key hashes must match; diagnostics expose key names only, never values. This also catches computed configuration, such as reading a custom `package.json` field with `fs.readFileSync`. Time- or random-derived resolved values must be stable between phases; otherwise proof fails closed. The contract holds constant the harness inputs Vite/Vitest report and the effective resolved configuration. Files read by plugins or other code that Vite does not report count as part of the change under test. For example, changing `offset.dat` read through `fs` by a test-transform plugin can produce PASS with an unchanged product module: the proof says the test detects the `offset.dat` change. `dv` does not intercept filesystem reads or capture arbitrary side effects and closed-over state. A read that changes a resolved configuration field still fails that field's hash comparison. This remains outside a security-sandbox guarantee.

Test-support retention is shared across runners through the path classifier. Existing test/spec/e2e directories, `__tests__`, `__mocks__` and fixtures are retained from head; `test-utils`, `test_utils` and every `conftest.py` are explicitly included. Base-only regular support is removed. A test-support symlink is accepted only when its path, mode and target bytes are unchanged between base and head. Keep the base link rather than overlaying it; record its mode and target-byte hash with the retained inputs. Its relative target must resolve directly to a tracked regular file inside both execution snapshots, with identical file bytes and modes. Changed production targets, phase-varying documents, added/deleted/changed links, escaping paths, directories, dangling targets, chains and loops are rejected before execution. Resolve every component against the committed trees, including components before `..`, not the caller's filesystem. Explicit `--test-file` inputs still require regular files. Command-directory guesses do not exempt links: a selected test may import helpers elsewhere in the repository. A changed `tests/helper.js` exporting an expected value cannot create the required production delta; with a real product change, both phases use the helper's head value. Arbitrary imported product modules remain phase-specific. Test-only helpers belong under the documented support paths. Environment filenames such as `.env.test.local` are excluded from the filename-only `.test.<extension>` convention and instead enter the Vitest harness receipt.

On 3.2.x, the bundled reporter checks the root and every resolved project before setting each project's in-memory runner to a subclass of `VitestTestRunner`. The subclass preserves stock lifecycle methods and wraps `getFn(test)()` to record body errors. It stamps every collected file and executed test with a fresh per-run nonce. A complete report with the matching nonce is required; there is no reporter-only fallback. This matters because Vitest reports a beforeEach-returned cleanup or `onTestFinished` assertion as a test failure even when `result.hooks` contains only passing states. Body errors must equal final errors, including their names and messages. Assertions from cleanup, callbacks and soft assertions in setup are therefore excluded.

Receipt schema 7 adds `external: boolean` to each harness input alongside its `path` and nullable `sha256`. External paths retain location identity through a path hash and filename under `<home>` or `<absolute>` placeholders, without exposing machine prefixes. The version gate accepts Vitest 3.2.x and exactly 4.1.12, with no separate Vite version gate.

Receipt schema 7 retains task `id`, `type`, `mode`, `fails`, `result.state`, `result.errors[].name/message`, `result.hooks`, `result.retryCount`, and `result.repeatCount`, plus body-phase errors and activation markers. Each body error has `assertion: "chai" | "node" | "snapshot" | null`. Chai/Node classification requires the live native Error instance and its real assertion constructor. For `expect.soft`, the probe observes Chai's live `toJSON` call and binds the serialized error to that test's async context. A renamed TypeError or thrown plain object cannot qualify by name alone. On 3.2.x, snapshot classification observes a failing `SnapshotState.match` or property comparison inside `SnapshotClient.assert`. On 4.1.12, it binds `SnapshotClient.match` results to the exact matcher-created Error; message text alone is insufficient.

`onTestRunEnd` supplies the task tree but does not finalize evidence. The reporter records main-process uncaught exceptions and unhandled rejections, observes resource-close promises before Vite can swallow their rejections with `allSettled`, and captures stderr during shutdown. It synchronously writes the final receipt from process `exit`, including `exitCode`, `shutdownComplete`, `lateErrors`, and `lateStderr`. Errors or stderr after that write invalidate the owned file. A completed close and empty late-error/diagnostic lists are mandatory. Red requires normal exit 1, a complete task tree, at least one failed test, and a recognized live assertion classification on every body error. File/suite errors, inconsistent or unfinished states, unhandled errors, failed hooks, retries, repeats and expected failures are rejected. Green requires normal exit 0, at least one passed body and no errors; empty or all-skipped/todo runs cannot pass.

Inline and standard external snapshots run read-only: `CI=true`, inherited `UPDATE_SNAPSHOT` removed, and `--update=false` on 3.2.x or `--update=none` on 4.1.12. The reporter verifies resolved snapshot update mode is `none`, and the environment save/remove methods reject writes. This also blocks reserialization of obsolete entries, which update mode alone does not prevent. Resolve such obsolete entries before proof. Missing snapshots fail without writing files. Existing retention logic overlays head's `.snap` and `__snapshots__` files alongside regression tests, including newly added external snapshots. CLI validation consumes value-taking options before interpreting positional subcommands, preserving keyword filters such as `-t watch` and `--testNamePattern dev`.

The probe wraps the actual snapshot environment's `readSnapshotFile` before `SnapshotState.create` loads its file. Every consulted path and raw-file-byte SHA-256 (or `null` for absence) is included in the file's `snapshots` array; decoded matcher text is not hashed. Snapshot assertions also associate the observed input with their test ID, including raw snapshots and hook assertions. Schema 7 records file/project identities, each test's snapshot inputs and ordered consumed entries. The stock matcher's counter determines the entry key; the probe captures the expected serialization before standard matching can overwrite it and verifies the returned key. Entry records contain file path, kind, key and expected-serialization SHA-256 (or `null` for absence), including inline and raw snapshots. Both `toMatchFileSnapshot` and custom `resolveSnapshotPath` therefore use the same check: each read and its materialized file must match the tracked head blob, including presence/absence. A changed or added snapshot outside the retained inputs fails closed rather than manufacturing red. External, symlinked and nonregular snapshot inputs are rejected. Before accepting proof, red and green must have identical snapshot path/hash sets for each file and each test, plus identical ordered entry sequences per test. Identity retains repo-relative file path, project, full test name and collection occurrence. Snapshot-bearing tests must additionally have unique full names within a file/project; a same-named skipped or non-snapshot sibling also fails closed. This prevents collection-order changes from rebinding an ordinal to another parameterized case. The diagnostic asks for case interpolation, such as `test.each(...)('value %s', ...)`. Ordered entry binding still catches switched expectations for uniquely named tests and identifies the first differing entry. A read or consumed entry in only one phase also fails closed, including a later assertion that red never reaches.

The 4.1.12 adapter rejects direct `_snapshotData` and `_initialData` cache reads. Source-checked instrumentation moves stock cache operations to private fields and guards the former public accessors. Only the adapter's synchronous expected-entry read is authorized; direct access marks the file unsupported even if test code catches the error. Standard matchers continue to record ordered per-test entry consumption in both forks and threads.

Reporter and runner files are copied from the installed package into an exclusive temporary `node_modules/dv-probe` directory outside the execution checkout and removed after the phase. Vite loads these `.mjs` files as external Node dependencies, keeping them outside Git status and normal source collection. Their loadability does not depend on a site-packages, pipx or `PYTHONPATH` location. Vite 6.4.5 applies its filesystem allow/deny policy to SSR source loads, unlike 6.3.5's server-consumer exemption; directly loading a Python-package reporter path can therefore produce a misleading module-not-found error. Its `.git` deny rule independently blocks worktrees under `.git`. Neither the user's `server.fs.allow`, `server.fs.deny` nor `server.fs.strict` is changed. The real-test matrix covers Vitest 3.2.4 with Vite 6.3.5, 6.4.5 and 7.3.7, npm/pnpm layouts with and without project `package.json`, isolated-wheel execution, and pnpm workspace-local dependencies.

Each phase sets `TMPDIR`, `TEMP` and `TMP` to its exclusive temporary root, so Vitest SSR transforms and probe files share the existing outer cleanup on success, failure and timeout. Only that owned root is removed. Effective-configuration hashing normalizes its path with a typed temporary marker, including RegExp sources.

Each phase writes JSON to a fresh dv-owned temporary file. After execution, dv checks its nonce and exit status and embeds it as one `DV-VITEST-REPORT` record in the log before hashing; the temporary file is removed. `prove` validates both Vitest receipts and their snapshot parity before publishing PASS. Probe JavaScript is included in the checker digest and installed resources. Real 3.2.4 fixtures include lifecycle failures, cleanup/callback ambiguities and a two-project configuration; provenance records source, command and tool versions.

The test-body boundary is runner-specific. For unittest, it is execution of the test method, including helper calls and subtests, but excluding `setUp`/`tearDown`, their async counterparts, and cleanups. This boundary also applies to unittest cases run by pytest. For pytest test functions, `dv` treats the entire **call phase** as the test body: everything that runs while the test function is being called, including fixtures dynamically requested through `request.getfixturevalue()` and call-phase hooks such as `pytest_runtest_call` in `conftest.py`. Assertions in those fixtures or hooks count as test-body failures even though they are outside the test function's own statements. Setup/teardown phases and collection errors are excluded. This is a phase boundary, not statement-level attribution; reviewers should read the red logs to locate the failing assertion and verify that it proves the intended regression.

A zero red exit rejects a hollow regression test. A qualifying assertion can still concern an unrelated behavior. Reporter text is trusted local output, not an authenticated event stream.

The in-place approach preserves dependency availability but is not a sandbox. Test commands are trusted local programs and can have arbitrary side effects; `dv` itself never installs dependencies or accesses the network. Run only commands the repository owner trusts. Initially, mutation-based proof and hook integration target POSIX; unsupported process-control environments fail explicitly rather than promising safe cleanup. Uncatchable termination can still interrupt restoration; report the restoration plan before mutation and retain it until cleanup succeeds.

Rust trailing `#[cfg(test)]` modules and `#[test]` functions count as tests for hygiene and size. Red/green rejects every changed Rust production file containing inline tests in either version, even if the inline tests did not change. Separating only the new regression test does not enable proof: both merge-base and HEAD versions must be free of inline tests, so any separation must already be included in the chosen baseline. This version does not rewrite mixed files. Listing the whole production file in `test_files` is not a way to claim the fix was removed. Do not revert away the regression test and report its absence as a behavioral failure.

## Hygiene

Inspect added lines for content rules, and additions plus deletions for size. Include added, modified, renamed, and deleted files; binary changes get a file entry without invented line counts. Test-path conventions cover `test`, `tests`, `spec`, `__tests__`, fixtures, end-to-end directories, `test_*.py`, `_test.go`, and `.test.*`/`.spec.*` files. Explicit `test_files` supplement classification.

| Check | Default result |
| --- | --- |
| New source comments | Fail without `comment_justification`. Replacing an existing comment in the same diff hunk warns rather than counting as a wholly new comment. Shebangs are not comments. |
| Test comments and directives | Test comments do not fail; more than three new test-comment lines warn. New lint/type-ignore directives warn and stay visible to review. |
| Authorship tells | Generated-by footers, AI coauthor trailers, robot symbols, AI self-references, and unresolved placeholders fail in new code comments, commit messages, and optional delivery text. Provider names used as technical subject matter are not a hard failure. |
| Prose filler | Count stock filler phrases across commit messages and delivery text. Warn up to three hits; fail above three. More than two em dashes or decorative emoji add a soft finding. These are style heuristics, not AI-origin detection. |
| Debugging remnants | New `console.log`, `console.debug`, or `debugger` in production code fail. Test code is exempt from this debug-output rule. |
| Focused tests | New `it.only`, `test.only`, `describe.only`, `fit`, or `fdescribe` in test code fail. |
| Weak tests | Recognized new test blocks without assertions, or with only existence/truthiness assertions, fail. Snapshot files are excluded. This lexical check is not a substitute for V2 and does not claim complete language coverage. |
| Missing regression test | Fail when no test changes, unless `no_test_reason` explains why a test is inappropriate. The reason is printed as a waiver. |
| Lockfiles | Fail unless `lockfile_reason` explains an intentional dependency change. Lockfiles remain listed but are excluded from source-size caps. |
| Junk and unfinished work | Editor settings, environment/secret-like files, and build outputs remaining at `HEAD` fail; deleting them is allowed and counted. New TODO/FIXME/XXX/HACK markers fail only in comments, not string data. This is not a general secret scanner. |
| Whitespace | Nonzero `git diff --check` fails even if its diagnostic happens to be empty. |
| Source size | Warn above 200 changed source lines; fail above 400 for small items or 900 for large items. |
| Tests and generated content | Warn above 1,200 changed test lines. Report source, test, documentation, generated, lockfile, and snapshot counts separately. Generated files do not consume the source cap or undergo comment/weak-test style checks, but junk-path checks still apply. |
| Commit noise | More than three commits in the inspected range warns. No built-in author name or email requirement. |

Generated paths include `generated/`, `__generated__/`, `.generated.*`, and `.gen.*`, supplemented by configured repository-relative globs. Generated classification never silently hides files from the report.

For Rust inline test counting, inspect old and new file versions separately. Attribute changed lines in a recognized trailing test section to tests, including deletions. Recognize both a test module and a bare trailing test function. Do not classify production code after a non-trailing test block as test code; ambiguous layouts remain source and warn. Regex checks have documented language limits; avoid adding parser dependencies merely to claim universal coverage.

Language-aware test entry rules keep Rust attributes attached to their functions, recognize `should_panic`, and find JavaScript callback bodies after options objects and destructured parameters. Focused-test detection includes `.only.each` calls and tagged tables. Python boolean assertions inspect argument expressions; comparisons and behavior calls count, while simple existence/truthiness checks remain weak. Go recognizes Testify `require`/`assert` imports, aliases, and receivers created with `New`. For unrecognized frameworks, item exception `weak_test_justification` explains the assertion contract and changes weak-test failures to visible warnings. It does not waive V2 or any other gate. These remain heuristics, not full parsers.

Strings, comments, and recognizable JavaScript regex literals are separated before debug scanning. Authorship checks inspect extracted comments, not string values on the same line. Exact `allowed_prose_lines` exemptions match the complete original source line for comments. `allowed_code_lines` exempts exact lines only from debug-output and unfinished-comment checks. Both exemption lists are reported visibly. The initial tell/style rules are conservative defaults. Exact-line exemptions in repository configuration allow required attribution or legitimate technical examples; report those exemptions. The tool does not decide whether an external project requires AI disclosure, and it must not instruct users to conceal required disclosure.

## Configuration

Read defaults, then `[tool.dv]` in the repository's `pyproject.toml`, then `[dv]` in `.dv.toml`. Merge tables recursively; a later scalar or array replaces an earlier value. No home-directory configuration, environment-based policy overrides, or network discovery. Invalid types and unknown keys under the owned tables fail instead of silently weakening a gate; unrelated TOML sections are ignored.

```toml
[tool.dv]
schema_version = 1
static_commands = [["python", "-m", "compileall", "-q", "src"]]
test_commands = [["python", "-m", "unittest", "discover", "-s", "tests"]]
timeout_seconds = 1800
generated_paths = ["src/generated/*"]
allowed_prose_lines = []
allowed_code_lines = []

[tool.dv.limits]
warn_source_lines = 200
max_source_lines = 400
max_source_lines_large = 900
warn_test_lines = 1200
max_soft_tell_hits = 3

[tool.dv.hooks]
items = [".dv/item.json"]
max_stop_blocks = 3
allow_commands = []
```

For `.dv.toml`, replace `tool.dv` with `dv` in table names. Command arrays default to empty, generated/exemption lists to empty, and hook items to `[".dv/item.json"]`; other defaults are shown above. A readiness gate requires at least one static command and one full-suite command. A nonblank `no_test_reason` can waive the full-suite requirement as well as V2 for changes such as documentation, but cannot waive static checks or convert a skipped test into a pass. `dv init` does not guess or run commands; the owner supplies the repository's real checks.

Timeouts and size limits must be positive integers, `max_stop_blocks` must be a nonnegative integer, and the large source cap must not be smaller than the small cap. An explicit red/green timeout override is stored in its result; it does not change any gate criterion. Hook retry state is scoped to the current session, never a shared global counter.

Python 3.10 has no `tomllib`. The configuration module uses the standard-library parser on newer Python versions and an unchanged Python-3.10-compatible copy of CPython 3.14.6's `tomllib`, with its MIT license retained, on 3.10. No third-party runtime dependency or partial TOML grammar is used. Compatibility fixtures exercise both parsers.

## Gate execution and exit codes

A gate resolves the current scope, validates clean committed state and all referenced artifacts, recomputes hygiene, runs static/full-suite commands, checks red/green evidence and independent review, and optionally checks approval. Review verdict must be `pass`; `fix-needed` and `drop` cannot be overridden by approval. Missing or empty review reports fail. Missing logs, malformed evidence, mismatched hashes, or stale scope fail closed with a rerun instruction.

Check mergeability against the locally resolved base ref with `git merge-tree --write-tree`; conflicts fail and being more than 200 commits behind that ref warns. This requires Git 2.38 or newer. Do not use checkout or fetch for the mergeability check. Runtime files establish that evidence exists and is review-bound, not that screenshots or demonstrations were independently reproduced.

| Exit code | Meaning |
| --- | --- |
| `0` | The requested operation completed successfully; for a check, no failing rows. Warnings and explicit waivers remain visible. Help/version also return zero. |
| `1` | Verification did not pass: bad/absent evidence, stale approval, dirty worktree, weak regression proof, policy finding, or a completed check command that failed. |
| `2` | Invalid invocation, malformed item/configuration, unsupported schema, invalid path/ref, or wrong argument type. |
| `3` | Operation could not complete: missing executable, unsupported platform, timeout, I/O/Git failure, restoration failure, or concurrent mutation. Never a verification pass. |
| `130` | Interrupted by the user; attempt restoration before returning. |

Machine output for a completed check includes `schema_version`, `command`, `ok`, `head_sha`, `base_sha`, and `checks`. Each row has `check`, `outcome` (`pass`, `warn`, `fail`), `detail`, and `fix` for failures. `ok` is computed from rows. Errors in JSON mode use `schema_version`, `command`, `ok: false`, and `error: {code, message}`. Logs never mix into JSON stdout. Argument errors may be argparse diagnostics on stderr.

## Claude Code hooks

Installation is opt-in and the supported minimum Claude Code version is 2.1.310. `dv hooks install --agent claude-code --dry-run` prints only the installer's hook entries, destination path, and status without writing or echoing existing settings. Existing `env`, permissions, and unrelated hooks never appear in installer output. Actual installation merges only its own command entries, keeps every unrelated setting, is idempotent, and refuses malformed settings or symlink escapes. It never changes permissions, global configuration, `CLAUDE.md`, branch protection, or Git hook settings.

The installed command entries call `dv hooks run stop` and `dv hooks run pre-tool-use`, private adapters reading one hook JSON payload from stdin. Resolve the installed executable to an absolute path and quote it for the host shell so pipx/uv environments and repository paths with spaces work. Validate `cwd` against the configured project rather than trusting a path supplied by a shell string.

- **Stop:** inspect configured items marked `ready` or `approved` using the local gate (approval required for `approved`). A failing claim emits `{"decision":"block","reason":"..."}` and exits zero according to the hook protocol. Preparing/blocked items do not trap a session. Missing configured items or malformed records/settings are reported as problems, not silently skipped. Consecutive blocks with `stop_hook_active` are counted per session in ignored state. After three blocks, the next attempt is allowed with a visible unresolved-problem message and saved failure state. This escape ends the session; it never approves a change or passes a gate. A successful check or a new stop sequence resets the counter.
- **PreToolUse:** match Bash commands that directly attempt `git push`, including ordinary `git -C` forms. Do not execute the push. Require a passing delivery gate for the target repository's configured items and require pushed source refs to equal the approved current `HEAD`; otherwise emit `hookSpecificOutput.permissionDecision: "deny"` with a reason and exit zero. Unsupported push syntax, multiple target repositories, ambiguous refspecs, force pushes, and commands that combine mutation with a push are denied with a manual-delivery instruction. No special exemption exists for a wrapper command or a project's own repository. An exact string in `hooks.allow_commands` explicitly exempts that command, with a visible message that no gate pass is claimed. Ordinary reads and local commits remain allowed so a new commit can invalidate evidence normally.

PreToolUse command entries set `onFailure: "block"` so adapter crashes and host timeouts deny the operation. Installed timeouts are `(static command count + full-suite command count) × item count × timeout_seconds + 120`. The adapter sets a POSIX deadline 30 seconds earlier, turns catchable termination/interruption into deny JSON, and reaps command process groups. Reinstall after changing those configuration values. Stop uses the same time budget but retains its bounded retry behavior; a host process failure is not a passing verification.

This guard detects direct shell invocations, not arbitrary shell programs, aliases, subprocesses, other tools, or later races. It is a convenience barrier against common shortcuts, not a guarantee that code cannot leave the machine. Parse errors and gate crashes deny a recognized guarded operation. Other coding agents run `dv gate ITEM --require-approval` explicitly; `dv` does not enforce their permissions. The guard also denies recognized direct GitHub PR/issue/repository writes and mutating `gh api` calls unless exactly allowlisted. It does not perform those operations. Package publishing and arbitrary shell programs are outside the hook's scope.

## Package and acceptance

Distribution name: `delivery-verifier`; import package: `dv`; console script: `dv = dv.cli:main`; Python requirement: `>=3.10`. Runtime imports use the standard library only; Git and user-configured check executables are external prerequisites. Setuptools is a build-time dependency, not an installed runtime dependency. Local `pipx install .` and `uv tool install .` are installation targets, not publishing operations.

The package separates work-item/config validation, Git/subprocess access, red/green execution, hygiene classification, review/approval evidence, gate composition, and hook adapters under `src/dv/`. Sandbox Git repositories in the `unittest` suite cover the command contracts. There are no service clients or agent-provider dependencies.

Required acceptance coverage as those modules land:

- A real temporary Git change fails red and passes green; a test that always passes is rejected. Source additions/deletions, paths with spaces, failures, timeouts, and interrupts preserve the developer's original tree/index. Dirty trees are rejected before mutation.
- Added comments, edits of existing comments, technical provider names, authorship footers, debug code, focused/weak tests, generated paths, lockfiles, and missing regression tests produce the specified outcomes.
- Source/test/generated/deleted-file counts and Rust inline sections are measured separately; large tests do not trip the source cap. Mixed source/test red-green proof is refused rather than misrepresented.
- Same-model reviews, fresh-context exceptions, full commit IDs, stale base/head/config/text, changed review reports, negative reviews, and missing approvals cannot accidentally pass.
- Gates rerun declared repository checks; neither forged `ok` fields nor old `gate.json` files replace computation. Approving and then gating unchanged inputs succeeds without a hash/timestamp cycle.
- Hook installation preserves unrelated settings and is idempotent; payload handling, push target/ref checks, malformed inputs, Stop retry bounds, and nondefault item locations have deterministic tests.
- Python 3.10 and a current Python version parse the same valid/invalid configuration fixtures. Package install creates a working `dv` entry point with no third-party runtime dependencies.

Run `PYTHONPATH=src python -m unittest discover -s tests -v` and `ruff check .` with Ruff 0.17.1, as CI does. If Ruff is unavailable offline, `python -m compileall -q src tests examples` provides syntax validation only; it does not complete the required Ruff check.

## Deliberate non-goals

- Repository discovery, contribution qualification, maintainer policy research, issue assignment, GitHub authentication, PR submission, follow-up, ledgers, lanes, and personal profiles.
- Git push, package publication, deployment, remote fetching, CI polling, telemetry, dependency installation, or automatic network calls.
- Implementing fixes, generating tests, selecting/calling models, rewriting prose, or deciding approval for a person.
- Cryptographic attestations, authenticated identities, a general shell security boundary, complete secret scanning, or judging whether an assertion tests the intended behavior.
- A workflow server, shared database, scheduler, GUI, or agent-memory system. The unit of work is a local JSON file plus inspectable evidence.
