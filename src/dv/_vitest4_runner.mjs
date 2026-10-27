import { AssertionError as NodeAssertionError } from 'node:assert';
import { AsyncLocalStorage, createHook } from 'node:async_hooks';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { types } from 'node:util';
import { environments, observeSnapshots, recordSnapshot } from './_vitest_snapshots.mjs';

const { TestRunner } = await import(pathToFileURL(process.env.DV_VITEST_RUNNERS_MODULE));
const { AssertionError: ChaiAssertionError } = await import(
  pathToFileURL(process.env.DV_VITEST_CHAI_MODULE)
);
const sources = JSON.parse(readFileSync(process.env.DV_VITEST4_SOURCES, 'utf8'));
const sourceIs = (key, url) => pathToFileURL(sources[key].path).href === url;
const contexts = new WeakMap();
const thrown = new WeakMap();
const results = new WeakMap();
const pending = new Map();
const clients = new WeakSet();
const bodyContext = new AsyncLocalStorage();
const promises = new WeakMap();
const promiseIds = new Map();
// Close at promise settlement, before detached microtasks or .then observers can run.
createHook({
  init(id, type, trigger, resource) {
    if (type !== 'PROMISE') return;
    const record = { settled: false, context: null };
    promises.set(resource, record);
    promiseIds.set(id, record);
  },
  promiseResolve(id) {
    const record = promiseIds.get(id);
    if (record) {
      record.settled = true;
      if (record.context) record.context.open = false;
      promiseIds.delete(id);
    }
  },
  destroy(id) { promiseIds.delete(id); },
}).enable();
const files = new Set();
let unsupportedSnapshots = false;
const nonce = process.env.DV_VITEST_NONCE;
const symbol = Symbol.for('dv.vitest4.body');
if (globalThis[symbol]) throw new Error('dv: duplicate Vitest 4 body observer');

function active(test) {
  const context = bodyContext.getStore();
  return context?.open && (!test || context.task === test) ? context : null;
}

function errorRecord(error, context) {
  let assertion = null;
  if (types.isNativeError(error)) {
    if (error.constructor === ChaiAssertionError && error instanceof ChaiAssertionError) {
      assertion = 'chai';
    } else if (error.constructor === NodeAssertionError && error instanceof NodeAssertionError) {
      assertion = 'node';
    } else if (context.snapshots.has(error)) assertion = 'snapshot';
  }
  return { name: error?.name ?? null, message: error?.message ?? null,
    assertion, token: randomUUID() };
}

const observer = {
  async invoke(fn, context, url, getFn) {
    const task = context.task;
    const store = contexts.get(task);
    if (!sourceIs('runner', url) || getFn !== TestRunner.getTestFn || !getFn(task) ||
      !store || store.record.entered !== 0 || task.context !== context) {
      throw new Error('dv: different or inactive Vitest collection module');
    }
    store.record.entered++;
    store.open = true;
    try {
      const value = bodyContext.run(store, () => fn(context));
      if (!value || typeof value.then !== 'function') {
        store.open = false;
        return value;
      }
      const promise = promises.get(value);
      if (!promise) throw new Error('dv: unsupported test-body thenable');
      promise.context = store;
      if (promise.settled) store.open = false;
      return await value;
    } catch (error) {
      const record = errorRecord(error, store);
      store.record.errors.push(record);
      if (error && (typeof error === 'object' || typeof error === 'function')) {
        thrown.set(error, { store, record });
      }
      throw error;
    } finally {
      store.open = false;
      store.record.finished++;
    }
  },
  soft(test, error, serialized, url) {
    if (!sourceIs('expect', url)) throw new Error('dv: different Vitest assertion module');
    const context = active(test);
    if (!context) return;
    const record = errorRecord(error, context);
    context.record.errors.push(record);
    serialized.dvBodyToken = record.token;
  },
  errors(result, error, serialized) {
    const observed = error && thrown.get(error);
    if (observed && observed.store.task.result === result && serialized.length === 1) {
      serialized[0].dvBodyToken = observed.record.token;
      thrown.delete(error);
    }
  },
  snapshotResult(result, error, url) {
    const observed = results.get(result);
    if (!sourceIs('matchers', url) || !observed || observed.consumed) {
      throw new Error('dv: unobserved snapshot result');
    }
    observed.consumed = true;
    pending.get(observed.testId).delete(result);
    const context = active();
    if (error && context && context === observed.context && context.task.id === observed.testId) {
      context.snapshots.add(error);
    }
  },
};
Object.defineProperty(globalThis, symbol, { value: observer });

