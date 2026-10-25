# delivery-verifier

**`dv` verifies an agent-made code change locally before you share it: regression proof, diff hygiene, independent review, and approval tied to the exact commit.**

[简体中文](README.zh-CN.md) · [Design and configuration reference](docs/design.md)

Works with Claude Code, Codex, or any coding agent that can run a local command. Python 3.10+, Git 2.38+, Linux/macOS. No third-party runtime dependencies, model calls, accounts, or telemetry. Version 0.1.0 is in development; the project name is provisional.

## The problem

A green test suite can still leave you with an unverified change:

- An agent fixes whitespace handling, then adds `assertIsNotNone(split_words("red   green"))`. That test passes on the broken implementation too.
- A patch leaves `console.log(...)` in production code or `test.only(...)` in the suite. The relevant tests pass; the delivery is still unfinished.
- The implementer's model reviews its own change in the same context and misses the same mistake.
- You approve a commit, then the agent amends it. The old approval says nothing about the new code.

`dv` removes the production fix while retaining the regression test, runs it, restores the fix, and runs the same command again. Red must fail at assertions under a supported test runner; errors before the assertion and tests that stay green are rejected. The gate also reruns your repository checks, inspects the diff, and validates review and approval against the current inputs.

## Try it in 60 seconds

From a source checkout, run:

```sh
python examples/demo/run.py
```

Use `python3` if that is your Python 3.10+ executable. No installation or download is needed. The script creates a throwaway Git repository inside the checkout's ignored `.dv/` directory and removes it afterward. It leaves your project files and Git configuration alone.

The demo changes `text.split(" ")` to `text.split()`, first with the hollow test above, then with an assertion on the returned words:

```python
self.assertEqual(split_words("red   green"), ["red", "green"])
```

You will see:

```text
redgreen: FAIL (exit 1)
  red=0, green=0
Hollow test rejected: it passes even with the fix removed.

redgreen: PASS (exit 0)
  red=1, green=0
Meaningful regression proved: red=1, green=0.
...
gate: PASS (exit 0)
...
gate: FAIL (exit 1)
  [FAIL] V5 human approval: ...
```

The readiness gate uses an explicitly **synthetic review fixture** so the demo runs offline. Real work needs a separate reviewer. The final delivery gate deliberately fails because the demo never fabricates human approval.

## Install from a local checkout

```sh
pipx install .
# Or:
uv tool install .

dv --help
```

These commands install the local source, not a published package. Installation needs setuptools at build time; an offline install needs locally available build tooling. Once installed, `dv` uses Python's standard library and your local Git executable. The demo works directly from source without either installer.

The source distribution includes the demo, complete tests, and referenced documentation. The wheel also installs these resources under `<environment>/share/delivery-verifier`, retaining the source layout. From that directory, run `python examples/demo/run.py` or `PYTHONPATH=src python -m unittest discover -s tests -v` in a writable environment; use the environment's Python. These resources are local examples, not installed top-level test packages.

