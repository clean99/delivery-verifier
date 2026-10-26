# First usable version

Status: implemented in development source, not released. Implementation: `cf4dbf82bc90dd96919435b0cf8f6cf529b81213` (`0.1.0.dev0`), plus the handbook and distribution-resource changes accompanying this document. Frozen baseline: `aeec479965619bb5325cbccfcd7e94fb1e962ce8`. Acceptance IDs A01–A21 remain executable in `tests/test_first_usable_spec.py` and `tests/test_first_usable_delivery_spec.py`; their assertions are unchanged and expected-failure markers have been removed. A21 and full release validation remain blocked; see the execution record below.

## Decision

Add `dv prove`: one supplied test command, committed source snapshots, local red/green evidence. No item creation, JSON editing, implementer label, review, approval, static check, or full-suite configuration is required on this path. Keep the existing `init`, `redgreen`, `gate`, review and approval interfaces unchanged; do not weaken an existing delivery gate or hook by silently substituting proof-only success.

The legacy `redgreen` command still requires an item and temporarily replaces production files in the caller's checkout; `gate.evaluate` still requires hygiene, configured checks and review. The separate `prove` and `snapshots` modules reuse assertion classification, the Python phase probe, `process.run` process-group cleanup, `checker_digest`, and content-bound review validation. They do not wrap the in-place executor around the user's files.

Success means the selected test distinguishes the selected implementations. It does not mean the whole project was tested, the fix is correct, or a person approved delivery.

## CLI and proof contract

```sh
dv prove --test "python -m unittest discover -s tests -p test_words.py -v"
dv prove --test "python -m pytest --tb=long tests/test_words.py" --base main --head fix
dv prove --test "node --test --test-reporter=tap tests/words.test.cjs" --hygiene
```

| Input | Contract |
| --- | --- |
| `--test COMMAND` | Required, nonblank command string. Split once with POSIX `shlex.split`, execute the resulting argv without a shell. No expansion, pipelines, redirection, implicit setup or dependency installation. A test ID belongs in its runner command; bare-ID inference is deferred. |
| `--base REF`, `--head REF` | Defaults `main`, `HEAD`. Resolve local full commit IDs before execution. As in the existing CLI, red uses the unique merge base of base and head, not the possibly divergent base tip. Record both the requested base tip and effective `base_sha`; green uses exactly `head_sha`. Missing refs, missing objects or ambiguous merge bases fail locally, never fetch. |
| `--test-file PATH` | Repeatable supplement to existing test-path classification for a custom test/helper. Paths are repository-relative regular tracked files in the selected head. Apply the existing pure-test check outside conventional test paths; refuse a mixed production/test file, path escape or symlink. No need to list conventionally named tests. |
| `--timeout SECONDS` | Positive integer, default 1,800 per phase. Timeout, launch failure and abnormal termination are operational failures, not evidence of a failing assertion. |
| `--hygiene` | Run existing diff/style heuristics on the selected committed change. Report findings separately; fail the requested operation if hygiene fails. Do not run configured static/full-suite commands. |
| `--require-review ITEM` | Validate an existing independently supplied review, including its report hash, checker and context bindings. Its repository, head, effective base and focused recipe must match the proof. A missing, stale, negative or mismatched review fails. This consumes the existing item workflow; it neither creates a report nor calls a reviewer. |
| `--json` | One JSON object on stdout; child output only in local logs. Preserve existing exit-code meanings: 0 success, 1 failed check, 2 invalid input, 3 operational failure, 130 interruption. |

The default path must ignore unrelated `.dv/item.json` files and must not require a clean caller checkout. It proves **committed snapshots only**: staged, unstaged, untracked and ignored caller files are excluded from execution and left alone. Read policy from the selected committed head, not from dirty local configuration. Help and results state this boundary.

Red starts as a detached worktree at the effective base. Overlay the head's test-classified paths and explicit test files, remove base-only test paths, and preserve executable modes. Green starts as a separate detached worktree at head with the same test inputs. Include test support files under conventional test/fixture directories. Other paths remain native to each revision; root-level helper/config changes may therefore need a compatible baseline or an explicit pure-test helper. Record the retained file paths and hashes. Reject changes with no production difference or no usable retained test; never copy a mixed production fix as a test to manufacture proof.