function refuseSnapshots() {
  unsupportedSnapshots = true;
  for (const file of files) file.meta.dvUnsupportedSnapshots = true;
  throw new Error('dv: domain and polling snapshots are unsupported');
}

function installSnapshots(client) {
  if (clients.has(client)) return;
  clients.add(client);
  for (const name of ['matchDomain', 'pollMatchDomain']) client[name] = refuseSnapshots;
  const match = client.match;
  client.match = function(options) {
    recordSnapshot(this, options);
    const state = this.getSnapshotState(options.filepath);
    const testId = options.testId;
    const records = environments.get(state.environment)?.entries.get(testId);
    if (!records) throw new Error('dv: missing snapshot test identity');
    const testName = [options.name, ...(options.message ? [options.message] : [])].join(' > ');
    const count = state._counters.get(testName) + 1;
    const key = `${testName} ${count}`;
    const kind = options.isInline ? 'inline' : options.rawSnapshot ? 'raw' : 'external';
    const expected = options.isInline ? options.inlineSnapshot : options.rawSnapshot
      ? options.rawSnapshot.content : state._snapshotData[key];
    if (expected != null && typeof expected !== 'string') {
      throw new Error('dv: unsupported snapshot entry');
    }
    const entry = { path: resolve(options.isInline ? options.filepath
      : options.rawSnapshot?.file ?? state.snapshotPath), kind, key,
      sha256: expected == null ? null : createHash('sha256').update(expected).digest('hex') };
    // probeExpectedSnapshot can consume an entry before State.match is reached.
    const result = match.call(this, options);
    if (state._counters.get(testName) !== count ||
      state._testIdToKeys.get(testId)?.at(-1) !== key) {
      throw new Error('dv: unsupported snapshot entry key or counter');
    }
    if (kind === 'inline') {
      const stack = state._inlineSnapshotStacks.filter(stack => stack.testId === testId).at(-1);
      if (stack?.file) entry.path = resolve(stack.file);
    }
    records.push(entry);
    results.set(result, { testId, context: active(), consumed: false });
    if (!pending.has(testId)) pending.set(testId, new Set());
    pending.get(testId).add(result);
    return result;
  };
}

export default class DeliveryRunner extends TestRunner {
  constructor(config) {
    super(config);
    installSnapshots(this.snapshotClient);
  }

  onCollectStart(file) {
    super.onCollectStart(file);
    file.meta.dvProbe = nonce;
    file.meta.dvSnapshots = [];
    file.meta.dvUnsupportedSnapshots = unsupportedSnapshots;
    files.add(file);
  }

  onCollected(files) {
    function visit(task) {
      if (task.type === 'suite') {
        const hooks = TestRunner.getSuiteHooks(task);
        if (!hooks) throw new Error('dv: different Vitest collection module');
        if (hooks.aroundEach?.length || hooks.aroundAll?.length) {
          throw new Error('dv: aroundEach/aroundAll are unsupported');
        }
        for (const child of task.tasks) visit(child);
      } else if (task.mode === 'run' && !TestRunner.getTestFn(task)) {
        throw new Error('dv: missing instrumented test function');
      }
    }
    files.forEach(visit);
  }

  async onBeforeRunSuite(suite) {
    if (suite.mode !== 'skip' && 'filepath' in suite) {
      const environment = this.workerState.config.snapshotOptions.snapshotEnvironment;
      if (typeof environment.readSnapshotFile !== 'function' || environment.readSnapshotFileData) {
        throw new Error('dv: unsupported snapshot read contract');
      }
      observeSnapshots(environment, suite.file);
    }
    await super.onBeforeRunSuite(suite);
    if (suite.mode !== 'skip' && 'filepath' in suite) {
      this.snapshotClient.getSnapshotState(suite.file.filepath).processDomainSnapshot = refuseSnapshots;
    }
  }

  async onBeforeRunTask(task) {
    await super.onBeforeRunTask(task);
    const record = { nonce, task: task.id, entered: 0, finished: 0, errors: [],
      pendingSnapshots: 0 };
    task.meta.dvBody = record;
    contexts.set(task, { task, record, open: false, snapshots: new WeakSet() });
  }

  onAfterRunTask(task) {
    const context = contexts.get(task);
    if (context) {
      context.open = false;
      context.record.pendingSnapshots = pending.get(task.id)?.size ?? 0;
    }
    super.onAfterRunTask(task);
  }
}
