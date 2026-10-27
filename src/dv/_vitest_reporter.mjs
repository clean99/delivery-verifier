import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { basename, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { selectedSources } from './_vitest4_sources.mjs';

const errors = values => (values || []).map(error => ({
  name: error?.name ?? null,
  message: error?.message ?? null,
}));

function pathSpans(text, root, temporary, regexp = false) {
  const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const paths = [];
  for (const [path, marker] of [[root, 'root'], [temporary, 'temporary']]) {
    if (!path) continue;
    const forms = [path, JSON.stringify(path).slice(1, -1), path.replaceAll('/', '\\/')];
    if (regexp) forms.push(escape(path), escape(path).replaceAll('/', '\\/'),
      escape(path).replaceAll('-', '\\-').replaceAll('/', '\\/'));
    for (const form of new Set(forms)) paths.push([form, marker]);
  }
  paths.sort((a, b) => b[0].length - a[0].length);
  const pattern = new RegExp(paths.map(([path]) => escape(path)).join('|'), 'g');
  const parts = [];
  let start = 0;
  for (const match of text.matchAll(pattern)) {
    const after = text.slice(match.index + match[0].length);
    // Do not turn a different external path sharing a prefix into a checkout path.
    if (/^[\w.-]/.test(after)) continue;
    parts.push(text.slice(start, match.index), [paths.find(([path]) => path === match[0])[1]]);
    start = match.index + match[0].length;
  }
  parts.push(text.slice(start));
  return parts;
}

export function configHashes(config, vite, root) {
  const omitted = new Set(['runner', 'reporter', 'reporters', 'cache', 'cacheDir']);
  const probeKeys = new Set(['DV_VITEST_REPORT', 'DV_VITEST_NONCE', 'DV_VITEST_ROOT',
    'DV_VITEST_RUNNERS_MODULE', 'DV_VITEST_TASKS_MODULE', 'DV_VITEST_CHAI_MODULE', 'DV_VITEST_TEMP',
    'DV_VITEST4_PACKAGE', 'DV_VITEST4_SOURCES']);
  const temporary = process.env.DV_VITEST_TEMP;
  function canonical(value, trail = [], seen = new Set()) {
    if (typeof value === 'string') return ['string', pathSpans(value, root, temporary)];
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'number') return ['number', Object.is(value, -0) ? '-0' : String(value)];
    if (typeof value === 'undefined') return ['undefined'];
    if (typeof value === 'function') return ['function', canonical(value.toString())];
    if (value instanceof RegExp) {
      return ['regexp', pathSpans(value.source, root, temporary, true), value.flags, value.lastIndex];
    }
    if (value instanceof Date) return ['date', value.toISOString()];
    if (typeof value !== 'object' || seen.has(value)) {
      throw new Error('unsupported or circular effective configuration');
    }
    seen.add(value);
    let result;
    if (Array.isArray(value)) {
      result = ['array', Array.from({ length: value.length }, (_, index) =>
        index in value ? canonical(value[index], trail, seen) : ['hole'])];
    } else if (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null) {
      result = ['object', Object.keys(value).sort().filter(key =>
        !(trail.length === 1 && trail[0] === 'env' && probeKeys.has(key)) &&
        !(trail.length === 1 && trail[0] === 'api' && ['port', 'token'].includes(key))
      ).map(key => [key, canonical(value[key], [...trail, key], seen)])];
    } else {
      throw new Error('unsupported effective configuration object');
    }
    seen.delete(value);
    return result;
  }
  const hashes = {};
  for (const [key, value] of Object.entries({ ...config, 'vite.env': vite.env,
    'vite.define': vite.define, 'vite.resolve': vite.resolve })) {
    if (omitted.has(key)) continue;
    try {
      hashes[key] = createHash('sha256').update(JSON.stringify(canonical(value, [key]))).digest('hex');
    } catch {
      throw new Error(`dv: cannot canonicalize effective configuration key ${key}`);
    }
  }
  return hashes;
}

function taskRecord(task) {
  const result = task.result;
  return {
    id: task.id,
    type: task.type,
    name: task.name,
    filepath: task.filepath ?? null,
    project: task.projectName ?? '',
    mode: task.mode,
    fails: task.fails ?? false,
    result: result ? {
      state: result.state,
      errors: (result.errors || []).map(error => ({ ...errors([error])[0],
        ...(error.dvBodyToken ? { token: error.dvBodyToken } : {}) })),
      hooks: result.hooks ?? {},
      retryCount: result.retryCount ?? 0,
      repeatCount: result.repeatCount ?? 0,
    } : null,
    probe: task.meta?.dvProbe ?? null,
    unsupportedSnapshots: task.meta?.dvUnsupportedSnapshots ?? null,
    body: task.meta?.dvBody ?? null,
    snapshots: task.meta?.dvSnapshots ?? null,
    snapshotIdentity: task.meta?.dvSnapshotIdentity ?? null,
    snapshotEntries: task.meta?.dvSnapshotEntries ?? null,
    tasks: task.tasks?.map(taskRecord) ?? [],
  };
}

