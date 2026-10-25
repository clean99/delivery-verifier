"""Location-independent identity of the installed checker sources."""

from __future__ import annotations

from pathlib import Path

from dv.common import DVError, canonical, digest


def checker_digest(package: Path | None = None) -> str:
    root = package if package is not None else Path(__file__).resolve().parent
    sources = {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in sorted(root.rglob("*.py"))
    }
    if "__init__.py" not in sources or "checker.py" not in sources:
        raise DVError("Checker source files are missing; reinstall delivery-verifier", 3)
    return digest(canonical(sources))
