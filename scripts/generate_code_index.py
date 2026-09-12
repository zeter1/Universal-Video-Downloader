#!/usr/bin/env python3
"""Regenerate a compact owner/symbol index for Codex."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "CODE_INDEX.md"


def symbols(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    result: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(f"{node.name}@{node.lineno}")
        elif isinstance(node, ast.ClassDef):
            methods = [
                f"{child.name}@{child.lineno}"
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            result.append(f"{node.name}@{node.lineno}")
            result.extend(f"{node.name}.{item}" for item in methods)
    return result


def main() -> int:
    lines = [
        "# CODE INDEX — generated, compact\n\n",
        "> Генерируется `python scripts/generate_code_index.py`. Для обычной задачи используйте `scripts/codex_scope.py`.\n\n",
    ]
    for path in sorted((ROOT / "src").rglob("*.py")):
        items = symbols(path)
        if not items:
            continue
        rel = path.relative_to(ROOT).as_posix()
        lines.append(f"- `{rel}` — " + "; ".join(f"`{item}`" for item in items) + "\n")
    OUT.write_text("".join(lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
