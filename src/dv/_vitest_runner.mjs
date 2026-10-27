import { AssertionError as NodeAssertionError } from 'node:assert';
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash, randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { types } from 'node:util';

const { VitestTestRunner } = await import(pathToFileURL(process.env.DV_VITEST_RUNNERS_MODULE));
const { getFn } = await import(pathToFileURL(process.env.DV_VITEST_TASKS_MODULE));
const { AssertionError: ChaiAssertionError } = await import(
  pathToFileURL(process.env.DV_VITEST_CHAI_MODULE)
);
import { environments, observeSnapshots, recordSnapshot } from './_vitest_snapshots.mjs';

const bodyContext = new AsyncLocalStorage();
const clients = new WeakSet();
const states = new WeakSet();
function assertionKind(error, context) {
  if (types.isNativeError(error)) {
    if (error instanceof ChaiAssertionError && error.constructor === ChaiAssertionError) return 'chai';
    if (error instanceof NodeAssertionError && error.constructor === NodeAssertionError) return 'node';
    if (context.snapshots.has(error)) return 'snapshot';
  }
  return context.softAssertions.has(error?.dvAssertion) ? 'chai' : null;
}

// expect.soft serializes errors before runTask can see them. Classify the live instance.
const toJSON = ChaiAssertionError.prototype.toJSON;
ChaiAssertionError.prototype.toJSON = function(...args) {
  const record = toJSON.apply(this, args);
  const context = bodyContext.getStore();
  if (context && types.isNativeError(this) && this instanceof ChaiAssertionError &&
    this.constructor === ChaiAssertionError) {
    record.dvAssertion = randomUUID();
    context.softAssertions.add(record.dvAssertion);
  }
  return record;
};

export default class DeliveryRunner extends VitestTestRunner {
  constructor(config) {
    super(config);
    const client = this.snapshotClient;
    if (!clients.has(client)) {
      clients.add(client);
      const equal = client.options.isEqual;
      if (equal) client.options.isEqual = function(...args) {
        const result = equal.apply(this, args);
        const context = bodyContext.getStore();
        if (context && result === false) context.mismatches++;
        return result;
      };
      const assert = client.assert;
      client.assert = function(...args) {
        recordSnapshot(this, args[0]);
        const context = bodyContext.getStore();
        const before = context?.mismatches;
        try {
          return assert.apply(this, args);
        } catch (error) {
          if (context && context.mismatches > before && types.isNativeError(error)) {
            context.snapshots.add(error);
          }
          throw error;
        }
      };
    }
  }

  getWorkerContext() {
    const context = bodyContext.getStore();
    if (context) context.record.fixtures = true;
    return super.getWorkerContext();
  }

  onCollectStart(file) {
    super.onCollectStart(file);
    file.meta.dvProbe = process.env.DV_VITEST_NONCE;
    file.meta.dvSnapshots = [];
  }

  async onBeforeRunSuite(suite) {
    if (suite.mode !== 'skip' && 'filepath' in suite) {
      observeSnapshots(this.workerState.config.snapshotOptions.snapshotEnvironment,
        suite.file);
    }
    await super.onBeforeRunSuite(suite);
    if (suite.mode === 'skip' || !('filepath' in suite)) return;
    const state = this.snapshotClient.getSnapshotState(suite.file.filepath);
    if (!states.has(state)) {
      states.add(state);
      const match = state.match;
      state.match = function(options) {
        const key = options.key || `${options.testName} ${this._counters.get(options.testName) + 1}`;
        let expected = this._snapshotData[key];
        let path = this.snapshotPath;
        let kind = 'external';
        if (options.isInline) {
          expected = options.inlineSnapshot;
          kind = 'inline';
        } else if (options.rawSnapshot) {
          kind = 'raw';
          path = options.rawSnapshot.file;
        }
        const result = match.call(this, options);
        if (result.key !== key) throw new Error('dv: unsupported snapshot entry key');
        if (kind === 'inline') path = this._inlineSnapshotStacks.at(-1)?.file;
        if (kind === 'raw') expected = options.rawSnapshot.content;
        const records = environments.get(this.environment)?.entries.get(options.testId);
        if (!records || typeof path !== 'string' ||
          (expected != null && typeof expected !== 'string')) {
          throw new Error('dv: missing per-test snapshot entry');
        }
        records.push({ path: resolve(path), kind, key,
          sha256: expected == null ? null : createHash('sha256').update(expected).digest('hex'),
        });
        const context = bodyContext.getStore();
        if (context && result.pass === false) context.mismatches++;
        return result;
      };
    }
  }

  async runTask(test) {
    const body = { nonce: process.env.DV_VITEST_NONCE, fixtures: false, errors: [] };
    const context = {
      record: body, softAssertions: new Set(), snapshots: new WeakSet(), mismatches: 0,
    };
    test.meta.dvBody = body;
    const previousErrors = test.result?.errors?.length ?? 0;
    const thrown = [];
    try {
      await bodyContext.run(context, () => getFn(test)());
    } catch (error) {
      thrown.push(error);
      throw error;
    } finally {
      body.errors = [
        ...(test.result?.errors || []).slice(previousErrors), ...thrown,
      ].map(error => ({
        name: error?.name ?? null,
        message: error?.message ?? null,
        assertion: assertionKind(error, context),
      }));
    }
  }
}
