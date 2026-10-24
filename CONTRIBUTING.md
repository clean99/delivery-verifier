# Contributing

Keep delivery-verifier a small, local verification tool. Changes should improve the evidence for a code change, not add repository discovery, contribution tracking, model orchestration, or publishing workflows.

## Local setup

Use Python 3.10+ and Git 2.38+ on Linux or macOS. Runtime code uses the standard library only. The test suite uses `unittest`; Ruff is development tooling.

From the checkout root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
ruff check .
python examples/demo/run.py
```

Use `python3` if that is your Python executable. Tests and the demo use disposable local repositories, synthetic Git identities, and no network services. They need a writable checkout for their temporary directories. The demo records a synthetic review fixture; it deliberately leaves human approval absent.

For an installed CLI, `pipx install .` or `uv tool install .` installs this source checkout. For development in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e . ruff==0.17.1
```

Installation can download build/development dependencies. In an offline environment, use already installed tools or a local wheel cache; the source-level test and demo commands do not need a package install. If Ruff is unavailable offline, run `python -m compileall -q src tests examples` and report it as syntax validation, not as a completed Ruff check. CI runs Ruff and tests on Python 3.10–3.13.

## Changes and tests

- Reproduce a bug with a test that fails before the fix and passes afterward. Check the failure reason; an import or setup error is not behavioral evidence.
- Keep policy decisions and classification logic separately testable. Use real temporary Git repositories for worktree, index, restoration, and evidence-binding behavior.
- Cover negative cases: stale commits, changed inputs, malformed records, weak tests, missing executables, and cleanup failures must not become passes.
- Keep commands as argument arrays. Do not add implicit shell execution, dependency installation, telemetry, or network calls.
- Preserve unrelated files and configuration. Report exceptions and warnings instead of lowering the gate silently.
- Use synthetic paths, repository names, identities, and fixtures. Never add real credentials, private logs, customer data, or machine-specific paths.
- Use types where they clarify a contract. Comments should explain a constraint or reason that the code cannot express.

Maintain the English and Chinese READMEs when a command or guarantee changes. Update `docs/design.md` for evidence/configuration contracts and `CHANGELOG.md` for user-visible changes. Keep the bundled parser's license and notice intact.

## Review

Explain the behavior changed, the regression reproduced, and the exact test and lint commands you ran. Call out checks you did not run and any compatibility limitations. A separate reviewer should inspect the patch and red/green logs without inheriting the implementer's reasoning; that reviewer should not edit the change.

Agent-assisted contributions have the same evidence requirements as other changes. You are responsible for the patch and for following applicable disclosure policies. Never substitute an automated approval record for an actual person's decision.

For security problems, follow [SECURITY.md](SECURITY.md) rather than posting exploit details in a public issue.

## License

Contributions are provided under [Apache-2.0](LICENSE). Retain applicable third-party notices and licenses.