Build artifacts with `uv build`. The build backend normalizes sdist ownership to `0/root` and uses `SOURCE_DATE_EPOCH` for archive timestamps (default: 2000-01-01 UTC). See [reproducible builds](CONTRIBUTING.md#reproducible-builds) for release checks.

## The verification ladder

| Level | Evidence | What `dv` checks |
| --- | --- | --- |
| V0 claim | Work-item summary and optional delivery text | Records what changed; a claim is not a passing check. |
| V1 mechanical checks | Diff hygiene, configured static checks, full test suite | Recomputes hygiene and reruns every configured command. |
| V2 regression proof | Focused test fails without the fix and passes with it | Records both runs and logs; requires assertion-only red failures, rejects runner errors, all-green tests, timeouts, and launch failures. |
| V3 runtime | Optional local demo, screenshot, or runtime log | Binds nonempty files to review. Runtime behavior is a reviewer attestation, not an automatic replay. |
| V4 independent review | Report for the current commit and inputs | Requires a passing report and a different model identifier. Small changes may use an explicit same-model fresh-context exception, shown as a warning. |
| V5 human approval | A person's decision on the exact reviewed change | Requires matching commit, configuration, review, description, and runtime evidence. |

`dv gate` checks readiness: V1, V2, and V4, plus supplied V3 attachments. `dv gate --require-approval` also checks V5. Warnings remain visible; a reasoned test waiver is reported as skipped, never as reproduced proof.

A new commit or amendment invalidates prior evidence. Evidence also binds a SHA-256 digest of the installed checker's Python sources, including its version and bundled parser. Updating or editing the checker invalidates red/green proof, review, and approval; records without this binding must be regenerated. Changes to the merge base, item settings, effective configuration, delivery text, or runtime files also require fresh evidence. Changing the review invalidates approval. Cached `gate.json` is never an authorization input.

## Use it on your change

Start in a repository with an existing commit and a local base branch, such as `main`. Work on a separate branch. Install dependencies yourself and declare the repository's actual checks in `.dv.toml`:

```toml
[dv]
static_commands = [["python", "-m", "compileall", "-q", "src"]]
test_commands = [["python", "-m", "unittest", "discover", "-s", "tests"]]
```

This is a Python example: use your project's lint, format, typecheck, and full-suite commands. `compileall` checks syntax only. Commands are argument arrays, run without an implicit shell. Static and full-suite commands run from the repository root. You can put the same settings under `[tool.dv]` in `pyproject.toml`; `.dv.toml` takes precedence.

Initialize an item:

```sh
dv init --implementer model-a --base main
```

`dv init` creates `.dv/item.json` and locally excludes `.dv/` from Git. Fill in the summary and focused regression recipe, for example:

```json
{
  "schema_version": 1,
  "summary": "Split repeated spaces without empty words",
  "base_ref": "main",
  "kind": "small",
  "implementer": "model-a",
  "status": "preparing",
  "test_command": ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_words.py"],
  "test_cwd": ".",
  "test_files": ["tests/test_words.py"],
  "runtime_files": [],
  "exceptions": {}
}
```

### Focused runner contract

`test_runner` defaults to `auto`, detecting direct `python -m unittest`, `python -m pytest` / `pytest`, and `node --test` commands. Both phases use the same recipe. For direct Python commands, `dv` adds its bundled phase probe: a unittest launcher or a pytest plugin, using the selected interpreter and installed runner. The probe adds a `DV-RUNNER-PHASES` record to the log without new runtime dependencies. Red must exit 1 and produce a complete assertion-only report:

| Runner | Required red output |
| --- | --- |
| Python unittest | `FAILED (failures=N)` with positive failures, no errors, and `AssertionError` in every failure traceback. The phase probe must establish test-body execution for every failure; setup, teardown, `addCleanup` and `addAsyncCleanup` failures are rejected. |
| pytest | Default/long traceback report (`--tb=long`), positive failed count, every failure ending in `AssertionError`, and test-body phase evidence. Fixture setup/finalizer failures and unittest lifecycle failures are rejected, even when pytest labels them `FAILED`. Short/line/hidden traceback modes are unsupported. |
| Node.js test runner | `node --test --test-reporter=tap`: positive `# fail`, zero cancelled tests, each underlying failure reporting `failureType: 'testCodeFailure'` and `code: 'ERR_ASSERTION'`. Propagated `subtestsFailed` parents require failed children; parent tests count toward `# fail`, while suite containers do not. |

Wrapper commands must declare `test_runner`. Python wrappers also need to run the bundled probe (`python -m dv._runner_probe` for unittest, `python -m pytest -p dv._runner_probe` for pytest), with `dv` importable in that interpreter. A declared runner alone supplies no phase evidence. Missing, incomplete or ambiguous phase records fail closed. Static and full-suite commands are not instrumented.

Unsupported reporters, including Jest/Vitest, bare scripts, and suppressed tracebacks fail closed. Static and full-suite commands remain unrestricted. The `red assertion` check distinguishes assertion proof from import, runtime, collection, or fixture errors, even if green passes. The gate rechecks the saved log against the same contract. A reviewer still verifies that the failing assertion tests the intended regression; printed output is trusted local evidence, not authenticated proof.

Paths are relative to the repository root. An optional `description_file` points to ignored local delivery text, such as `.dv/description.txt`; `runtime_files` references ignored evidence files. Use a separate ignored directory for each additional item.

Commit the code, tests, and check configuration before verification. `dv` requires a clean committed worktree, including no nonignored untracked files; it never stashes your work or commits for you.

```sh
dv redgreen .dv/item.json
dv hygiene .dv/item.json
```

Have a separate reviewer inspect the diff and both red/green logs, especially the reason the red run failed. Save their report, then record it against the full commit ID they inspected:

```sh
dv review record .dv/item.json --reviewer model-b \
  --head "$(git rev-parse HEAD)" --verdict pass --file .dv/reviewer-notes.txt
dv gate .dv/item.json
```

`dv` records a supplied review; it never calls a reviewer. Model identifiers and fresh-context isolation are self-reported, not authenticated. For small items, `--fresh-context` permits the same model with a warning; large items require a different model identifier.

Only after the person has inspected and approved these exact inputs:

```sh
dv approve .dv/item.json --head "$(git rev-parse HEAD)" --note "Approved this exact change."
dv gate .dv/item.json --require-approval
```

The note must record a real decision. An agent running `dv approve` cannot obtain consent on the person's behalf. Neither command pushes or publishes anything.

### Commands

| Command | Purpose |
| --- | --- |
| `dv init [ITEM] --implementer MODEL [--base REF] [--kind small\|large]` | Create a preparing item; default path `.dv/item.json`, base `main`, kind `small`. Never overwrite an item. |
| `dv redgreen ITEM [--timeout SECONDS]` | Run and record focused red/green proof. Default timeout: 1,800 seconds per phase. |
| `dv hygiene ITEM` | Check the committed diff, commit messages, and optional delivery text; write findings without editing them. |
| `dv review record ITEM --reviewer MODEL --head SHA --verdict pass\|fix-needed\|drop --file REPORT [--fresh-context]` | Bind a supplied review to the exact current inputs. A recorded negative or non-independent review still fails the gate. |
| `dv gate ITEM [--require-approval]` | Recompute readiness, or delivery readiness including human approval. |
| `dv approve ITEM --head SHA --note TEXT` | Rerun readiness and record a human decision bound to the inspected inputs. |
| `dv hooks install --agent claude-code [--dry-run]` | Preview or install project-local hook entries. |
| `dv hooks run stop\|pre-tool-use` | Internal hook adapter; consumes the hook event JSON on stdin. Normally called by installed hooks. |
| `dv --help`, `dv COMMAND --help`, `dv --version` | Show usage or the installed version. |

Add `--json` to work-item commands for machine-readable output. Exit codes: `0` success (possibly with warnings), `1` failed verification, `2` invalid input, `3` operational failure, `130` interruption. Hook adapters use hook JSON decisions and normally exit zero even when denying an operation.

Evidence and logs live beside the item: `redgreen.json`, the red/green logs, `review.json`, `review.txt`, `approval.json`, `hygiene.json`, `gate.json`, and `checks/`. Keep these local; logs can contain sensitive command output.

### Hygiene defaults

Checks cover new source comments without a justification, generated-authorship footers, debugging statements, focused tests, assertion-free or existence-only tests, unexplained lockfile changes, unfinished markers, junk paths, and whitespace errors. Source size warns above 200 changed lines and fails above 400 (`small`) or 900 (`large`). Test size warns above 1,200 lines.

Counts separate source, tests, documentation, generated files, lockfiles, and snapshots. Recognized trailing Rust inline tests count as tests. Red/green rejects every changed Rust production file containing inline tests in either the merge base or `HEAD`, even when those tests are unchanged. Moving only the new regression test to a separate file is insufficient. Supported proof requires source-only files in both versions, so any test separation must already be part of the chosen baseline.

These are review heuristics, not proof of AI origin or a complete language parser. Reasoned exceptions (`no_test_reason`, `comment_justification`, `lockfile_reason`) remain visible. For unrecognized assertion frameworks, `weak_test_justification` changes weak-test findings to visible warnings; it does not waive red/green proof. Exact-line `allowed_prose_lines` exemptions support legitimate examples and required attribution; for comments, match the complete original source line. `allowed_code_lines` visibly exempts exact source lines from debug-output and unfinished-comment checks. Strings and comments are excluded from debug scanning; unfinished markers are checked in comments only. Follow the destination project's disclosure policy. See [the reference](docs/design.md#hygiene) for rule details and [configuration](docs/design.md#configuration) for overrides.

## Optional Claude Code hooks

Use Claude Code **2.1.310 or newer** for the supported fail-closed hook integration. Install `dv`, initialize the configured item, and preview the change from your project root:

```sh
dv hooks install --agent claude-code --dry-run
dv hooks install --agent claude-code
```

The installer merges its own entries into project-local `.claude/settings.json`, preserving unrelated settings. Installation and preview output contain only its own hook entries, target path, and status, never existing settings or environment values. It records absolute executable/project paths. PreToolUse entries use `onFailure: "block"`; abnormal exits and outer timeouts block delivery. The outer timeout covers all configured commands/items plus 120 seconds, with an adapter deadline 30 seconds earlier for cleanup and a deny response. Reinstall after changing the command count, item count, or command timeout. After relocating the checkout or installation, remove the old `dv hooks run` entries before reinstalling. Commit intentional changes to shared settings before verification, or keep machine-specific settings Git-ignored. Restart or reload the Claude Code session to use the changed hook settings.

- **Stop** reruns gates for items marked `ready` or `approved`. Failed claims block stopping; `preparing` or explicitly `blocked` items can stop. After three consecutive blocks in one stop sequence, it allows stopping with the unresolved failures still recorded. That escape does not pass the gate.
- **PreToolUse (Bash)** checks recognized direct `git push` attempts against the delivery gate and the approved `HEAD`. Use a standalone, explicit `git push REMOTE HEAD:DESTINATION`; unsupported, force, or compound forms are denied. Recognized direct GitHub writes are denied too. The hook itself never performs those operations.

Multiple items can be configured under `[dv.hooks]` in `.dv.toml`:

```toml
[dv.hooks]
items = [".dv/item.json"]
max_stop_blocks = 3
allow_commands = []
```

`allow_commands` contains exact-command exemptions: each exemption skips verification for that command and is reported as such. Hooks are a convenience barrier, **not a shell security boundary**. They do not cover arbitrary scripts, aliases, subprocesses, package publishing, or other agents' tools. They never change global settings or permissions. Other agents can run `dv gate ITEM --require-approval` explicitly. To remove the integration, remove only the installed `dv hooks run` entries from project settings.

## What it does not do

- Write fixes or tests, call models, judge a review's quality, authenticate reviewers, or decide approval for you.
- Discover repositories, research contribution policies, manage issues or PRs, fetch remotes, push, publish, deploy, poll CI, or maintain a contribution ledger.
- Sandbox configured commands, scan all secrets, cryptographically sign evidence, or guarantee that a test failed for the intended reason.

Internal Git commands disable lazy fetching and all transport protocols. Missing objects fail locally; prepare them yourself before retrying. Partial clones require Git with `--no-lazy-fetch` support, otherwise use a complete local clone.

Configured commands are trusted local programs with your permissions and environment. They can access the network even though `dv` itself does not. Inspect them first. Red/green temporarily replaces changed production files in place and attempts restoration even on failure. It requires POSIX process control; native Windows is not supported for this workflow. Do not concurrently edit the worktree. Paths absent from `HEAD` but present locally, including ignored files and symlinks, block restoration before any source changes. Each attempt invalidates old proof immediately; the gate rejects incomplete attempts and remaining `restoration.json` plans. Red and green use separate fresh Python bytecode-cache locations without deleting user caches. Commands that override Python's cache environment or reuse other build caches need their own clean-build recipe. If interruption leaves `restoration.json`, preserve it and inspect the affected paths before retrying; uncatchable termination can prevent cleanup. See [SECURITY.md](SECURITY.md).

## Development and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests, lint, and contribution scope, and [CHANGELOG.md](CHANGELOG.md) for changes.

Apache-2.0, copyright clean99. See [LICENSE](LICENSE). The bundled Python 3.10 TOML compatibility parser retains its [MIT license](src/dv/_vendor/tomllib/LICENSE) and [notice](src/dv/_vendor/NOTICE).
