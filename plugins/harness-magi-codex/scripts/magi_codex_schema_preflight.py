#!/usr/bin/env python3
"""Fail closed on JSON-Schema constructs unsupported by Codex structured output."""

import json
import sys
from pathlib import Path


UNSUPPORTED = {
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: magi_codex_schema_preflight.py <schema>", file=sys.stderr)
        return 64
    path = Path(sys.argv[1])
    try:
        schema = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"fanout: Codex output schema is unreadable or invalid: {exc}", file=sys.stderr)
        return 64
    if not isinstance(schema, dict) or schema.get("type") != "object":
        print(
            "fanout: Codex output schema is provider-incompatible: "
            "$ root type must be object",
            file=sys.stderr,
        )
        return 64

    found = []

    def visit(value, location="$"):
        if isinstance(value, dict):
            properties = value.get("properties")
            if value.get("type") == "object" or isinstance(properties, dict):
                if value.get("additionalProperties") is not False:
                    found.append(f"{location}.additionalProperties must be false")
            if isinstance(properties, dict):
                required = value.get("required")
                missing = (
                    sorted(set(properties) - set(required))
                    if isinstance(required, list)
                    else sorted(properties)
                )
                if missing:
                    found.append(
                        f"{location}.required missing provider-required properties "
                        + ",".join(missing)
                    )
            for key, child in value.items():
                if key in UNSUPPORTED:
                    found.append(f"{location}.{key}")
                if key == "properties" and isinstance(child, dict):
                    for property_name, property_schema in child.items():
                        visit(
                            property_schema,
                            f"{location}.properties[{property_name!r}]",
                        )
                else:
                    visit(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{location}[{index}]")

    visit(schema)
    if found:
        print(
            "fanout: Codex output schema is provider-incompatible: " + "; ".join(found),
            file=sys.stderr,
        )
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