export default class DeliveryReporter {
  record = { complete: false, error: 'Vitest did not finish' };
  lateErrors = [];
  lateStderr = [];
  closing = false;
  closed = false;
  written = false;
  guarded = new WeakSet();
  harness = [];
  postCloseErrors = null;
  finalErrors() {
    if (typeof this.ctx?.state?.getUnhandledErrors !== 'function') {
      throw new Error('dv: missing final Vitest error collection');
    }
    const collected = this.ctx.state.getUnhandledErrors();
    if (!Array.isArray(collected)) throw new Error('dv: malformed final Vitest error collection');
    return errors(collected);
  }

  guardClose(target) {
    if (!target || typeof target.close !== 'function' || this.guarded.has(target)) return;
    this.guarded.add(target);
    const close = target.close.bind(target);
    target.close = async (...args) => {
      try {
        return await close(...args);
      } catch (error) {
        this.invalidate(error);
        throw error;
      }
    };
  }

  invalidate(error) {
    this.lateErrors.push(error);
    // Exit listeners registered after ours can still fail after the JSON was written.
    if (this.written) writeFileSync(process.env.DV_VITEST_REPORT, '\ninvalidated\n', { flag: 'a' });
  }

  onInit(ctx) {
    this.version = ctx.version;
    this.ctx = ctx;
    this.v4 = this.version === '4.1.12';
    process.on('uncaughtExceptionMonitor', error => this.invalidate(error));
    process.on('unhandledRejection', error => {
      this.invalidate(error);
      process.exitCode = 1;
    });
    const stderr = process.stderr.write;
    process.stderr.write = (...args) => {
      if (this.closing || this.written) {
        this.lateStderr.push(String(args[0]));
        if (this.written) this.invalidate(new Error('stderr after final report'));
      }
      return stderr.apply(process.stderr, args);
    };
    const close = ctx.close.bind(ctx);
    ctx.close = async (...args) => {
      if (!this.closing) {
        // Vite uses allSettled during shutdown; observe rejection before it is swallowed.
        for (const project of new Set([ctx.getRootProject(), ...ctx.projects])) {
          const server = project.vite;
          const environments = Object.values(server?.environments || {});
          for (const target of [project, server, server?.watcher, server?.ws,
            server?.pluginContainer, ...environments.flatMap(environment => [
              environment, environment.pluginContainer, environment.depsOptimizer, environment.hot,
            ])]) this.guardClose(target);
        }
      }
      this.closing = true;
      try {
        const result = await close(...args);
        if (this.v4) this.postCloseErrors = this.finalErrors();
        this.closed = true;
        return result;
      } catch (error) {
        this.invalidate(error);
        throw error;
      }
    };
    process.once('exit', exitCode => {
      let finalErrors = null;
      if (this.v4) {
        try { finalErrors = this.finalErrors(); } catch (error) { this.invalidate(error); }
      }
      writeFileSync(process.env.DV_VITEST_REPORT, JSON.stringify({
        schema_version: 7, runner: 'vitest', version: this.version,
        nonce: process.env.DV_VITEST_NONCE, ...this.record,
        exitCode, shutdownComplete: this.closed,
        lateErrors: errors(this.lateErrors), lateStderr: this.lateStderr,
        ...(this.v4 ? { sourceVerified: this.sourceVerified === true,
          postCloseErrors: this.postCloseErrors, finalErrors } : {}),
      }), { flag: 'wx' });
      this.written = true;
    });

    const configs = [ctx.config, ...ctx.projects.map(project => project.config)];
    let error;
    if (!/^3\.2\.\d+$/.test(this.version) && !this.v4) {
      error = `Unsupported Vitest version ${this.version}; supported: 3.2.x`;
    } else if (configs.some(config => config.runner ||
      config.browser?.enabled || !['forks', 'threads'].includes(config.pool))) {
      error = 'Vitest proof requires the default runner and forks/threads pool; ' +
        'custom runners, custom pools and browser mode are unsupported';
    }
    if (configs.some(config => config.coverage?.enabled)) {
      error = 'Vitest coverage must be disabled during proof';
    }
    if (this.v4 && configs.some(config => config.onUnhandledError != null)) {
      error = 'Vitest onUnhandledError callbacks are unsupported';
    }
    if (configs.some(config => config.globalSetup?.length)) {
      error = 'Vitest globalSetup/teardown is unsupported';
    }
    if (configs.some(config => config.snapshotOptions?.updateSnapshot !== 'none')) {
      error = 'Vitest proof requires read-only snapshots (CI mode, update disabled)';
    }
    if (error) {
      this.record = { complete: false, error };
      throw new Error(`dv: ${error}`);
    }
    this.ctx = ctx;
    this.harness = this.harnessInputs(ctx);
    const require = createRequire(process.argv[1]);
    if (this.v4) {
      const sources = selectedSources(require.resolve('vitest/package.json'));
      this.sourceVerified = true;
      process.env.DV_VITEST_RUNNERS_MODULE = sources.entry.path;
      const executing = createRequire(sources.matchers.path);
      process.env.DV_VITEST_CHAI_MODULE = executing.resolve('chai');
    } else {
      process.env.DV_VITEST_RUNNERS_MODULE = require.resolve('vitest/runners');
      process.env.DV_VITEST_TASKS_MODULE = require.resolve('@vitest/runner');
      process.env.DV_VITEST_CHAI_MODULE = require.resolve('chai');
    }
    for (const project of ctx.projects) {
      project.config.runner = fileURLToPath(new URL(
        this.v4 ? './_vitest4_runner.mjs' : './_vitest_runner.mjs', import.meta.url));
    }
  }

