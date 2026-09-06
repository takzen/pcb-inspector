"""S-Expression parser and AST utilities for KiCad file formats (.kicad_pcb, .kicad_sch)."""

from __future__ import annotations

import re
from typing import Any

from pcb_inspector.core.exceptions import ProjectParsingError

# Regex tokenizer matching tokens:
# 1. Quoted strings with escaped characters
# 2. Open / close parentheses
# 3. Atoms / unquoted tokens (symbols, numbers)
TOKEN_RE = re.compile(r'("(?:\\.|[^"\\])*"|[()]|[^\s()]+)')


def tokenize(text: str) -> list[str]:
    """Tokenize raw S-expression text into an array of string tokens."""
    return TOKEN_RE.findall(text)


def parse_sexpr(text: str) -> list[Any]:
    """Parse an S-expression string into nested Python lists.

    Example:
        `"(kicad_pcb (version 20240108))"` -> `["kicad_pcb", ["version", "20240108"]]`
    """
    tokens = tokenize(text)
    if not tokens:
        return []

    stack: list[list[Any]] = []
    current: list[Any] = []

    for token in tokens:
        if token == "(":
            new_list: list[Any] = []
            if stack:
                stack[-1].append(new_list)
            else:
                current.append(new_list)
            stack.append(new_list)
        elif token == ")":
            if not stack:
                raise ProjectParsingError("Mismatched closing parenthesis in S-expression.")
            stack.pop()
        else:
            # Unescape quoted string if needed
            val: str = token
            if token.startswith('"') and token.endswith('"'):
                val = token[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
            if stack:
                stack[-1].append(val)
            else:
                current.append(val)

    if stack:
        raise ProjectParsingError(
            f"Unclosed parentheses in S-expression: {len(stack)} open levels remaining."
        )

    return current[0] if len(current) == 1 and isinstance(current[0], list) else current


def find_first(node: list[Any] | Any, tag: str) -> list[Any] | None:
    """Find the first immediate child list starting with `tag`."""
    if not isinstance(node, list):
        return None
    for item in node:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def find_all(node: list[Any] | Any, tag: str) -> list[list[Any]]:
    """Find all immediate child lists starting with `tag`."""
    if not isinstance(node, list):
        return []
    return [item for item in node if isinstance(item, list) and item and item[0] == tag]


def get_value(node: list[Any] | None, index: int = 1, default: Any = None) -> Any:
    """Get the value at a specific index from a node list if present."""
    if node is None or not isinstance(node, list) or len(node) <= index:
        return default
    return node[index]
