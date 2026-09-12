#!/usr/bin/env python3
"""Cheap structural guardrails that keep the repository AI-navigable."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
WARNINGS: list[str] = []


def check_python(path: Path) -> None:
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if len(lines) > 600:
        ERRORS.append(f"{rel}: {len(lines)} lines (>600 hard limit)")
    elif len(lines) > 450:
        WARNINGS.append(f"{rel}: {len(lines)} lines; consider another owner split")

    tree = ast.parse(text, filename=rel)

    # Regression guard for helpers extracted from mixin methods. A module-level
    # helper must receive its application object explicitly (usually ``app``);
    # a leftover ``self`` survives parsing and otherwise crashes only at runtime.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arg_names = {
                arg.arg
                for arg in (
                    list(getattr(node.args, "posonlyargs", []))
                    + list(node.args.args)
                    + list(node.args.kwonlyargs)
                )
            }
            uses_self = any(
                isinstance(child, ast.Name) and child.id == "self"
                for child in ast.walk(node)
            )
            if uses_self and "self" not in arg_names:
                ERRORS.append(
                    f"{rel}:{node.lineno}: module helper {node.name}() uses self without a self parameter"
                )

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            ERRORS.append(f"{rel}:{node.lineno}: wildcard import is forbidden")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
            size = node.end_lineno - node.lineno + 1
            if size > 350:
                WARNINGS.append(f"{rel}:{node.lineno} {node.name}() is {size} lines")

    if rel != "src/core/runtime.py" and "src.core.runtime" in text:
        ERRORS.append(f"{rel}: import owner module directly; do not depend on core.runtime")


def main() -> int:
    for path in sorted((ROOT / "src").rglob("*.py")):
        check_python(path)
    for path in sorted((ROOT / "tests").rglob("*.py")):
        check_python(path)

    agents = [ROOT / "AGENTS.md", *sorted((ROOT / "src").glob("*/AGENTS.md"))]
    for path in agents:
        if path.exists() and len(path.read_text(encoding="utf-8").splitlines()) > 80:
            WARNINGS.append(f"{path.relative_to(ROOT)}: AGENTS.md >80 lines; keep it as a router")

    print(f"errors={len(ERRORS)} warnings={len(WARNINGS)}")
    for item in ERRORS:
        print(f"ERROR: {item}")
    for item in WARNINGS:
        print(f"WARN: {item}")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
