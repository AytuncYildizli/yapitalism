#!/usr/bin/env python3
"""Validate server.json against the registry's published schema.

Written after a publish failed on `expected length <= 100` for description: the
constraint was in the schema all along and the first check only looked at
required fields and the name pattern. This checks every constraint the schema
states, so the next omission fails here instead of after a version has already
been burned on PyPI.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def main() -> int:
    doc = json.load(open("server.json"))
    with urllib.request.urlopen(SCHEMA, timeout=30) as r:
        schema = json.load(r)
    defs = schema["definitions"]
    errs: list[str] = []

    def check(obj: dict, spec: dict, where: str) -> None:
        for key in spec.get("required", []):
            if key not in obj:
                errs.append(f"{where}: missing required {key!r}")
        allowed = set(spec.get("properties", {}))
        for key in set(obj) - allowed - {"$schema", "_meta"}:
            errs.append(f"{where}: unknown key {key!r}")
        for key, rule in spec.get("properties", {}).items():
            if key not in obj:
                continue
            value = obj[key]
            if isinstance(value, str):
                if "maxLength" in rule and len(value) > rule["maxLength"]:
                    errs.append(
                        f"{where}.{key}: {len(value)} chars, max {rule['maxLength']}"
                    )
                if "minLength" in rule and len(value) < rule["minLength"]:
                    errs.append(f"{where}.{key}: shorter than {rule['minLength']}")
                if "pattern" in rule and not re.match(rule["pattern"], value):
                    errs.append(f"{where}.{key}: fails {rule['pattern']}")
            if "enum" in rule and value not in rule["enum"]:
                errs.append(f"{where}.{key}: {value!r} not in {rule['enum']}")

    check(doc, defs["ServerDetail"], "server")
    for i, pkg in enumerate(doc.get("packages", [])):
        check(pkg, defs["Package"], f"packages[{i}]")

    # Cross-file agreement: three places carry the version, and the registry
    # verifies ownership by matching this exact name in the PyPI description.
    pyproject = open("pyproject.toml").read()
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    if doc["version"] != version:
        errs.append(f"server.json {doc['version']} != pyproject {version}")
    for i, pkg in enumerate(doc.get("packages", [])):
        if pkg.get("version") != version:
            errs.append(f"packages[{i}].version != pyproject {version}")
    marker = re.search(r"mcp-name:\s*(\S+)", open("README.md").read())
    if not marker:
        errs.append("README has no 'mcp-name:' ownership marker")
    elif marker.group(1) != doc["name"]:
        errs.append(f"README marker {marker.group(1)!r} != name {doc['name']!r}")

    for e in errs:
        print(f"  {e}")
    print("  OK" if not errs else f"  {len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
