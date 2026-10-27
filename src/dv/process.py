"""Run trusted local argument arrays and reap the process group on interruption."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from dv.common import DVError, digest


def run(
    command: list[str], cwd: Path, log: Path, timeout: int, *, env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> dict:
    if os.name != "posix":
        raise DVError("Safe process-group cleanup requires POSIX", 3, "unsupported_platform")
    if log.is_symlink():
        raise DVError(f"Refusing symlinked log: {log}")
    log.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dv-log-", dir=log.parent)
    proc = None
    status, code = "launch_error", None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                    pass_fds=pass_fds,
                )
            except OSError as error:
                stream.write(f"Cannot launch command: {error}\n".encode())
            else:
                try:
                    code = proc.wait(timeout=timeout)
                    status = "normal" if code >= 0 else "signal"
                except subprocess.TimeoutExpired:
                    status = "timeout"
                    stream.write(f"\nTimed out after {timeout}s\n".encode())
                finally:
                    # Descendants may outlive the command's direct child.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
    finally:
        os.replace(temporary, log)
    return {
        "argv": command,
        "exit_code": code,
        "status": status,
        "timeout_seconds": timeout,
        "log_sha256": digest(log.read_bytes()),
    }
