"""Parse a unified diff and class each file from repo path rules."""

from __future__ import annotations

from triage.config import PathClasses
from triage.schemas.bundle import FileChange, FileKind


def parse_unified_diff(text: str, paths: PathClasses | None = None) -> list[FileChange]:
    """Turn a unified diff into classed file changes, merging repeated paths."""
    rules = paths or PathClasses()
    current: str | None = None
    added = 0
    removed = 0
    order: list[str] = []
    counts: dict[str, list[int]] = {}

    def flush() -> None:
        """Store the file currently being counted."""
        nonlocal current, added, removed
        if current:
            if current not in counts:
                order.append(current)
                counts[current] = [0, 0]
            counts[current][0] += added
            counts[current][1] += removed
        current = None
        added = 0
        removed = 0

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            continue
        if line.startswith(("+++ b/", "+++ a/", "+++ /dev/null")):
            flush()
            current = _diff_path(line[4:]) or None
            continue
        if current is None or line.startswith("--- ") or line.startswith("\\"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    flush()
    return [
        FileChange(
            path=path,
            kind=classify_path(path, rules),
            added_lines=counts[path][0],
            removed_lines=counts[path][1],
        )
        for path in order
    ]


def classify_path(path: str, paths: PathClasses | None = None) -> FileKind:
    """Return the longest matching path class, or other when nothing matches."""
    rules = paths or PathClasses()
    normal = path.replace("\\", "/").lstrip("./")
    best_len = -1
    best: FileKind = "other"
    table: tuple[tuple[FileKind, list[str]], ...] = (
        ("product_ui", rules.product_ui),
        ("product", rules.product),
        ("test", rules.test),
        ("config", rules.config),
        ("infra", rules.infra),
        ("dependency", rules.dependency),
    )
    for kind, prefixes in table:
        for prefix in prefixes:
            if _matches(normal, prefix) and len(prefix) > best_len:
                best_len = len(prefix)
                best = kind
    return best


def _matches(path: str, prefix: str) -> bool:
    """Return whether path equals prefix or sits inside a directory prefix."""
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _diff_path(raw: str) -> str:
    """Normalise a --- or +++ path and drop /dev/null."""
    path = raw.strip().split("\t", 1)[0].strip('"')
    if path.startswith(("a/", "b/")):
        path = path[2:]
    if path in {"/dev/null", "dev/null"}:
        return ""
    return path
