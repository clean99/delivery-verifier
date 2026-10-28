# Vitest 4.1.12 proof boundary

## Scope

Support is enabled for Vitest 3.2.x and exactly 4.1.12. The 4.1.12 adapter checks published source fingerprints and instruments only phase-owned dependency copies; other 4.x versions and all 5.x remain rejected. The [existing proof contract](../design.md#red-to-green-proof) still applies.

`tests/test_vitest4_runner.py` checks real body/fixture/snapshot proofs, replays the 3.2.x regression cases, and retains rejection cases for unsupported constructs. It uses optional, separately pinned tooling and never installs dependencies. Prepare the tooling explicitly from the public registry:

```sh
npm install --prefix .dv/vitest4 --registry=https://registry.npmjs.org/ \
  --ignore-scripts --no-audit --no-fund --save-exact vitest@4.1.12 vite@7.3.7
DV_VITEST4="$PWD/.dv/vitest4/node_modules/.bin/vitest" \
  python -m unittest discover -s tests -p test_vitest4_runner.py -v
```

Absent tooling skips the real-runner tests; the 4.1.12 version is checked before they run. `DV_VITEST` remains the independent 3.2.x test executable. `tests/test_vitest4_sources.py` covers source identity and configuration hashing, and `tests/test_vitest4_matrix.py` covers optional source/wheel and dependency-layout cells.

Local version selection chooses `--update=none` for 4.1.12 and rejects `--minWorkers` before launching proof phases. The reporter rechecks the executing version and source identity. Startup/configuration failures without a valid report reject proof with the runner diagnostic and phase log path, rather than a parser exception.

## Known limitations and compatibility

A 4.1.12 test body that directly returns a `resolves` or `rejects` matcher thenable, such as `return expect(p).resolves.toBe(x)`, is rejected in both phases as `unsupported test-body thenable`. The body observer cannot safely track that matcher thenable as a native promise. Use an async test body with `await expect(p).resolves.toBe(x)` instead; `rejects` has the same workaround. The runner controls expect rejection for the return form and PASS for the await form in both forks and threads.

The resolver-function check deliberately tightens Vitest 3.2.x as well as 4.1.12. Functions in Vite resolver settings, including function and object-form alias `customResolver` callbacks, are rejected even when their source is unchanged: identical source cannot establish identical closed-over resolution targets. Use declarative aliases instead.

## Why the old body wrapper is unsafe

The retained test below observes a change from `value = 1` on base to `value = 2` on head:

```js
import { value } from '../src/value.js';
const extended = test.extend({
  x: async ({}, use) => { expect(value).toBe(2); await use(2); },
});
extended('fixture-only regression', ({ x }) => { expect(x).toBe(2); });
```

The base assertion fails before the test body runs. Merely opening the version guard and changing the snapshot flag produced a false PASS in the feasibility replay: the old wrapper recorded the fixture error as a body error and reported `fixtures: false`.

In 4.1.12, `TestFixtures` owns fixture resolution. Its `getWorkerContext()` is not the runner method observed by the 3.x probe. During collection, the stock runner stores this wrapper in `getFn(task)`:

```js
withTimeout(withCancel(withAwaitAsyncAssertions(withFixtures(handler, { context }), task), ...), ...)
```

`runTest` calls `runner.runTask(test)` or `getFn(test)()` after beforeEach, but both routes still include fixture initialization. Fixture cleanup, afterEach, returned cleanups and test-finished callbacks run later. aroundEach encloses this lifecycle; aroundAll encloses the suite.

## Insertion point

Observe the invocation of the original `handler` **inside `withFixtures`, after fixture resolution**, only for the test-handler call site in `createTaskCollector`. The five `return fn(context)` paths cover absent fixtures, empty registrations, unused fixtures, empty dependencies and completed resolution. All must use the same body observer. The no-context path cannot qualify as an activated test body.

The observer must leave the original function available to `getUsedProps(fn)`. Replacing it with a generic rest-argument wrapper before fixture discovery changes destructuring analysis and can silently omit fixtures. Hook registrations also use `withFixtures`; they must not receive the test-handler marker.

There is no existing runner lifecycle hook at this inner boundary. The adapter adds a private option at the single test-handler call site and routes those five returns through an observer. It changes only disposable, phase-owned dependency copies; the caller's installed dependencies remain untouched.

The implementation uses an exact-source-checked instrumentation step in dv's **phase-owned dependency copies**. Validate the selected Vitest and runner versions and original source bytes before inserting anything; reject unfamiliar bytes, missing or duplicate insertion points, and a module graph that loads a different runner instance. Never edit the caller's package or load a second runner copy with different task WeakMaps. Resolve the selected package through npm/pnpm links, import its ESM `TestRunner` export, and prove that the instrumented collection module is the one executing the task. `vitest/runners` is already deprecated in 4.1.

The production receipt should distinguish `entered`, `finished`, body errors and errors outside the body. Missing activation is a rejection, not evidence of no fixtures. Require exactly one completed invocation for every executed test, a per-run nonce and task identity, and equality between classified body errors and the final runner errors. Preserve stock timeout, cancellation, pending async-assertion and assertion-count behavior; those outer wrappers must not acquire body attribution simply because they report a failure for that task.

Use per-test async context only during the original handler invocation, with a closed flag on settlement. An inherited async store alone is insufficient: detached work can retain it after the body returns. Soft assertions need live-error observation and a task-bound token, not a before/after slice of serialized error arrays. Fixtures can suspend across the body and resume during teardown; their async work must remain outside its context. The existing shutdown checks miss worker teardown errors: also inspect Vitest's final error collection after close, as described below.

### Prototype result

Real runs used npm Vitest 4.1.12, Vite 7.3.7 and Node 20.20.2, with read-only snapshots. Each lifecycle case below ran with `value = 1` and `value = 2` in both forks and threads; all green runs exited zero and entered/finished the body once.

| Red failure location | Body entered | Body errors | Stock result |
| --- | --- | --- | --- |
| Fixture setup, exact reproduction above | No | None | Test failed, exit 1 |
| Body using a fixture-provided value | Once | The assertion | Test failed, exit 1 |
| Fixture teardown | Once | None | Test failed, exit 1 |
| beforeEach | No | None | Test failed, exit 1 |
| afterEach | Once | None | Test failed, exit 1 |
| aroundEach setup | No | None | Test failed, exit 1 |
| aroundAll setup | No | None | Suite failed; test skipped, exit 1 |

The fixture-teardown replay yielded duplicate final errors in Vitest; neither became a body error. These runs demonstrate the insertion point, not a complete proof adapter. The prototype does not implement soft/snapshot classification, concurrency, cancellation or hardened receipt emission.

To reproduce the experiment, use the fixture above and a control whose fixture does only `await use(value)`. Mark only `withFixtures(handler, { context })`, dispatch its five context-bearing returns through an async observer, and record body entry, settlement and caught errors on `context.task.meta`. Run the stock CLI with a reporter that records final task/suite errors. Assert that setup-red never enters, body-red enters and catches the assertion, and both heads pass. The inspected runner chunk's original SHA-256 is `bf6af6d2f4469911879a67e83b8f9e7b20332cf42795032c53457b10ccc1c617`.

### Worker teardown and final errors

A worker-scoped fixture can fail after `onTestRunEnd` without throwing from `ctx.close`, writing stderr or emitting a process-level error. The review reproduction uses `await use(value); throw new Error('worker teardown must be rejected')` with `{ scope: 'worker' }`, and a body that checks the fixture value. In both forks and threads, the base body failed and the head body passed; the head process exited zero while `lateErrors`, `lateStderr` and the run-end errors were empty. Only `ctx.state.getUnhandledErrors()` contained the `Teardown Error` at exit. Treating that run as a proof would accept a broken green phase.

For the exact 4.1.12 adapter, use the exposed, typed `ctx.state.getUnhandledErrors()` accessor **after awaiting the original `ctx.close()`**. Read the entire collection, not only errors seen at run end or a delta from it. Require an available accessor, a successful read returning an array, completed shutdown and an empty collection in both phases. Any collected error rejects the proof; it must not become a body error. Re-read at receipt emission, retaining existing close-rejection, stderr, process-error and post-emission invalidation checks. Reject configured `onUnhandledError` callbacks: returning false suppresses insertion into the collection.

This ordering is supported by the pinned implementation, not by a generic post-run reporter hook. In `vitest@4.1.12/dist/chunks/cli-api.B0Pz1_R7.js`:

- Lines 3027–3035 record a worker's stop response with `state.catchError(..., "Teardown Error")` before resolving the stop promise.
- Lines 3547–3551 start worker termination without awaiting it, so arrival before or after `onTestRunEnd` is timing-dependent. Lines 3572–3583 await the pending terminations during pool close.
- Lines 12031–12041 store unsuppressed errors and return the full collection. The accessor and `ctx.state` are declared in `dist/chunks/reporters.d.DtoKVV2s.d.ts:1164,1297`.
- Lines 14011–14024 await pool close through `Promise.allSettled`. A fulfilled outer close is not proof of error-free shutdown. `onClose` callbacks join that same concurrent wait, so they are not a post-close checkpoint.

Bind these source identities and ordering assumptions alongside the runner instrumentation; reject an unrecognized implementation or a missing final-state checkpoint. This permits worker-scoped fixtures only when that checkpoint is verified, rather than assuming a cross-version stable teardown hook. Only the exact 4.1.12 implementation is allowed.

The updated prototype adds that post-close read and an exit-time recheck. Its worker-teardown replay adds a 100 ms delay after `use` to exercise the late-arrival ordering; the unmodified review reproduction above also exhibited it without a delay. Both pools produced the same results:

| Worker fixture case | Body result | Errors at run end | Errors after close | Shutdown accepted | Prototype exit |
| --- | --- | --- | --- | --- | --- |
| Teardown throws, base | Assertion failed | None | Teardown Error | No | 1 |
| Teardown throws, head | Passed | None | Teardown Error | No | 1 |
| Clean teardown, base | Assertion failed | None | None | Yes | 1 |
| Clean teardown, head | Passed | None | None | Yes | 0 |

All bodies entered and finished once. Only the base assertion became a body error; teardown never did. `tests/test_vitest4_runner.py` now verifies the delayed worker fixture and clean controls against the enabled adapter in both pools.

### Alternatives rejected

| Alternative | Why it is insufficient |
| --- | --- |
| Keep wrapping `getFn`, `runTask` or the `test.callback` trace | All include fixture setup. |
| Observe runner `getWorkerContext` | 4.1.12 moved ownership into `TestFixtures`; the reproduction evades it. |
| Use before/after-try hooks or `result.hooks` | These bracket the wider lifecycle and do not identify where a live error arose. |
| Infer origin from stack text, error names or mismatch counts | Helpers, serialized errors and swallowed mismatches can misattribute an unrelated error. |
| Wrap only global `test` or rewrite user callbacks | Misses imports, extended tests, aliases and generated cases; can break fixture parameter discovery. |
| Add an auto fixture that surrounds `use` | Its lifetime also covers other fixture setup/cleanup, not just the original callback. |
| Disable fixtures without detecting them | The old detector is exactly what failed. Reliable detection and rejection remains a valid fallback, not support. |

## Cross-phase test identity

Every base failure must execute and pass on head with the same repository-relative file, project, suite/test name segments and duplicate-name occurrence. A skipped, todo, missing or renamed head test cannot discharge the failure, even when other tests pass. Unrelated skipped tests are permitted.

## Snapshots

Move per-test read attribution from `SnapshotClient.assert` to `SnapshotClient.match`, using the executing instance and `options.testId`. Stock standard, inline, throw and raw-file matchers call `match` directly in 4.1.12. Keep file-level raw-byte reads, head-blob validation, per-test path/hash sets and ordered consumed entries; a successful file read alone is not evidence that a given test used that oracle.

`match` returns a result; `assertMatchResult` in Vitest's matcher layer creates the plain native Error afterwards. Instrument that construction boundary so only the exact result from an observed mismatch binds the exact thrown Error to the active body. A mismatch counter followed by an arbitrary thrown Error is not sufficient. Cover custom messages, property mismatches that return before `SnapshotState.match`, raw-file promises, soft assertions and swallowed mismatches followed by unrelated errors. Custom matcher helpers that consume a result without this binding must fail closed until explicitly supported.

Capture the expected entry before any overwrite or matcher exception and check the stock key/counter. Missing consumed external, inline or raw-file entries reject even when the test catches the error or skips afterwards. File-level absence remains permitted for the default snapshot-file discovery of tests that consume no snapshots. `probeExpectedSnapshot` can consume an entry even when property comparison returns early. Exact-source guards reject direct `SnapshotState.match` and `probeExpectedSnapshot` calls outside the observed client path, including prototype calls. Refusal is sticky for the worker and cannot be cleared by catching the error. Domain and polling snapshot paths use additional entry points, including `matchDomain` and `processDomainSnapshot`; reject them initially rather than omitting their reads or treating them as ordinary snapshots. The domain refusal test registers a custom matcher through `expect.extend` and `Snapshots.toMatchDomainSnapshot`, with a numeric domain adapter and an existing expected entry of `2`. Its base value `1` mismatches and head value `2` matches without calling the normal `SnapshotClient.match` path; simply instrumenting that method would miss the oracle read and mismatch binding.

Install environment probes before `SnapshotState.create`. The inspected 4.1.12 implementation uses `readSnapshotFile`; raw-file matching also calls it before `match`. Continue hashing actual bytes rather than decoded strings, including absence. Attribute direct environment reads to the calling test using its async context before awaiting IO; compare those per-test path/hash sets across phases, including concurrent tests. The 5.x `readSnapshotFileData` path requires a separate adapter for its returned data and file bytes. Do not assume a `readSnapshotFile` wrapper observes it; reject that path until its read contract is instrumented and tested. Keep save/remove write blocks even with update mode `none`, including obsolete entries.

## Effective configuration and CLI

Retain configuration hashes from before execution and compare them at run end, rejecting changed execution-recipe keys even if the phases converge to the same final configuration. Validate both checkpoints in the receipt and compare the phase recipes.

`viteDefine` is a copy of the original Vite define map, captured before Vitest consumes defines. A JSON-stringified checkout root therefore remains inside it even when `vite.define` no longer exposes that value. Hash it; do not omit the key to make parity pass.

Extend canonicalization to preserve literal text and type while replacing checkout and phase-temp path spans with typed markers, including quoted/escaped string literals. Preserve distinctions between a literal marker string and an actual normalized path. For RegExp aliases retain source, flags and `lastIndex`, normalizing both literal and escaped root forms. Equal committed config must hash identically across execution roots; changing a define value, regex suffix/flags, external path or project relation must still fail parity. Continue rejecting unsupported objects/cycles and binding imported config, setup and environment inputs. Account for per-project inherited defaults and Vite environment graphs explicitly, not by dropping whole fields.

| Input or feature | 4.1.12 behavior |
| --- | --- |
| Snapshot update | Inject `--update=none`; retain `CI=true`, removal of `UPDATE_SNAPSHOT`, resolved-mode validation and write blocks. |
| `--minWorkers` | Reject both separate and equals forms with a version-specific message before launching the phase. Keep 3.2.x behavior. |
| Version selection | Add a local exact-version preflight for argument selection, then recheck the executing version and source identity in the receipt. No network discovery. |
| aroundEach / aroundAll | Reject their presence in every collected suite, including passing hooks and inherited hooks, before running user tests. Do not merely add their names to the accepted hook-state list. |
| Runner selection | Use the selected package's ESM entry and executing Chai identity; keep custom runners/pools and browser mode unsupported. |

## Acceptance contract

Private runner-source drift, duplicate module identities, async attribution escaping the body, snapshot result/Error misbinding, and overly broad configuration normalization require rejection tests as well as successful proof controls.

The exact-version adapter must preserve all of the following:

- Body-only failures qualify; fixture setup/teardown, hooks, cleanups, callbacks, assertion-count failures, late errors and import/runtime errors do not. Cover no/unused/auto fixtures, dependency chains, overrides, test/file/worker scopes, concurrency, soft and async assertions, timeout and cancellation. Missing instrumentation always rejects.
- Worker teardown failures reject in both pools even when `onTestRunEnd`, close guards, stderr and the exit code indicate success. Require a readable, empty post-close and pre-receipt error collection; error-filter callbacks, missing checkpoints and incomplete close reject. Clean worker-fixture red/green controls must still qualify.
- Snapshot reads, unique full names, ordered keys and expected serializations retain parity. Switched files/entries, added external snapshots, duplicate names, early property mismatch, non-UTF-8 raw files, missing files and write attempts fail for the right reason. Domain and polling matchers reject until their separate entry points are instrumented.
- Re-run the existing real-runner regressions rather than translating only happy paths. The 0.2.0 reviews require root globalSetup rejection, late-close error capture, live Error provenance, effective harness identity (including external inputs), unchanged Vite filesystem policy, copied dependency isolation, Python independence, clean execution copies, shipped-artifact path scans and timeout cleanup. Preserve the documented limit for unreported plugin filesystem reads. The later action reviews also require branch/tag disambiguation and leading-dash branch rejection.
- Verify the matrix below with real red/green proofs, installed wheel and source execution, npm and pnpm isolated/hoisted/workspace layouts. Run the 3.2.x regression matrix separately so the new adapter cannot weaken existing support.
- Require independent review with zero remaining false-PASS findings. Keep behavioral assertions for the enabled adapter and rejection tests for every other 4.x and all 5.x versions.

### Required matrix

Every cell is an acceptance requirement. The implementation matrix uses Node 20.20, 22.23 and 24.16; use compatible patch versions starting at Node 20.19 and 22.12.

| Vitest 4.1.12 | Node 20 | Node 22 | Node 24 |
| --- | --- | --- | --- |
| Vite 6.4.5 | Required | Required | Required |
| Vite 7.3.7 | Required | Required | Required |
| Vite 8.3.5 | Required | Required | Required |

Each cell covers forks and threads, genuine assertion and snapshot red/green controls, lifecycle refusals, config identity, dependency isolation and cleanup. The baseline remains Vitest 3.2.4/3.2.7 with Vite 6.3.5/6.4.5/7.3.7. Tooling must be prepared explicitly; skipped optional tests do not establish a matrix cell.

## Source pointers

Inspected artifacts came from the public npm registry, not a moving repository branch:

- [`@vitest/runner@4.1.12`](https://registry.npmjs.org/@vitest/runner/-/runner-4.1.12.tgz), `dist/chunk-artifact.js`: `TestFixtures`, `withFixtures`, `createTaskCollector`, `runTest`, fixture cleanup and around-hook dispatch.
- [`vitest@4.1.12`](https://registry.npmjs.org/vitest/-/vitest-4.1.12.tgz), `dist/chunks/test.DNmyFkvJ.js`: `SnapshotPlugin`, `assertMatchResult`, `toMatchFileSnapshotImpl`, `VitestTestRunner` lifecycle; `dist/index.js`: ESM `TestRunner` export.
- The same Vitest artifact, `dist/chunks/cli-api.B0Pz1_R7.js`: `viteDefine` assignment; `dist/chunks/cac.*.js`: update argument declaration and CLI options.
- [`@vitest/snapshot@4.1.12`](https://registry.npmjs.org/@vitest/snapshot/-/snapshot-4.1.12.tgz), `dist/index.js`: `SnapshotState.create`, `probeExpectedSnapshot`, `SnapshotClient.match` and domain matching.
