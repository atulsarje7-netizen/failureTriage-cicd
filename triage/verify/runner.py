"""Subprocess runner that only executes allow-listed rerun commands."""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from triage.schemas.bundle import EvidenceBundle, TestResult
from triage.schemas.verdict import RerunAttempt, RerunOutcome
from triage.verify.allowlist import assert_allowed, bundle_test_ids, lookup_test
from triage.verify.commands import RerunCommand, build_rerun_command


@dataclass(frozen=True)
class RunnerPaths:
    """Workspace paths used to build pytest and Playwright commands."""

    repo_root: Path
    ui_cwd: Path
    test_roots: tuple[str, ...] = ("tests/",)


class TestRunner(Protocol):
    """Run one allow-listed test and return a structured attempt."""

    def run(
        self,
        test_id: str,
        *,
        sha: str | None,
        bundle: EvidenceBundle,
    ) -> RerunAttempt:
        """Execute one test id that must appear in the bundle."""


OutcomeFn = Callable[[RerunCommand, TestResult], RerunAttempt]


class SubprocessTestRunner:
    """Run pytest or Playwright for an allow-listed test id."""

    def __init__(
        self,
        paths: RunnerPaths,
        *,
        timeout_seconds: float = 120.0,
        executor: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """Use the real subprocess runner unless an executor is injected."""
        self.paths = paths
        self.timeout_seconds = timeout_seconds
        self._executor = executor or subprocess.run

    def run(
        self,
        test_id: str,
        *,
        sha: str | None,
        bundle: EvidenceBundle,
    ) -> RerunAttempt:
        """Refuse unknown ids, then run the mapped command once."""
        allowed = bundle_test_ids(bundle)
        assert_allowed(test_id, allowed)
        test = lookup_test(bundle, test_id)
        if test is None:
            return RerunAttempt(
                test_id=test_id,
                sha=sha,
                outcome="error",
                detail="test record missing after allow-list check",
            )
        if sha is None or not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
            return RerunAttempt(
                test_id=test_id,
                sha=sha,
                outcome="error",
                detail="rerun requires a full commit SHA",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="triage-rerun-") as temp:
                source_root = Path(temp) / "source"
                self._materialize_commit(sha, source_root)
                self._link_ui_dependencies(source_root)
                command = build_rerun_command(
                    test,
                    repo_root=source_root,
                    ui_cwd=source_root / self.paths.ui_cwd.relative_to(self.paths.repo_root),
                )
                self._validate_target(command, source_root)
                return self._execute(command, sha=sha)
        except (OSError, subprocess.CalledProcessError, tarfile.TarError, ValueError) as exc:
            return RerunAttempt(
                test_id=test_id,
                sha=sha,
                outcome="error",
                detail=f"could not prepare commit checkout: {exc}",
            )

    def _materialize_commit(self, sha: str, destination: Path) -> None:
        """Extract only regular files and directories from the requested git commit."""
        archive = subprocess.run(
            ["git", "-C", str(self.paths.repo_root), "archive", "--format=tar", sha],
            capture_output=True,
            check=True,
        )
        destination.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
            for member in bundle.getmembers():
                target = (destination / member.name).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise ValueError(f"unsafe archive path: {member.name!r}")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = bundle.extractfile(member)
                    if content is None:
                        raise ValueError(f"invalid archive file: {member.name!r}")
                    target.write_bytes(content.read())
                else:
                    raise ValueError(f"unsupported archive entry: {member.name!r}")

    def _link_ui_dependencies(self, source_root: Path) -> None:
        """Reuse installed Playwright packages without changing the source revision."""
        original = self.paths.ui_cwd / "node_modules"
        snapshot_ui = source_root / self.paths.ui_cwd.relative_to(self.paths.repo_root)
        if original.exists() and not (snapshot_ui / "node_modules").exists():
            snapshot_ui.mkdir(parents=True, exist_ok=True)
            (snapshot_ui / "node_modules").symlink_to(original.resolve(), target_is_directory=True)

    def _validate_target(self, command: RerunCommand, source_root: Path) -> None:
        """Ensure bundle metadata can select only a test file inside configured test roots."""
        test = command.test_id
        if command.cwd == source_root:
            relative = test.split("::", 1)[0]
            allowed_roots = self.paths.test_roots
        else:
            relative = command.argv[3] if len(command.argv) > 3 else ""
            if relative.startswith("tests/ui/"):
                relative = relative.removeprefix("tests/ui/")
            allowed_roots = (".",)
        candidate_path = Path(relative)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            raise ValueError(f"unsafe test path: {relative!r}")
        normalized = candidate_path.as_posix()
        if not any(
            root == "." or normalized.startswith(root.rstrip("/") + "/")
            for root in allowed_roots
        ):
            raise ValueError(f"test path is outside configured test roots: {relative!r}")
        target = (command.cwd / candidate_path).resolve()
        if not target.is_relative_to(command.cwd.resolve()) or not target.is_file():
            raise ValueError(f"test file does not exist in requested commit: {relative!r}")

    def _execute(self, command: RerunCommand, *, sha: str | None) -> RerunAttempt:
        """Invoke the command and map exit status to passed/failed/error."""
        try:
            completed = self._executor(
                command.argv,
                cwd=command.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env={**os.environ, "PYTHON": sys.executable},
            )
        except FileNotFoundError as exc:
            return RerunAttempt(
                test_id=command.test_id,
                sha=sha,
                outcome="error",
                detail=f"command not found: {exc}",
            )
        except subprocess.TimeoutExpired:
            return RerunAttempt(
                test_id=command.test_id,
                sha=sha,
                outcome="error",
                detail=f"timed out after {self.timeout_seconds:.0f}s",
            )
        outcome: RerunOutcome = "passed" if completed.returncode == 0 else "failed"
        detail = (completed.stderr or completed.stdout or "").strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        return RerunAttempt(
            test_id=command.test_id,
            sha=sha,
            outcome=outcome,
            detail=detail or None,
        )


class ScriptedTestRunner:
    """Return pre-scripted outcomes for unit tests without spawning processes."""

    def __init__(self, outcomes: dict[str, RerunOutcome] | OutcomeFn) -> None:
        """Map test_id → outcome, or accept a callable over the built command."""
        self._outcomes = outcomes

    def run(
        self,
        test_id: str,
        *,
        sha: str | None,
        bundle: EvidenceBundle,
    ) -> RerunAttempt:
        """Refuse unknown ids, then return the scripted outcome."""
        allowed = bundle_test_ids(bundle)
        assert_allowed(test_id, allowed)
        test = lookup_test(bundle, test_id)
        if test is None:
            return RerunAttempt(
                test_id=test_id,
                sha=sha,
                outcome="error",
                detail="test record missing after allow-list check",
            )
        if callable(self._outcomes):
            command = build_rerun_command(
                test,
                repo_root=Path("."),
                ui_cwd=Path("tests/ui"),
            )
            attempt = self._outcomes(command, test)
            return attempt.model_copy(update={"sha": sha or attempt.sha})
        outcome = self._outcomes.get(test_id, "failed")
        return RerunAttempt(test_id=test_id, sha=sha, outcome=outcome)
