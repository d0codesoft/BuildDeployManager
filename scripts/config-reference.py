#!/usr/bin/env python3
"""Generate a field inventory directly from the typed configuration model."""

from dataclasses import MISSING, fields
from pathlib import Path
from typing import get_type_hints

import scdeploy.config as schema


def main() -> None:
    """Keep documented types, defaults and required fields aligned with the code."""
    lines = ["# Schema 1 field reference", "", "Generated from src/scdeploy/config.py. See configuration.md for semantics.", ""]
    for name in ["Config", "Project", "Source", "BuildProject", "Build", "Deploy", "Healthcheck", "Rollback", "Updates", "Notification", "Notifications"]:
        cls = getattr(schema, name)
        hints = get_type_hints(cls)
        lines += [f"## {name}", "", "| Field | Type | Default |", "| --- | --- | --- |"]
        for item in fields(cls):
            if item.default is not MISSING:
                default = repr(item.default)
            elif item.default_factory is not MISSING:
                default = item.default_factory.__name__ + "()"
            else:
                default = "required"
            typed = str(hints[item.name]).replace("typing.", "").replace("scdeploy.config.", "").replace("|", "\\|")
            lines.append(f"| {item.name} | `{typed}` | `{default}` |")
        lines.append("")
    (Path(__file__).resolve().parents[1] / "docs/schema-reference.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