For both phases, use the same argv and the worktree root as cwd. Unittest, pytest and Node TAP retain their current red assertion rules. Extend runner parsing to validate **green execution**, not just exit zero: require a complete report, at least one genuinely passed test body, no failures/errors/cancellation, and positive executed count. Skips and expected failures are not passes. Record `tests: {executed, passed, failed, skipped}` for each phase. Missing/ambiguous counts fail closed. Unittest lifecycle assertions remain excluded; pytest's existing call-phase boundary, including dynamic fixtures, remains explicit.

Counts do not prove that every intended test ran. Branch-dependent selection can omit one test while another passes. Do not advertise complete suite coverage or identity-by-identity equivalence in this version. A reader must inspect the recipe and logs; the handbook demonstrates both a detected omission and this remaining limit.

## Isolation and retained evidence

1. Allocate a unique attempt directory under the caller's Git administrative path, `git rev-parse --git-path dv/proofs/<attempt>`. Do not modify `.gitignore`, `info/exclude`, Git configuration, caller files, caller index, refs, or existing item directories. Per-attempt files do not appear as worktree changes.
2. Atomically write `proof.json` with `ok: false` and `state: running` before creating either execution worktree. Store the exact owned paths in a recovery record before each creation. A new attempt never reuses a previous PASS.
3. Create detached temporary worktrees using existing local Git objects and disabled lazy fetching/transports. Do not execute checkout hooks or smudge filters; fail closed for unsupported materialization such as submodules/LFS rather than installing or fetching anything. Preserve unrelated worktrees and registrations.
4. Give red and green separate fresh Python cache directories. Never link the caller's writable environment, `node_modules`, caches or ignored files into either snapshot. Resolve executables using the supplied environment/PATH; users prepare dependencies themselves. Editable installs pointing back to the caller can defeat source isolation and must be corrected by the user; no hermetic-environment claim.
5. Run phases and persist combined stdout/stderr with hashes. Capture tracked/index mutations before discarding an owned snapshot and fail the proof if a test changed its tracked inputs. Test-created untracked output stays inside the owned temporary tree and is removed with it. Logs survive cleanup.
6. On normal exit, verification failure, creation failure, timeout, SIGINT or SIGTERM, reap the child process group and remove only this attempt's worktrees, registrations and caches. A cleanup failure produces exit 3, `ok: false`, and the exact remaining paths. Do not perform a repository-wide prune or touch another attempt.
7. Publish success atomically only after both runs, requested optional checks, scope rechecks and cleanup succeed. SIGKILL, machine shutdown and escaped descendants cannot be guaranteed clean; preserve a non-success recovery record and print targeted inspection instructions on the next run. Never delete an ambiguous leftover automatically.

**Isolation is not a security sandbox.** Worktrees share Git objects and administration. Trusted commands still have the user's environment, filesystem and network privileges; a command can deliberately reach outside its cwd. No automatic dependency installs, model calls, uploads or telemetry. Logs may contain secrets and remain local.

`proof.json` and JSON stdout share the completed result. Required fields reuse existing conventions: `schema_version`, `command: prove`, `ok`, `state`, `head_sha`, `base_sha`, `checker_sha256`, `context_sha256`, `argv`, `runner`, `retained_tests`, `red`, `green`, `checks`, `exit_code`; add `evidence_dir`, `isolation: git-worktree`, `security_sandbox: false`, and `optional_checks`. Optional-check values are `not_requested`, `pass` or `fail`, never a fabricated PASS. Logs are `red.log` and `green.log`, with the existing phase `log_sha256`, status, timeout and exit fields. Allow a null phase when it could not start.

The context digest binds immutable resolved refs, retained test bytes/modes, argv, committed policy, timeout, requested optional checks, supplied review inputs and checker sources. A failed/interrupted attempt cannot borrow old evidence. Existing gate evidence remains a separate format and is still reparsed/revalidated; new proof-only records are not human approvals or automatic gate inputs.

