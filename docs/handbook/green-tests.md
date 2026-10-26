# What a green test run still misses

A successful process exit is useful evidence, but it can describe a hollow assertion, a bootstrap that never ran tests, or a report for an older commit. These cases compare deliberately small native scripts with `dv prove`. A native script can implement the same checks. `dv` packages runner classification, owned-resource cleanup and content-bound local evidence; it does not establish the intended behavior or authenticate a reviewer.

## Reproduction environment

Reference implementation: `cf4dbf82bc90dd96919435b0cf8f6cf529b81213`, version `0.1.0.dev0`. Reference toolchain: Python **3.12.13**, pytest **8.4.2**, Node **20.20.2**, Git 2.38+ on Linux/macOS. The cases use unittest; the pytest and Node pins also identify the supported-reporter reproduction environment. The driver records observed versions, not assumed pins.

All fixtures in [examples/handbook/fixtures](../../examples/handbook/fixtures) are repository-owned synthetic examples under Apache-2.0. No historical/private incidents or third-party patches are used. [cases.json](cases.json) pins the inputs, expected observations and comparison sources. The historical results below use the pinned development source. For v0.1.0 installation, use the [GitHub Release or Git tag recipes](../../README.md#installation-options); the release bundles this handbook and its replay resources.

With the reference tooling already prepared, run from this checkout:

```sh
for case in hollow-test wrong-failure not-run stale-review; do
  for backend in native dv; do
    HUSKY=0 LEFTHOOK=0 UV_NO_CACHE=1 PYTHONPATH=src python examples/handbook/run.py \
      --case "$case" --backend "$backend" --output ".dv/handbook/$case-$backend" --json
  done
done
```

Each output directory must be new. The driver creates only its own repository, commits synthetic baseline/fix revisions, executes the selected backend, and leaves `result.json`, `raw.log`, phase logs and the repository for inspection. It never overwrites an earlier replay. It exits 0 when the **demonstration** completed; its JSON `exit_code` is the demonstrated native/dv command's result. A deliberate dv rejection is therefore driver exit 0 with recorded `exit_code: 1`.

The driver resolves relative `PYTHONPATH` entries against the launch directory before changing the child cwd, and works from another cwd when invoked by absolute script path. For an installed artifact, use the script under `<environment>/share/delivery-verifier/examples/handbook/run.py` with `--dv <environment>/bin/dv`; this removes `PYTHONPATH` for the child CLI. The wheel/sdist also include the fixtures, manifest and tests. Installation itself requires prepared dependencies or network access and is separate from the offline replay.

## Observed results

The original source replay recorded **8/8 case/backend combinations**. A newcomer replay on 2026-10-26 at candidate `44776dd` completed **0/8** using the exact loop above: relative `PYTHONPATH=src` stopped locating dv after the child changed directories. Replacing it with an absolute source path completed **8/8**, using Python 3.12.13 and Node 20.20.2, with pytest absent. The driver now normalizes that path; `test_exact_documented_loop_with_relative_pythonpath` executes the documented loop to guard this entry point.

The table retains the case observations, not a claim that the original command worked unchanged. Paths, commit timestamps and durations are omitted. Each replay retains its own raw logs. These are synthetic demonstrations, not an estimate of detection accuracy on real projects.

| Case | Native exit | dv exit | Red exit | Green exit | Green executed | Requested review |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `hollow-test` | 0 | 1 | 0 | 0 | 1 | not requested |
| `wrong-failure` | 0 | 1 | 1 | 0 | 1 | not requested |
| `not-run` | 0 | 1 | 1 | 0 | 0 | not requested |
| `stale-review` | 0 | 1 | 1 | 0 | 1 | fail: stale |

The native command in every fixture is `python -m unittest discover -s tests -v`. The first, third and fourth native policies trust green's exit code; `wrong-failure` trusts `red != 0 and green == 0`. The driver runs both revisions to preserve observations, even where the naive policy only consults green. The dv command is:

```sh
dv prove --test "python -m unittest discover -s tests -v" --json
# stale-review adds:
# --require-review .dv/item.json
```

### Hollow assertion

Inputs: `base_value.py` returns 21, `fixed_value.py` returns 42, and `hollow.py` uses `self.assertIsNotNone(value())`. Both revisions pass. The actual red and green logs contain `Ran 1 test` and `OK`; dv's `red assertion` check fails because red exited 0.

Improved native alternative: run the same test against the baseline and require an actual assertion failure, not just a head-only pass. For this fixture, checking the unittest result's nonempty `failures` and empty `errors` is enough to reject the hollow test. To match dv's phase boundary generally, also distinguish failures in test methods from failures in setup/teardown/cleanup.

Remaining limit: changing the assertion to `self.assertEqual(value(), 42)` distinguishes these revisions, but says nothing about other inputs or whether 42 satisfies the real requirement.

### Wrong reason for red

Inputs: `runtime_value.py` raises `RuntimeError('not a behavioral assertion')`; head returns 42, and `regression.py` checks equality. The native exit-code pair is red 1 / green 0, so its policy returns 0. The red log contains `RuntimeError: not a behavioral assertion` and `FAILED (errors=1)`. dv returns 1 because red has a runner error rather than an assertion-only test-body failure.

Improved native alternative: inspect `TestResult.errors` as well as `TestResult.failures`, retaining tracebacks and phase information. A compact fixture-specific check is:

```python
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=2).run(suite)
accepted_red = bool(result.failures) and not result.errors and not result.unexpectedSuccesses
```

This distinguishes the `RuntimeError` fixture. It is not a general lifecycle classifier: setup assertions also populate `failures` without dv's phase probe. Pytest function tests use the call phase, including dynamically requested fixtures and call hooks; see the [runner boundary](../../README.md#focused-runner-contract).

Remaining limit: an unrelated assertion in an accepted phase can still look valid. Neither this script nor dv infers intent from the assertion.

### Green never ran

Inputs: `not_run.py` runs the equality test at baseline, but its `load_tests` bootstrap raises `SystemExit(0)` at head before invoking the runner. The green log prints `Bootstrap exited before running tests`, without a completed test report. The naive policy sees exit 0. dv's `green execution` check reports `complete=False`, `executed=0`, `passed=0`, and returns 1.

Improved native alternative: catch early exits around discovery/execution, require a result object and a positive number of genuinely successful tests. A fixture-specific count can subtract failures, errors, skips, expected failures and unexpected successes from `testsRun`; require normal completion as well. Preserve a machine-readable completion marker outside the bootstrap. Ordinary empty discovery already exits 5 on the pinned Python 3.12.13 interpreter; this case deliberately exercises an early **successful** exit instead of taking credit for that native protection.

Remaining limit: aggregate counts do not prove identity-by-identity execution. This additional source replay at the same implementation commit returned `ok: true` with red `{executed: 2, passed: 1, failed: 1, skipped: 0}` and green `{executed: 1, passed: 1, failed: 0, skipped: 1}`. The regression was omitted from green while a decoy passed.

To reproduce, replace `tests/test_value.py` in the disposable `not-run` repository with the following, commit that replacement on `fix`, and rerun the same `dv prove` recipe:

```python
import unittest
from value import value

class ValueTests(unittest.TestCase):
    @unittest.skipIf(value() == 42, 'conditional omission')
    def test_value(self):
        self.assertEqual(value(), 42)

    def test_decoy(self):
        self.assertEqual(2 + 2, 4)
```

A05 separately checks rejection of **all-skipped** green. Full-suite completeness and selection equivalence require a stronger native test-ID comparison or independent review.

### Review belongs to an old commit

Inputs: `regression.py` with the numeric fix. The driver initializes an item, writes a clearly labeled synthetic review fixture, records it with `dv review record`, then amends the commit message. `review_head` and `head` in `result.json` differ. Head's test still passes, so the native policy returns 0. With `--require-review`, dv returns 1 and reports `Review has stale code, base, configuration, or attachments`.

Improved native alternative: bind the report to the full commit ID, base, command, effective policy and report bytes, and recompute those bindings at the point of use. Comparing only a report's timestamp or presence is insufficient. A full HEAD comparison detects this particular amendment; configuration and report hashes cover additional changes.

Remaining limit: current reports may be shallow, forged or falsely attributed. The fixture is not a real review or human approval. Default `dv prove` intentionally does not request review and may pass this change; its `optional_checks.review` says `not_requested`. The separate delivery gate still requires its own evidence.

## Comparison sources, not comparative executions

Every case's manifest carries the following pinned first-party documentation with the **documentation-only** label. No competitor was executed in this replay; there are no inferred pass/fail results or comparative performance claims.

- [trustbut 0.7.0, `2486068`](https://github.com/isAlmogK/trustbut/blob/248606877b49b8975aeea4c01c70933058105c2b/README.md) already documents clean-checkout counterfactual execution of new/changed tests on prior code and receipts with test/skipped counts. It directly overlaps the hollow-test and execution-receipt cases and supports more reporters. Its published measurements are its own study, not dv benchmarks.
- [Trail of Bits Regression Hunt, `196a907`](https://github.com/trailofbits/skills/blob/196a9073ab10d35bacf111f945e02c00b2c6883a/plugins/regression-hunt/README.md) documents preparing snapshots, deploying both states, independent PoC replay, adjudication and sealed evidence. It is an agent-coordinated investigation with host/execution prerequisites, rather than this focused no-model CLI.
- [Bugsweep, `5410ec6`](https://github.com/shanemhamilton/bugsweep/blob/5410ec6da58de483517017c808746781c1048d6c/README.md) documents expected assertion failure before the fix, unchanged tests afterward, suite comparison by native test identity, native review receipts and external execution policy. It distinguishes pure checks from real host/isolation release evidence. Those documented goals are not independently measured guarantees here.

Use the native runner's protections where they suffice. Choose additional tooling based on the evidence it actually captures and your execution environment, not these synthetic cases alone. Worktrees share Git administration and user privileges; they are not a security sandbox. Tests, logs and review records remain local and can contain secrets.
