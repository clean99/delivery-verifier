import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, relative, resolve } from 'node:path';

// Private boundaries are accepted only for these published 4.1.12 artifacts.
export const sources = {
  runner: ['@vitest/runner', 'dist/chunk-artifact.js',
    'bf6af6d2f4469911879a67e83b8f9e7b20332cf42795032c53457b10ccc1c617'],
  runnerEntry: ['@vitest/runner', 'dist/index.js',
    '61a758d5e6d41e0928c92bfd5ab7152bde625d90b97a08b269dc5a0438485d35'],
  entry: ['vitest', 'dist/index.js',
    '3986618dbcbbdc0a3e200e2a507a434ef6f13dd8cc20c234168edb7b16e98d29'],
  matchers: ['vitest', 'dist/chunks/test.DNmyFkvJ.js',
    'f07fc8036b066834143b14748bd70c436a6a2f15f648455af8d3a003a4eafc94'],
  shutdown: ['vitest', 'dist/chunks/cli-api.B0Pz1_R7.js',
    '222aa60f8555aea9311703c68edaaa8eba0888ea29bfb4a49b37f98e0811ee2d'],
  expect: ['@vitest/expect', 'dist/index.js',
    'b177ca97952f1d65d3d2691edbc31ab1af56ac36b3cf08d7f56ad0ca69f07a45'],
  snapshot: ['@vitest/snapshot', 'dist/index.js',
    'd1b07d9337f1caf8b9b59ddb1ba1d58df92ce77cb4646df7700e07e994522128'],
};
const observer = "globalThis[Symbol.for('dv.vitest4.body')]";
const hash = text => createHash('sha256').update(text).digest('hex');

function replace(text, before, after, count = 1) {
  if (text.split(before).length !== count + 1) {
    throw new Error('dv: missing or duplicate Vitest 4.1.12 insertion point');
  }
  return text.replaceAll(before, after);
}

function instrument(key, text) {
  if (key === 'runner') {
    const start = text.indexOf('function withFixtures(fn, options) {');
    const end = text.indexOf('\nfunction isAutoFixture', start);
    const fixture = replace(text.slice(start, end), 'return fn(context);',
      `return options?.dvBody ? ${observer}.invoke(fn, context, import.meta.url, getFn) : fn(context);`, 5);
    text = text.slice(0, start) + fixture + text.slice(end);
    text = replace(text, 'withFixtures(handler, { context })',
      'withFixtures(handler, { context, dvBody: true })');
    const fail = text.indexOf('function failTask(');
    const insertion = 'result.errors.push(...errors);';
    text = text.slice(0, fail) + replace(text.slice(fail), insertion,
      `${observer}.errors(result, e, errors);\n\t\t${insertion}`);
  } else if (key === 'expect') {
    text = replace(text, 'test.result.errors.push(processError(err));',
      `const serialized = processError(err);\n\t${observer}.soft(test, err, serialized, import.meta.url);\n\ttest.result.errors.push(serialized);`);
  } else if (key === 'matchers') {
    text = replace(text, 'throw Object.assign(new Error(errorMessage), {',
      'const error = Object.assign(new Error(errorMessage), {');
    text = replace(text, 'diffOptions: { expand: getWorkerState().config.snapshotOptions.expand }\n\t\t});\n\t}\n}',
      `diffOptions: { expand: getWorkerState().config.snapshotOptions.expand }\n\t\t});\n\t\t${observer}.snapshotResult(result, error, import.meta.url);\n\t\tthrow error;\n\t}\n\t${observer}.snapshotResult(result, null, import.meta.url);\n}`);
  }
  return text;
}

export function selectedSources(packageFile, ownedRoot = null) {
  const require = createRequire(realpathSync(packageFile));
  const result = {};
  const writes = [];
  const manifest = ownedRoot ? null : JSON.parse(readFileSync(process.env.DV_VITEST4_SOURCES, 'utf8'));
  const root = ownedRoot && realpathSync(ownedRoot);
  for (const [key, [name, file, expected]] of Object.entries(sources)) {
    const packagePath = require.resolve(`${name}/package.json`);
    if (JSON.parse(readFileSync(packagePath, 'utf8')).version !== '4.1.12') {
      throw new Error(`dv: unsupported ${name} source version`);
    }
    const path = realpathSync(resolve(dirname(packagePath), file));
    const original = readFileSync(path, 'utf8');
    if (ownedRoot) {
      const location = relative(root, path);
      if (isAbsolute(location) || location === '..' || location.startsWith('../')) {
        throw new Error('dv: Vitest source leaves phase-owned dependencies');
      }
      if (hash(original) !== expected) throw new Error(`dv: unrecognized Vitest 4.1.12 source: ${key}`);
      const modified = instrument(key, original);
      writes.push([path, modified]);
      result[key] = { path, sha256: hash(modified) };
    } else {
      if (manifest[key]?.path !== path || manifest[key]?.sha256 !== hash(original)) {
        throw new Error(`dv: executing Vitest 4.1.12 source identity differs: ${key}`);
      }
      result[key] = manifest[key];
    }
  }
  for (const [path, text] of writes) writeFileSync(path, text);
  return result;
}
