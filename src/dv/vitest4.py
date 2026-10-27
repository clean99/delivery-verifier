"""Local selection and exact-source instrumentation of phase-owned Vitest copies."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from dv.common import DVError


def node_json(script: str, *args: str, cwd: Path) -> dict:
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, *args], cwd=cwd,
            capture_output=True, text=True, check=True, timeout=30,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise DVError("Cannot prepare local Vitest tooling: " + detail,
                      2, "unsupported_runner") from error


def select(command: list[str], cwd: Path) -> dict:
    executable = Path(command[0])
    if executable.name in ("npx", "pnpm"):
        anchor = cwd / "package.json"
    else:
        candidate = shutil.which(command[0]) if "/" not in command[0] else command[0]
        anchor = (cwd / (candidate or command[0])).resolve()
    return node_json(
        "import {createRequire} from 'node:module';"
        "import {readFileSync,realpathSync} from 'node:fs';"
        "const r=createRequire(process.argv[1]);"
        "const path=realpathSync(r.resolve('vitest/package.json'));"
        "console.log(JSON.stringify({path,version:JSON.parse(readFileSync(path)).version}));",
        str(anchor), cwd=cwd,
    )


def validate_options(command: list[str], version: str) -> None:
    if version == "4.1.12" and any(arg.split("=", 1)[0] == "--minWorkers" for arg in command):
        raise DVError("Vitest 4.1.12 does not support --minWorkers; remove it and use "
                      "--maxWorkers if needed", 2, "unsupported_runner_option")


def prepare(selection: dict, cwd: Path, temporary: Path, *, phase_owned: bool) -> dict:
    # Never patch the caller's installed dependencies, even for direct run_test callers.
    package = Path(selection["path"])
    owned_root = cwd.resolve()
    if not (phase_owned and package.is_relative_to(owned_root)):
        from dv.snapshots import DependencyCopier

        modules = next((parent for parent in package.parents if parent.name == "node_modules"
                        and parent.parent.name != ".pnpm"), None)
        if modules is None:
            raise DVError("Vitest 4.1.12 needs a local npm or pnpm dependency tree")
        # pnpm's package path has an inner node_modules; copy the outer store as a unit.
        for parent in modules.parents:
            if parent.name == "node_modules":
                modules = parent
        target = temporary / "dependencies" / "node_modules"
        target.parent.mkdir()
        copier = DependencyCopier(target.parent, [modules])
        shutil.copytree(modules, target, symlinks=True, copy_function=copier,
                        ignore=shutil.ignore_patterns(*copier.excluded))
        for path in target.rglob("*"):
            if not path.is_symlink():
                continue
            original = (modules / path.relative_to(target)).resolve()
            if not original.is_relative_to(modules):
                raise DVError("Vitest dependency link leaves the selected dependency tree")
            path.unlink()
            path.symlink_to(os.path.relpath(target / original.relative_to(modules), path.parent))
        package = target / package.relative_to(modules)
        owned_root = target
    probe = temporary / "node_modules" / "dv-probe" / "_vitest4_sources.mjs"
    manifest = node_json(
        "import {pathToFileURL} from 'node:url';"
        "const {selectedSources}=await import(pathToFileURL(process.argv[1]));"
        "console.log(JSON.stringify(selectedSources(process.argv[2],process.argv[3])));",
        str(probe), str(package), str(owned_root), cwd=cwd,
    )
    path = temporary / "sources.json"
    path.write_text(json.dumps(manifest))
    return {"package": str(package), "manifest": str(path),
            "cli": str(package.parent / "vitest.mjs")}
