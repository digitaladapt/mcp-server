"""CLI tool to validate registry configuration.

Usage:
    python -m app.validate [registry_dir]

Exit codes:
    0 – all files valid (warnings are OK)
    1 – one or more files have errors
    2 – registry directory does not exist
"""

from __future__ import annotations

import sys
from pathlib import Path

from .registry import REGISTRY_DIR, validate_registry


def _format_result(result) -> str:
    """Format a ValidationResult as a human-readable report."""
    lines: list[str] = []

    if result.total == 0:
        lines.append("Registry directory is empty — no files to validate.")
        return "\n".join(lines)

    for issue in result.issues:
        symbol = {
            "ok": "✓",
            "warning": "⚠",
            "error": "✗",
        }.get(issue.status, "?")

        if issue.status == "ok":
            lines.append(f"  {symbol} {issue.file} → {issue.command}")
        elif issue.status == "warning":
            lines.append(f"  {symbol} {issue.file} → {issue.command}: {issue.message}")
        else:
            cmd = f" [{issue.command}]" if issue.command else ""
            lines.append(f"  {symbol} {issue.file}{cmd}: {issue.message}")

    lines.append("")
    parts = [f"{result.total} file(s) checked"]
    if result.errors:
        parts.append(f"{result.errors} error(s)")
    if result.warnings:
        parts.append(f"{result.warnings} warning(s)")
    lines.append(f"  {' · '.join(parts)}")

    lines.append("")
    if result.valid:
        if result.warnings:
            lines.append("  Registry is valid (with warnings).")
        else:
            lines.append("  Registry is valid.")
    else:
        lines.append("  Registry has errors — fix them before restarting.")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if argv:
        registry_dir = Path(argv[0])
    else:
        registry_dir = REGISTRY_DIR

    result = validate_registry(registry_dir)

    print(f"MCP Server registry validation: {registry_dir}")
    print()
    print(_format_result(result))

    # Exit 2 if the directory itself doesn't exist.
    if not registry_dir.is_dir():
        return 2
    # Exit 1 if there are any errors.
    if not result.valid:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
