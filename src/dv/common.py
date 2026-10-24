"""Local records, diagnostics, and atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DVError(Exception):
    def __init__(self, message: str, exit_code: int = 2, code: str = "invalid_input"):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(text: str) -> Any:
    def invalid(value: str) -> None:
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)
    except (ValueError, RecursionError) as error:
        raise DVError(f"Malformed JSON: {error}") from error


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = decode_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise DVError(f"Cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise DVError(f"Expected JSON object: {path}")
    return value


def atomic_bytes(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise DVError(f"Refusing symlinked output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dv-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_bytes(path, canonical(value) + b"\n")


def row(check: str, outcome: str, detail: str, fix: str = "") -> dict[str, str]:
    result = {"check": check, "outcome": outcome, "detail": detail}
    if outcome == "fail":
        result["fix"] = fix or "Correct the finding and rerun this check."
    return result


def result(
    command: str, scope: dict[str, Any], checks: list[dict[str, str]], **extra: Any
) -> dict[str, Any]:
    return {
        **scope,
        "schema_version": 1,
        "at": now(),
        "command": command,
        "ok": all(r["outcome"] != "fail" for r in checks),
        "checks": checks,
        **extra,
    }


def nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise DVError(f"{label} must be a nonblank string without NUL characters")
    return value


def keys(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DVError(f"{label} must be an object/table")
    if value.keys() - allowed:
        raise DVError(f"Unknown {label} fields: {', '.join(sorted(value.keys() - allowed))}")
    return value


def argv(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DVError(f"{label} must be a nonempty argument array")
    for arg in value:
        nonblank(arg, label)
    return value