## Installation and release checks

Keep the standard-library runtime, Python 3.10–3.13 and Linux/macOS with Git 2.38+. Keep the current setuptools backend and reproducible sdist normalization. Ship the handbook, its manifest, replay script, fixtures and tests in sdist and wheel resources under `share/delivery-verifier`; update both `MANIFEST.in` and setuptools data-file declarations during implementation.

Build wheel and sdist from the candidate commit. A21 installs each into separate clean environments through `pip` and `pipx`, also exercises a Git VCS install, runs `dv --version` and a real proof from a cwd outside the source checkout with `PYTHONPATH` removed, and checks bundled resources. Development prerequisites are explicit: setuptools 80.9.0, pipx 1.7.1, pytest 8.4.2 and Ruff 0.17.1. Run the acceptance and existing suite on all four Python minor versions; missing runner prerequisites or skipped install checks do not satisfy release acceptance.

Document immutable install forms once release assets exist:

```sh
pipx install https://github.com/clean99/delivery-verifier/releases/download/<tag>/<wheel>
python -m pip install https://github.com/clean99/delivery-verifier/releases/download/<tag>/<sdist>
pipx install 'delivery-verifier @ git+https://github.com/clean99/delivery-verifier.git@<full-commit>'
```

These are templates, not claims of an available release. Local acceptance uses file artifacts and `git+file`; a release rehearsal sets `DV_SPEC_WHEEL_URL`, `DV_SPEC_SDIST_URL` and `DV_SPEC_SOURCE_URL` to the immutable public URLs and repeats A21 read-only. Record results as passed/attempted cases with exact commit and tool versions. Local install success alone does not establish public asset availability. Prepare artifacts only; publishing, tagging and PyPI credentials are separate owner actions.

## Companion handbook

Create `docs/handbook/green-tests.md`, titled **What a green test run still misses**, with `docs/handbook/cases.json` and `examples/handbook/run.py`. Use repository-owned synthetic fixtures under Apache-2.0, not copied private incidents or unlicensed historical patches. The reference replay pins Python 3.12.13, pytest 8.4.2 and Node 20.20.2, matching existing runner provenance, plus the exact dv implementation commit. This pin describes the reproduction environment, not an expansion of support beyond the matrix.

| Case ID | Native-script observation | Requested dv observation | Limit to demonstrate |
| --- | --- | --- | --- |
| `hollow-test` | An existence assertion passes with and without a numeric fix; head-only test command exits 0. | Exit 1: red passed, so the test did not distinguish the change. | A narrow distinguishing assertion can still miss other inputs or the real requirement. |
| `wrong-failure` | A baseline `RuntimeError` followed by green exit 0 fools a script that checks only red-nonzero/green-zero. | Exit 1: red was not an assertion-only test-body failure. | An unrelated assertion in the accepted runner phase can still look valid; dv does not understand intent. |
| `not-run` | A conditional unittest bootstrap runs red but exits 0 before invoking green tests, printing that execution was skipped. | Exit 1: no complete green execution evidence or passed test body. Also cover all-skipped green in A05. | An omitted test alongside a different passing test is not necessarily detected. Full-suite coverage is not claimed. |
| `stale-review` | Tests still pass after an amended commit while a prior review file remains. A head-only script exits 0. | With `--require-review ITEM`, exit 1 for stale scope. Default proof alone may pass and must say review was not requested. | A current report can be shallow, forged or falsely attributed; identities are not authenticated. |

Ordinary empty-suite discovery already exits 5 in the pinned Python 3.12.13 interpreter; credit that native protection rather than presenting it as a dv-only detection. The `not-run` fixture deliberately exercises an early successful process exit, not ordinary empty discovery.

For each case, include fixture source, exact setup/commands, expected relevant output, the native script's exit code, an improved native-script alternative, dv's actual output, and its remaining blind spot. A native script can implement these same checks; dv packages consistent classification, cleanup and evidence rather than inventing the method.

Replay interface:

```sh
python examples/handbook/run.py --case hollow-test --backend native --output ./case-native --json
python examples/handbook/run.py --case hollow-test --backend dv --output ./case-dv --json
```

