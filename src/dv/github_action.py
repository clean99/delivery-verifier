"""GitHub Actions adapter; network and installation stay outside the dv CLI."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def install_source(version: str, action_path: Path) -> str:
    if version == "local":
        return str(action_path.resolve())
    if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version):
        raise ValueError("version must be a release version such as 0.2.0, v0.2.0, or local")
    version = version.removeprefix("v")
    return (
        "https://github.com/clean99/delivery-verifier/releases/download/"
        f"v{version}/delivery_verifier-{version}-py3-none-any.whl"
    )


def install(source: str, temporary: Path, log) -> Path:
    tooling = temporary / "installer"
    subprocess.run([sys.executable, "-I", "-m", "venv", str(tooling)], check=True,
                   stdout=log, stderr=log)
    python = tooling / "bin/python"
    subprocess.run([str(python), "-I", "-m", "pip", "install", "pipx==1.7.1"],
                   check=True, stdout=log, stderr=log)
    env = dict(os.environ, PIPX_HOME=str(temporary / "pipx"),
               PIPX_BIN_DIR=str(temporary / "bin"), PIPX_MAN_DIR=str(temporary / "man"))
    subprocess.run([str(python), "-I", "-m", "pipx", "install", "--python", sys.executable,
                    source], env=env, check=True, stdout=log, stderr=log)
    return temporary / "bin/dv"


def git(repo: Path, *args: str) -> str:
    command = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if command.returncode:
        raise ValueError(f"git {args[0]} failed: {command.stderr.strip()}")
    return command.stdout.strip()


def fetch_base(repo: Path, base: str, default: str) -> str:
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ValueError("working-directory must be a repository root; tests run from that root")
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("A full checkout is required; set actions/checkout fetch-depth: 0")
    branch = base or default
    if not branch:
        raise ValueError("No default branch is available; set the base input")
    if branch.startswith("refs/") and not branch.startswith("refs/heads/"):
        raise ValueError("base must name an origin branch; only refs/heads/ refs are supported")
    branch = branch.removeprefix("refs/heads/")
    if branch.startswith("-"):
        raise ValueError("base branch name must not begin with '-'")
    ref = f"refs/heads/{branch}"
    git(repo, "check-ref-format", ref)
    try:
        git(repo, "fetch", "--no-tags", "--no-recurse-submodules", "--", "origin", ref)
    except ValueError as error:
        raise ValueError(
            f"Cannot fetch base branch {ref} from origin; "
            "check that the branch exists and origin is accessible. " + str(error)
        ) from error
    return git(repo, "rev-parse", "--verify", "FETCH_HEAD^{commit}")


def summarize(proof: object, exit_code: int) -> dict:
    if not isinstance(proof, dict):
        raise ValueError("dv did not return a JSON object")
    checks = proof.get("checks", [])
    if not isinstance(checks, list) or any(
        not isinstance(c, dict) or c.get("outcome") not in ("pass", "warn", "fail")
        for c in checks
    ):
        raise ValueError("dv returned invalid checks")
    phases = {}
    qualified = True
    for name, check_name, expected_code in (
        ("red", "red assertion", 1), ("green", "green execution", 0)
    ):
        phase = proof.get(name)
        matching = [c for c in checks if c.get("check") == check_name]
        accepted = len(matching) == 1 and matching[0]["outcome"] == "pass"
        if isinstance(phase, dict):
            phases[name] = (
                f"{'pass' if accepted else 'fail'} "
                f"(exit {phase.get('exit_code')}, {phase.get('status')})"
            )
            qualified &= (
                accepted and phase.get("status") == "normal"
                and type(phase.get("exit_code")) is int
                and phase["exit_code"] == expected_code
                and phase.get("report_complete") is True
            )
        else:
            phases[name] = "not run"
            qualified = False
    failures = [str(c.get("detail", c.get("check", "Failed check")))
                for c in checks if c["outcome"] == "fail"]
    ok = (
        exit_code == 0 and proof.get("schema_version") == 1
        and proof.get("command") == "prove" and proof.get("ok") is True
        and proof.get("state") == "complete" and type(proof.get("exit_code")) is int
        and proof["exit_code"] == 0 and qualified and not failures and not proof.get("error")
    )
    error = proof.get("error")
    if isinstance(error, dict) and error.get("message"):
        failures.insert(0, str(error["message"]))
    reason = (
        "Assertion-only red failure and executed green pass."
        if ok else "; ".join(failures) or f"Incomplete or inconsistent proof (dv exit {exit_code})."
    )
    return {"ok": ok, "verdict": "PASS" if ok else "FAIL", **phases, "reason": reason}


def collect_evidence(proof: dict, repo: Path, destination: Path) -> None:
    directory = proof.get("evidence_dir")
    if not isinstance(directory, str):
        raise ValueError("dv did not provide an evidence directory")
    source = Path(directory)
    expected = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "dv/proofs"))
    if (source.is_symlink() or source.parent.resolve() != expected.resolve()
            or not re.fullmatch(r"[0-9a-f]{32}", source.name)):
        raise ValueError("dv returned an unexpected evidence directory")
    for path in source.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Evidence must contain regular files only")
        shutil.copyfile(path, destination / path.name)
    saved = json.loads((destination / "proof.json").read_text(encoding="utf-8"))
    if saved != proof:
        raise ValueError("Saved proof differs from dv's JSON output")
    if proof.get("ok") is True and not all(
        (destination / name).is_file() for name in ("red.log", "green.log")
    ):
        raise ValueError("Passing proof is missing red/green logs")


def publish(report: dict, evidence: Path, output: Path, summary: Path, artifact_id: str) -> None:
    values = {"ok": str(report["ok"]).lower(), "verdict": report["verdict"],
              "evidence-path": str(evidence), "artifact-id": artifact_id}
    with output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            delimiter = uuid.uuid4().hex
            stream.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
    with summary.open("a", encoding="utf-8") as stream:
        stream.write(
            f"## dv prove: {report['verdict']}\n\n"
            f"- Red: {html.escape(report['red'])}\n"
            f"- Green: {html.escape(report['green'])}\n\n"
            f"<pre>{html.escape(report['reason'][:2000])}</pre>\n"
        )
    (evidence / "action-result.json").write_text(json.dumps(report, indent=2) + "\n",
                                               encoding="utf-8")


def main() -> None:
    env = os.environ
    evidence = Path(tempfile.mkdtemp(prefix="dv-evidence-", dir=env["RUNNER_TEMP"]))
    report = {"ok": False, "verdict": "FAIL", "red": "not run", "green": "not run",
              "reason": "Proof did not complete."}
    try:
        test = env["DV_TEST"]
        timeout = env["DV_TIMEOUT"]
        if not test.strip():
            raise ValueError("test must be a nonblank command")
        if not re.fullmatch(r"[0-9]+", timeout) or int(timeout) < 1:
            raise ValueError("timeout must be a positive integer in seconds")
        repo = (Path(env["GITHUB_WORKSPACE"]) / env["DV_WORKING_DIRECTORY"]).resolve()
        source = install_source(env["DV_VERSION"], Path(env["DV_ACTION_PATH"]))
        base = fetch_base(repo, env.get("DV_BASE", ""), env.get("DV_DEFAULT_BASE", ""))
        with tempfile.TemporaryDirectory(prefix="dv-tools-", dir=env["RUNNER_TEMP"]) as temp:
            with (evidence / "install.log").open("wb") as log:
                dv = install(source, Path(temp), log)
            with (evidence / "stdout.json").open("wb") as stdout, (
                evidence / "stderr.log"
            ).open("wb") as stderr:
                command = subprocess.run(
                    [str(dv), "prove", "--test", test, "--base", base,
                     "--timeout", timeout, "--json"], cwd=repo, stdout=stdout, stderr=stderr,
                )
            proof = json.loads((evidence / "stdout.json").read_text(encoding="utf-8"))
            report = summarize(proof, command.returncode)
            if proof.get("evidence_dir"):
                collect_evidence(proof, repo, evidence)
            elif report["ok"]:
                raise ValueError("Passing proof is missing evidence")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report.update(ok=False, verdict="FAIL", reason=str(error))
    publish(report, evidence, Path(env["GITHUB_OUTPUT"]), Path(env["GITHUB_STEP_SUMMARY"]),
            evidence.name.removeprefix("dv-evidence-"))


if __name__ == "__main__":
    main()
