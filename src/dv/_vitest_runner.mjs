import { AssertionError as NodeAssertionError } from 'node:assert';
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { types } from 'node:util';

const { VitestTestRunner } = await import(pathToFileURL(process.env.DV_VITEST_RUNNERS_MODULE));
const { getFn } = await import(pathToFileURL(process.env.DV_VITEST_TASKS_MODULE));
const { AssertionError: ChaiAssertionError } = await import(
  pathToFileURL(process.env.DV_VITEST_CHAI_MODULE)
);
const bodyContext = new AsyncLocalStorage();
const clients = new WeakSet();
const states = new WeakSet();
const environments = new WeakMap();

function observeSnapshots(environment, file) {
  let observer = environments.get(environment);
  if (!observer) {
    observer = {};
    environments.set(environment, observer);
    const read = environment.readSnapshotFile;
    environment.readSnapshotFile = async function(path, ...args) {
      const content = await read.call(this, path, ...args);
      if (content != null && typeof content !== 'string') {
        throw new Error('dv: unsupported snapshot content');
      }
      let bytes;
      try {
        bytes = await readFile(path);
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        bytes = null;
      }
      if ((bytes == null) !== (content == null)) {
        throw new Error('dv: snapshot content does not match file presence');
      }
      const record = {
        path: resolve(path),
        sha256: bytes == null ? null : createHash('sha256').update(bytes).digest('hex'),
      };
      observer.records.push(record);
      observer.reads.set(record.path, record);
      return content;
    };
    for (const name of ['saveSnapshotFile', 'removeSnapshotFile']) {
      environment[name] = async function() {
        throw new Error('dv: snapshot writes are not allowed during proof');
      };
    }
  }
  observer.records = file.meta.dvSnapshots;
  observer.reads = new Map();
  observer.tests = new Map();
  observer.entries = new Map();
  const occurrences = new Map();
  function collect(task, names) {
    if (task.type === 'test') {
      const name = JSON.stringify(names);
      const occurrence = (occurrences.get(name) ?? 0) + 1;
      occurrences.set(name, occurrence);
      task.meta.dvSnapshotIdentity = { names, occurrence };
      task.meta.dvSnapshots = [];
      task.meta.dvSnapshotEntries = [];
      observer.tests.set(task.id, task.meta.dvSnapshots);
      observer.entries.set(task.id, task.meta.dvSnapshotEntries);
    }
    for (const child of task.tasks ?? []) collect(child, [...names, child.name]);
  }
  collect(file, []);
}

function recordSnapshot(client, options) {
  if (options.isInline) return;
  const state = client.getSnapshotState(options.filepath);
  const observer = environments.get(state.environment);
  const read = observer?.reads.get(resolve(options.rawSnapshot?.file ?? state.snapshotPath));
  const records = observer?.tests.get(options.testId);
  if (!read || !records) throw new Error('dv: missing per-test snapshot input');
  records.push(read);
}

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