Repeat for each case ID. The driver exits 0 only when the demonstration completes; its JSON records the demonstrated command's `exit_code` (native 0, dv 1), case, backend, raw-log relative path, observed toolchain and case observations. Record observed results, never copy expected values into result fields. Preserve raw logs; normalize temporary paths only for checked-in excerpts. A20 reruns all eight case/backend combinations from outside the checkout and checks the observations.

### Comparison sources

These are first-party documentation readings, **not comparative runs**. Carry the pinned link and `documentation-only` label into every applicable case. A reproduced competitor result needs its own pinned command, environment, raw evidence and denominator; no inferred PASS/FAIL cells.

- [trustbut 0.7.0, commit `2486068`](https://github.com/isAlmogK/trustbut/blob/248606877b49b8975aeea4c01c70933058105c2b/README.md) already documents clean-checkout execution, testing new/changed tests against prior code, and receipts with test/skipped counts. It directly overlaps the hollow-test and execution-receipt use cases and supports more reporters than dv. Its published measurements are its own study, not dv benchmarks. Do not imply it lacks counterfactual proof.
- [Trail of Bits Regression Hunt, commit `196a907`](https://github.com/trailofbits/skills/blob/196a9073ab10d35bacf111f945e02c00b2c6883a/plugins/regression-hunt/README.md) documents preparing snapshots, deploying both states, independent PoC replay, adjudication and sealed evidence. It is an agent-coordinated investigation workflow with host/execution prerequisites, not merely a style checker or this focused no-model CLI.
- [Bugsweep, commit `5410ec6`](https://github.com/shanemhamilton/bugsweep/blob/5410ec6da58de483517017c808746781c1048d6c/README.md) documents expected assertion failure before a fix, the unchanged test passing afterward, suite comparison by native test identity, native review receipts and external execution policy. It also distinguishes pure checks from real host/isolation release evidence. Do not present those documented goals as independently measured guarantees, or claim dv pioneered isolated red/green proof.

## Frozen acceptance and execution

| IDs | Required observable result |
| --- | --- |
| A01–A05 | No-item proof with bound logs; reject hollow tests, wrong-reason failures, zero executed green and all-skipped green. |
| A06–A08 | Separate source snapshots/caches; preserve dirty caller bytes, index, config, excludes and branch; explicit head and merge-base semantics; no implicit shell. |
| A09–A11 | Timeout, missing executable and catchable termination never pass; owned worktrees cleaned, unrelated worktree preserved, interrupted attempt inspectable. |
| A12–A14 | Hygiene/review are opt-in; no full-suite execution on proof path; stale review rejected when requested; attempts and existing item state stay separate. |
| A15–A18 | Honest help/unsupported-runner diagnostics, explicit custom test retention, existing pytest and Node TAP support. |
| A19–A20 | Pinned, licensed handbook cases and fair source-linked comparisons; all eight native/dv replays match observable results. |
| A21 | Both artifact formats and Git source install through both installers; installed CLI proves a real change and ships replay resources. |

Run from the repository root with already prepared tooling:

```sh
# Normal suite: acceptance cases are ordinary tests, not expected failures.
HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 PYTHONPATH=src python -m unittest discover -s tests -v
# Focused oracle: DV_SPEC_STRICT is retained for old command-line recipes; no exemptions remain.
HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 DV_SPEC_STRICT=1 PYTHONPATH=src \
  python -m unittest discover -s tests -p 'test_first_usable*spec.py' -v
# Release oracle: includes clean build/install tests; requires disk and network for installers.
HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 DV_SPEC_STRICT=1 DV_SPEC_INSTALL=1 PYTHONPATH=src \
  python -m unittest discover -s tests -p 'test_first_usable*spec.py' -v
HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 ruff check .
```

All `@pending` markers have been removed without changing their assertions. A21 still requires `DV_SPEC_INSTALL=1`; a skipped installation check is not acceptance. CI now installs pipx 1.7.1 and enables A21 on Python 3.10–3.13. Build completion requires 21/21 accepted IDs, the existing regression suite, Ruff, the demo and the four-version installation matrix. Added fault-injection coverage exercises partial creation, removal failure, symlink-replaced cleanup targets, raw materialization collisions, tracked/index mutation, committed policy, test support/modes and filter/hook suppression. Existing process/runner regression cases remain required.

## Execution record: 2026-10-26

Source implementation `cf4dbf82bc90dd96919435b0cf8f6cf529b81213`; handbook/resources are the accompanying change. **A01–A20 passed on all four supported Python minors. A21 was skipped, so acceptance is 20/21, not 21/21.** No expected failures remain. The full existing unit suite, wheel/sdist build and clean installation matrix were not run because the required 5 GiB disk gate failed. No installed-artifact or release-readiness claim is made.

The focused command below covers acceptance, the new execution/lifecycle faults and the existing Python/Node reporter regressions. It is not the full repository suite. Commands use checkout-relative paths to avoid checking machine-specific paths into this public repository; exact local invocations and outputs are retained under the ignored `.dv/spec/` directory.

```sh
HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python -m unittest test_first_usable_spec test_first_usable_delivery_spec \
  test_execution test_prove_faults test_runner_phases test_node_runner -v
```

| Interpreter | Passed / discovered | Skipped | Acceptance |
| --- | ---: | --- | --- |
| Python 3.10.20 | 48/49 | A21 | 20/21 |
| Python 3.11.15 | 48/49 | A21 | 20/21 |
| Python 3.12.13 | 48/49 | A21 | 20/21 |
| Python 3.13.14 | 48/49 | A21 | 20/21 |

The Python 3.12 terminal summary was:

```text
Ran 49 tests in 77.768s

OK (skipped=1)
```

Prepared tooling: pytest 8.4.2, setuptools 80.9.0, Node 20.20.2, Git 2.50.1 (Apple Git-155), Ruff 0.17.1. No dependencies were installed for these runs. CI's only code-quality command is `ruff check .`; no separate format/type command is configured. Repository lint and whitespace checks completed:

```sh
HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 ruff check .
git diff --check
```

```text
All checks passed!
```

The [handbook replay loop](../handbook/green-tests.md#reproduction-environment) completed 8/8 native/dv combinations with the pinned Python 3.12 toolchain. Its source-only results and the demonstrated partial-selection blind spot are recorded in the handbook. These runs do not satisfy the installed-wheel handbook requirement.

The attempted full-suite/build/install gate was:

```sh
available=$(df -k /System/Volumes/Data | awk 'NR==2{print $4}')
if test "$available" -ge 5242880; then
  HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 \
    DV_SPEC_INSTALL=1 PYTHONPATH=src:tests python -m unittest discover -s tests -v
else
  printf 'BLOCKED: full unit suite and build/install matrix require 5242880 KiB; available=%s KiB. No build or install was started.\\n' "$available"
fi
```

```text
BLOCKED: full unit suite and build/install matrix require 5242880 KiB; available=3226124 KiB. No build or install was started.
```

After sufficient disk space is available, rerun the complete suite and A21 on all four interpreters, build from the final candidate commit, and execute a bundled handbook case using a fresh artifact-installed CLI with `PYTHONPATH` removed. `DV_SPEC_INSTALL=1` includes six installer/source combinations per interpreter: pip and pipx, each with wheel, sdist and Git. Public immutable-URL rehearsal remains separate. Nothing was pushed, published or released.

## Out of scope

- Jest/Vitest, arbitrary reporters, automatic runner/test-ID discovery, native Windows proof, mixed Rust rewriting, submodule/LFS hydration.
- Dirty-worktree snapshots, automatic dependency setup, shared writable dependency links, hermetic containers, security sandboxing or remote execution.
- Full-suite completeness, per-test identity equivalence, mutation generation, semantic correctness of an assertion, review quality or authenticated approval.
- Changing legacy gate/hook policy, installing hooks, model orchestration, a hosted service or a separate awesome-list repository.
- Pushes, tags, releases, PyPI publication, GitHub writes, outreach or adoption claims. No claim of superiority without comparative execution evidence.
