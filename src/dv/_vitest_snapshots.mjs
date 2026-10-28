import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export const environments = new WeakMap();

export function observeSnapshots(environment, file, currentTest) {
  let observer = environments.get(environment);
  if (!observer) {
    observer = {};
    environments.set(environment, observer);
    const read = environment.readSnapshotFile;
    environment.readSnapshotFile = async function(path, ...args) {
      // Capture attribution before awaiting IO, including direct environment reads.
      const records = observer.records;
      const testRecords = observer.tests.get(observer.currentTest?.());
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
      records.push(record);
      testRecords?.push(record);
      observer.reads.set(record.path, record);
      return content;
    };
    for (const name of ['saveSnapshotFile', 'removeSnapshotFile']) {
      environment[name] = async function() {
        throw new Error('dv: snapshot writes are not allowed during proof');
      };
    }
  }
  observer.currentTest = currentTest;
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

export function recordSnapshot(client, options) {
  if (options.isInline) return;
  const state = client.getSnapshotState(options.filepath);
  const observer = environments.get(state.environment);
  const read = observer?.reads.get(resolve(options.rawSnapshot?.file ?? state.snapshotPath));
  const records = observer?.tests.get(options.testId);
  if (!read || !records) throw new Error('dv: missing per-test snapshot input');
  records.push(read);
}