  harnessInputs(ctx) {
    const root = process.env.DV_VITEST_ROOT;
    const rootProject = ctx.getRootProject();
    return [...new Set([rootProject, ...ctx.projects])].map(project => {
      const vite = project.vite.config;
      if (!Array.isArray(vite.configFileDependencies) ||
        !Array.isArray(project.config.setupFiles) || typeof vite.mode !== 'string') {
        throw new Error('dv: missing resolved Vitest harness inputs');
      }
      const paths = new Set([...vite.configFileDependencies, ...project.config.setupFiles]);
      if (vite.configFile) paths.add(vite.configFile);
      if (ctx._workspaceConfigPath) paths.add(ctx._workspaceConfigPath);
      const seen = new Set();
      function visit(module) {
        if (!module || seen.has(module)) return;
        seen.add(module);
        if (module.file) paths.add(module.file);
        for (const imported of module.importedModules) visit(imported);
      }
      for (const filename of [...project.config.setupFiles, ctx._workspaceConfigPath]) {
        if (!filename) continue;
        const graphs = new Set([project.vite, ctx.vite].flatMap(server =>
          [server.moduleGraph, ...Object.values(server.environments || {})
            .map(environment => environment.moduleGraph)]).filter(Boolean));
        if (!graphs.size) throw new Error('dv: missing Vite environment module graph');
        for (const graph of graphs) {
          if (typeof graph.getModulesByFile !== 'function') {
            throw new Error('dv: unsupported Vite environment module graph');
          }
          for (const module of graph.getModulesByFile(filename) || []) visit(module);
        }
      }
      if (vite.envDir !== false) {
        for (const name of ['.env', '.env.local', `.env.${vite.mode}`, `.env.${vite.mode}.local`]) {
          paths.add(resolve(vite.envDir, name));
        }
      }
      const inputs = [];
      for (const filename of [...paths].sort()) {
        const absolute = resolve(filename);
        let path = relative(root, absolute).split('\\').join('/');
        const external = isAbsolute(path) || path === '..' || path.startsWith('../');
        if (external) {
          const home = relative(homedir(), absolute);
          const prefix = isAbsolute(home) || home === '..' || home.startsWith('../')
            ? '<absolute>' : '<home>';
          const identity = createHash('sha256').update(absolute).digest('hex');
          path = `${prefix}/${identity}/${basename(absolute)}`;
        }
        let sha256 = null;
        try {
          sha256 = createHash('sha256').update(readFileSync(filename)).digest('hex');
        } catch (error) {
          if (error.code !== 'ENOENT') throw error;
        }
        inputs.push({ path, sha256, external });
      }
      return { project: project.name, root: relative(root, project.config.root) || '.',
        kind: project === rootProject ? 'root' : 'project', inputs,
        configHashes: configHashes(project.config, vite, root) };
    });
  }

  onTestRunEnd(modules, unhandledErrors, reason) {
    if (this.record.complete) this.invalidate(new Error('multiple test runs'));
    const harness = this.harnessInputs(this.ctx);
    for (let index = 0; index < this.harness.length; index++) {
      for (const input of this.harness[index].inputs) {
        if (!harness[index].inputs.some(current =>
          current.path === input.path && current.sha256 === input.sha256)) {
          this.invalidate(new Error(`harness input changed during execution: ${input.path}`));
        }
      }
    }
    this.record = {
      complete: true,
      reason,
      harness,
      unhandledErrors: errors(unhandledErrors),
      files: modules.map(module => taskRecord(module.task)),
    };
  }
}
