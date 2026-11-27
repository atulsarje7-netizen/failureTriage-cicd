"""Map allow-listed bundle test ids to pytest or Playwright argv."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from triage.schemas.bundle import TestResult


@dataclass(frozen=True)
class RerunCommand:
    """One subprocess invocation that runs a single allow-listed test."""

    argv: list[str]
    cwd: Path
    test_id: str


def build_rerun_command(
    test: TestResult,
    *,
    repo_root: Path,
    ui_cwd: Path,
) -> RerunCommand:
    """Build a pytest or Playwright command for one bundle test record."""
    if test.layer == "api":
        return RerunCommand(
            argv=[sys.executable, "-m", "pytest", test.test_id, "-q", "--tb=no"],
            cwd=repo_root,
            test_id=test.test_id,
        )
    file_name = test.file or _file_from_ui_id(test.test_id)
    title = test.title or _title_from_ui_id(test.test_id)
    argv = ["npx", "playwright", "test"]
    if file_name:
        argv.append(file_name)
    if title:
        argv.extend(["-g", title])
    return RerunCommand(argv=argv, cwd=ui_cwd, test_id=test.test_id)


def _file_from_ui_id(test_id: str) -> str | None:
    """Pull the suite file from a `file > title` Playwright test id."""
    if " > " not in test_id:
        return None
    left, _right = test_id.split(" > ", 1)
    return left.strip() or None


def _title_from_ui_id(test_id: str) -> str | None:
    """Pull the spec title from a `file > title` Playwright test id."""
    if " > " not in test_id:
        return test_id.strip() or None
    _left, right = test_id.split(" > ", 1)
    return right.strip() or None
