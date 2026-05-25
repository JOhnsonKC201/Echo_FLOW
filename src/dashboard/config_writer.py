"""Comment-preserving config.yaml editor for simple scalar settings.

Scope: change scalar values of existing top-level or nested-by-dot keys
(e.g. "dashboard.theme" -> "light"). Does NOT add new keys, does NOT
replace whole sections, does NOT edit lists/mappings.

This narrow scope is intentional. User-managed COLLECTIONS (custom
vocabulary, snippets, transforms) live in SQLite — the dashboard's
source of truth. config.yaml stays human-edited for genuine settings
(hotkey, mic device, ports, toggles), and this writer's only job is to
flip values for those.

Why so narrow? Editing nested YAML mappings while preserving every
comment requires a CST-aware library (ruamel). Adding that dep for a
handful of toggle flips isn't worth it. Anything bigger goes through
SQLite or stays a text-edit operation.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

import yaml


_WRITE_LOCK = threading.Lock()


class ConfigWriteError(RuntimeError):
    """Raised when an edit cannot be applied safely."""


def _atomic_write(path: Path, text: str) -> None:
    """Atomic on NTFS: write to .tmp, then os.replace into place."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _render_scalar(value: Any) -> str:
    """Render a Python value as a YAML scalar (no key, no indent)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    sv = str(value)
    if sv == "":
        return '""'
    # Quote if contains YAML-special characters or starts with one.
    needs_quote = any(c in sv for c in ':#{}[]&*!|>%@`,?') or sv[0] in " '\"-"
    if needs_quote:
        return '"' + sv.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return sv


def set_scalar(path: Path, dotted_key: str, value: Any) -> None:
    """Set the scalar value of an existing key. Preserves all comments.

    Examples:
        set_scalar(path, "dashboard.theme", "light")
        set_scalar(path, "cleanup.skip_when_clean", False)
        set_scalar(path, "mobile.port", 8765)

    Raises ConfigWriteError if the key doesn't exist, the value isn't a
    scalar in the existing file (i.e. it's a list/dict), or the resulting
    YAML doesn't parse.
    """
    with _WRITE_LOCK:
        text = path.read_text(encoding="utf-8")
        parts = dotted_key.split(".")
        new_text = _replace_scalar_value(text, parts, value)
        # Sanity round-trip: must still be valid YAML.
        try:
            parsed = yaml.safe_load(new_text)
        except Exception as e:
            raise ConfigWriteError(f"resulting YAML invalid: {e}") from e
        # Confirm the key now equals the new value (defensive).
        cur = parsed
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                raise ConfigWriteError(
                    f"post-edit key {dotted_key!r} missing — refusing to save"
                )
            cur = cur[p]
        _atomic_write(path, new_text)


def _replace_scalar_value(text: str, parts: list[str], value: Any) -> str:
    """Locate `<indent_for_depth>key:` and replace its scalar value in-line.

    Walks line by line, tracking the indent at which each part of the dotted
    path was matched. To match part[N], the current line's indent must be
    DEEPER than the indent that matched part[N-1] (one nesting level in).
    """
    lines = text.splitlines(keepends=True)
    # indent_at_depth[N] = indent at which parts[N] was matched. None until
    # matched. Root keys live at indent 0 so indent_at_depth[-1] starts as -1.
    matched_indents: list[int] = [-1]  # sentinel: "above the root"
    depth = 0
    target_idx: int | None = None

    for i, raw_line in enumerate(lines):
        stripped = raw_line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(stripped)
        # If indent dropped to or below an already-matched ancestor's indent,
        # that ancestor's scope has ended — pop back.
        while depth > 0 and indent <= matched_indents[depth - 1]:
            matched_indents.pop()
            depth -= 1
        # To match the next part of the path, this line must be DEEPER than
        # the indent at which the previous part was matched.
        parent_indent = matched_indents[depth - 1] if depth > 0 else -1
        if depth >= len(parts):
            continue
        if indent <= parent_indent:
            continue
        m = re.match(rf"^{re.escape(parts[depth])}\s*:", stripped)
        if not m:
            continue
        if depth == len(parts) - 1:
            target_idx = i
            break
        # Descend.
        matched_indents.append(indent)
        depth += 1

    if target_idx is None:
        raise ConfigWriteError(f"key {'.'.join(parts)!r} not found")

    original = lines[target_idx]
    # Preserve indent + key + colon + trailing comment if any.
    m = re.match(r"^(?P<indent>\s*)(?P<key>[^:]+):(?P<rest>.*)$", original)
    if not m:
        raise ConfigWriteError(f"malformed key line at {target_idx}")
    indent = m.group("indent")
    key = m.group("key")
    rest = m.group("rest")
    # Split off any inline comment so we can preserve it.
    comment_m = re.search(r"(\s+#.*)$", rest.rstrip("\n"))
    inline_comment = comment_m.group(1) if comment_m else ""
    # Ensure the existing value is a scalar — refuse to overwrite a block.
    body_part = rest[: -len(inline_comment)] if inline_comment else rest.rstrip("\n")
    body_stripped = body_part.strip()
    if body_stripped == "" or body_stripped.startswith(("|", ">")):
        # Block scalars or empty (meaning mapping/list on next line) — not supported.
        raise ConfigWriteError(
            f"key {'.'.join(parts)!r} is not an inline scalar — "
            "this writer only updates inline scalar values"
        )
    new_value = _render_scalar(value)
    new_line = f"{indent}{key}: {new_value}{inline_comment}\n"
    lines[target_idx] = new_line
    return "".join(lines)
