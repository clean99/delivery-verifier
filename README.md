# delivery-verifier

**`dv prove` checks whether your regression test fails without the fix and actually passes with it. Optional delivery gates bind review and approval records to the exact commit.**

For people shipping changes written by coding agents. Runs locally with any agent that can execute a command.

[简体中文](README.zh-CN.md) · [Design and configuration reference](docs/design.md)

![Offline demo: a hollow test is rejected, a real regression is proved, and delivery stays blocked without human approval.](docs/assets/demo.gif)

*Actual CLI output from synthetic fixtures, excerpted and paced for reading. Review in the demo is a synthetic fixture; human approval is left absent.*

## Quick start

Python 3.10–3.13, Git 2.38+, Linux/macOS. Install this development checkout:

```sh
git clone https://github.com/clean99/delivery-verifier.git
cd delivery-verifier
pipx install .
```

In **your project's repository**, with the fix and regression test committed on a branch based on local `main`, run:

```sh
dv prove --test "python -m unittest discover -s tests -p test_words.py -v"
```

No item file or JSON editing is needed. Red runs at the merge base of `main` and `HEAD` with head's tests retained; green runs at `HEAD`, in a separate detached worktree. The command requires an assertion-only red failure and a complete green report with at least one passed test body. It rejects a hollow assertion, a runtime/import failure, zero executed green, and all-skipped green.

Use `--base REF --head REF` to select committed revisions, `--test-file checks/regression.py` for a custom pure test file, and `--json` for machine-readable output. Other supported recipes:

```sh
dv prove --test "python -m pytest --tb=long tests/test_words.py"
dv prove --test "node --test --test-reporter=tap tests/words.test.cjs"
```

Hygiene and review are opt-in: add `--hygiene` or `--require-review .dv/item.json`. The latter consumes an existing matching review from the item workflow below. Default proof does not run configured full-suite checks or approve delivery; the legacy `gate` and hooks retain their own requirements.

**Committed snapshots only.** Staged, unstaged, untracked and ignored caller files are excluded and left untouched. Policy comes from committed head. Each attempt records `proof.json`, `red.log` and `green.log` under the Git administrative `dv/proofs/` directory and removes its execution worktrees and caches. The CLI prints the exact evidence directory. Failure and interruption never reuse a previous PASS; a leftover recovery record needs targeted inspection, not a global worktree prune.

**Current limits:** 0.1.0 is in development; release/install acceptance is still pending. unittest, pytest and Node TAP are supported; Jest/Vitest, native Windows, submodule/LFS hydration, and automatic dependency installation are outside this version. Commands are POSIX-split argv, with no implicit shell expansion or pipelines. Prepare dependencies yourself; editable installs pointing to another checkout can defeat source isolation. Worktrees are **not a security sandbox**: commands retain your filesystem/network privileges. Logs remain local and can contain secrets. A passed proof establishes neither complete test coverage nor assertion correctness; reviewer identities are self-reported. See [the reproducible handbook](docs/handbook/green-tests.md), [implementation and validation status](docs/design/first-usable-version.md), and [security limits](SECURITY.md).

For an offline source-only example, run `python3 examples/demo/run.py` from this checkout. It demonstrates the separate item-based delivery gate with synthetic review evidence.

## What the demo checks

The fix changes `text.split(" ")` to `text.split()`. A hollow test only checks that the result exists:

```python
self.assertIsNotNone(split_words("red   green"))
```

That passes on both implementations, so `dv redgreen` rejects it. Replace it with:

```python
self.assertEqual(split_words("red   green"), ["red", "green"])
```

Now the test fails at an assertion with the fix removed and passes once the fix is restored:

```text
redgreen: FAIL (exit 1)
  red=0, green=0
Hollow test rejected: it passes even with the fix removed.

redgreen: PASS (exit 0)
  red=1, green=0
Meaningful regression proved: red=1, green=0.
```

The full demo also runs diff hygiene and the readiness gate using a **synthetic review fixture**. Its final delivery gate deliberately fails for missing human approval. Real work needs a separate reviewer and a real person's decision.

The gate reruns your configured repository checks, flags issues such as focused tests and debug statements, and rejects stale evidence after the commit or reviewed inputs change.

## Related tools

[mutmut](https://github.com/boxed/mutmut) (Python) and [StrykerJS](https://github.com/stryker-mutator/stryker-js) test test-suite sensitivity by generating code mutations. `dv` checks a narrower counterfactual: revert the actual production change to its Git merge base, keep the new tests, and rerun the supplied regression command. It does not generate mutants or compute a mutation score.

[pre-commit](https://github.com/pre-commit/pre-commit) manages multi-language Git hooks. You can use it to run checks, including a custom regression script. `dv` adds a prescribed red/green procedure and commit/input-bound evidence records. Keep your existing lint, test and review tools; `dv` neither replaces them nor supplies the reviewer.

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

## Optional item-based delivery workflow

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
| Python unittest | `FAILED (failures=N)` with positive failures, no errors, and `AssertionError` in every failure traceback. The phase probe must establish test-method execution for every failure; `setUp`, `tearDown`, `addCleanup` and `addAsyncCleanup` failures are rejected. |
| pytest | Default/long traceback report (`--tb=long`), positive failed count, every failure ending in `AssertionError`, and call-phase evidence. Setup/teardown phases, collection errors and unittest lifecycle failures are excluded, even when pytest labels a unittest lifecycle failure `FAILED`. Short/line/hidden traceback modes are unsupported. |
| Node.js test runner | `node --test --test-reporter=tap`: positive `# fail`, zero cancelled tests, each underlying failure reporting `failureType: 'testCodeFailure'` and `code: 'ERR_ASSERTION'`. Propagated `subtestsFailed` parents require failed children; parent tests count toward `# fail`, while suite containers do not. |

Wrapper commands must declare `test_runner`. Python wrappers also need to run the bundled probe (`python -m dv._runner_probe` for unittest, `python -m pytest -p dv._runner_probe` for pytest), with `dv` importable in that interpreter. A declared runner alone supplies no phase evidence. Missing, incomplete or ambiguous phase records fail closed. Static and full-suite commands are not instrumented.

For unittest, `dv` treats execution of the test method (including its helpers and subtests) as the test body, excluding `setUp`/`tearDown`, their async counterparts, and cleanups. For pytest test functions, the boundary is the **call phase**: everything that runs while the test function is being called, including fixtures dynamically requested with `request.getfixturevalue()` and call-phase hooks such as `pytest_runtest_call` in `conftest.py`. An assertion in either of those counts as a test-body failure even when it is outside the function's own statements. Pytest setup/teardown phases and collection errors are excluded; unittest cases run by pytest retain the stricter test-method boundary. Reviewers should read the red logs to check where the assertion came from and whether it proves the intended regression.

Unsupported reporters, including Jest/Vitest, bare scripts, and suppressed tracebacks fail closed. Static and full-suite commands remain unrestricted. The `red assertion` check distinguishes qualifying assertions from import, runtime, collection, or setup/teardown-phase errors, even if green passes. The gate rechecks the saved log against the same contract. Printed output is trusted local evidence, not authenticated proof.

Paths are relative to the repository root. An optional `description_file` points to ignored local delivery text, such as `.dv/description.txt`; `runtime_files` references ignored evidence files. Use a separate ignored directory for each additional item.

Commit the code, tests, and check configuration before item-based verification. The legacy `redgreen` and `gate` workflow requires a clean committed worktree, including no nonignored untracked files; it never stashes your work or commits for you. The separate `prove` command accepts a dirty caller checkout and executes only committed snapshots.

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
