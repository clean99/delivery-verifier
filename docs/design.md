# delivery-verifier

## Problem and boundary

A coding agent can finish a change that looks convincing but is not ready to leave the machine. A regression test may pass without the fix. The same model may approve its own mistakes. An approval may survive an amended commit that the reviewer never saw. Debug statements, focused tests, generated attribution footers, or unfinished prose can remain in an otherwise passing change.

`dv` checks a local Git change and its evidence before a person shares it. It works with any coding agent that can run a command; Claude Code also gets optional hooks. It does not call a model, authenticate a reviewer, or publish anything.

The local commands, validation, evidence records, and optional hook adapters are implemented. Verification never substitutes a placeholder PASS for an unperformed check. The contracts below describe the implementation and its deliberate limits.

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
| `test_cwd` | Existing relative directory; default `.`. Used only for the focused command. Static and full-suite commands run from the repository root. |
| `test_files` | Relative paths to existing test files at `HEAD`, default `[]`. Supplements conventional test-path detection; it must not classify the production fix itself as a retained test. |
| `description_file` | Optional relative path to the exact local delivery text, such as a proposed PR body or handoff note. No title or issue-reference format is imposed. Its bytes are checked and approval-bound. |
| `runtime_files` | Relative paths to runtime evidence, default `[]`. Every file must exist and be nonempty, and its content hash is bound to review and approval. No file list means V3 is not claimed. |
| `exceptions` | Object with optional nonblank string values: `no_test_reason`, `comment_justification`, and `lockfile_reason`. Each is printed in the gate and reviewed; a boolean is not a justification. |

Metadata, evidence, and delivery text live in ignored paths so writing a gate result cannot invalidate its own commit. `dv init` adds the default `.dv/` entry to the repository-local Git exclude file without changing existing entries. A custom item directory must already be ignored. Reject tracked metadata paths, path traversal, and symlinks that escape the repository; do not write through symlinked metadata files. Source and test paths remain ordinary tracked files.

State moves `preparing` to `ready` after a successful readiness gate, and to `approved` after explicit approval. A failed gate does not quietly downgrade a ready/approved claim: hooks must still surface the failure. To stop honestly when work cannot proceed, set `status` to `blocked` with a reason. The gate still reports missing checks if explicitly run on a blocked item.

### Evidence binding

The inspected scope is the full `HEAD` commit ID plus the current merge-base commit ID. Abbreviated IDs are accepted as Git input only where a ref is expected; stored evidence and `--head` require full IDs (40 hexadecimal characters for SHA-1, 64 for SHA-256).

Every result carries `schema_version`, `at` (UTC timestamp), `head_sha`, `base_sha`, and `context_sha256`. The context hash covers canonical JSON of the validated item with defaults (excluding only `status` and `blocked_reason`), effective configuration, both commit IDs, and the content hashes of the description/runtime files. Canonical JSON uses sorted keys, compact separators, UTF-8, and rejects non-finite numbers. Optional absent files have a null hash, not an empty-file hash.

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
6. Accept only a normal, nonzero red exit and green exit zero. Timeouts, signal termination, command-not-found/launch failures, and restoration failures never count as red. The default timeout is 1,800 seconds per phase. A timeout kills and reaps the child process group before restoration on supported POSIX systems.

Each attempt atomically replaces old proof with `state: "running"` and `ok: false` before planning or changing source. A completed run atomically publishes `state: "complete"` only after both phases and cleanup checks. The gate rejects incomplete attempts and leftover restoration plans, including when a test waiver is configured. Before mutation, paths absent from `HEAD` must also be absent locally, including ignored files, directories, and dangling symlinks.

Both phases receive separate fresh `PYTHONPYCACHEPREFIX` directories and `PYTHONDONTWRITEBYTECODE=1`. Existing user bytecode caches are neither read by ordinary Python imports nor removed. Explicit Python flags that override the environment, and non-Python build caches, remain the command author's responsibility. All reverted paths are compared by content, file type, and mode during red, including baseline-only files invisible to `git diff HEAD`. Mutations retain a Git patch plus `redgreen-mutations.json` with before/after states and base64-encoded content before restoration. Treat these files as sensitive local evidence.

A zero red exit rejects a hollow regression test. A nonzero red exit alone does not prove the intended failure: the independent reviewer must check that the log identifies the regression, not a missing dependency or unrelated failure. Gate validation recomputes the exit-code predicate and checks log hashes; it does not trust a stored `ok` flag.

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

Run `PYTHONPATH=src python -m unittest discover -s tests -v`. Lint with offline Ruff when available, otherwise pyflakes, otherwise `python -m compileall -q src tests`. Compileall is syntax validation, not a substitute for a future full linter.

## Deliberate non-goals

- Repository discovery, contribution qualification, maintainer policy research, issue assignment, GitHub authentication, PR submission, follow-up, ledgers, lanes, and personal profiles.
- Git push, package publication, deployment, remote fetching, CI polling, telemetry, dependency installation, or automatic network calls.
- Implementing fixes, generating tests, selecting/calling models, rewriting prose, or deciding approval for a person.
- Cryptographic attestations, authenticated identities, a general shell security boundary, complete secret scanning, or proof that an arbitrary test failed for the intended reason.
- A workflow server, shared database, scheduler, GUI, or agent-memory system. The unit of work is a local JSON file plus inspectable evidence.
