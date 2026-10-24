"""Validated repository-only configuration."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from dv.common import DVError, argv, keys, nonblank

try:
    import tomllib
except ModuleNotFoundError:
    from dv._vendor import tomllib

DEFAULTS = {
    "schema_version": 1,
    "static_commands": [],
    "test_commands": [],
    "timeout_seconds": 1800,
    "generated_paths": [],
    "allowed_prose_lines": [],
    "allowed_code_lines": [],
    "limits": {
        "warn_source_lines": 200,
        "max_source_lines": 400,
        "max_source_lines_large": 900,
        "warn_test_lines": 1200,
        "max_soft_tell_hits": 3,
    },
    "hooks": {"items": [".dv/item.json"], "max_stop_blocks": 3, "allow_commands": []},
}


def relative(value: Any, label: str) -> str:
    value = nonblank(value, label)
    if Path(value).is_absolute() or ".." in Path(value).parts or ".git" in Path(value).parts:
        raise DVError(f"{label} must be a repository-relative path without traversal: {value}")
    return value


def merge(left: dict, right: dict) -> dict:
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = value
    return left


def validate(config: dict) -> dict:
    keys(config, set(DEFAULTS), "configuration")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise DVError("Configuration schema_version must be 1")
    for name in ("static_commands", "test_commands"):
        if not isinstance(config[name], list):
            raise DVError(f"{name} must be an array of argument arrays")
        for command in config[name]:
            argv(command, name)
    for name in ("generated_paths", "allowed_prose_lines", "allowed_code_lines"):
        if not isinstance(config[name], list):
            raise DVError(f"{name} must be an array of strings")
        for value in config[name]:
            nonblank(value, name)
            if name == "generated_paths":
                relative(value, name)
    limits = keys(config["limits"], set(DEFAULTS["limits"]), "limits")
    for name, value in {"timeout_seconds": config["timeout_seconds"], **limits}.items():
        if type(value) is not int or value <= 0:
            raise DVError(f"{name} must be a positive integer")
    if limits["max_source_lines_large"] < limits["max_source_lines"]:
        raise DVError("Large source cap must not be smaller than small cap")
    hooks = keys(config["hooks"], set(DEFAULTS["hooks"]), "hooks")
    if type(hooks["max_stop_blocks"]) is not int or hooks["max_stop_blocks"] < 0:
        raise DVError("max_stop_blocks must be a nonnegative integer")
    for name in ("items", "allow_commands"):
        if not isinstance(hooks[name], list) or (name == "items" and not hooks[name]):
            raise DVError(f"hooks.{name} must be a {'nonempty ' if name == 'items' else ''}array")
        for value in hooks[name]:
            relative(value, "hooks.items") if name == "items" else nonblank(value, name)
    return config


def load(root: Path) -> dict:
    config = copy.deepcopy(DEFAULTS)
    for filename, tables in (("pyproject.toml", ("tool", "dv")), (".dv.toml", ("dv",))):
        path = root / filename
        if not path.exists():
            continue
        if not path.resolve().is_relative_to(root):
            raise DVError(f"Configuration escapes repository: {filename}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise DVError(f"Malformed {filename}: {error}") from error
        for table in tables:
            if not isinstance(data, dict):
                raise DVError(f"{filename}: {'.'.join(tables)} must be a table")
            data = data.get(table, {})
        keys(data, set(DEFAULTS), filename)
        merge(config, data)
        validate(config)
    return validate(config)
