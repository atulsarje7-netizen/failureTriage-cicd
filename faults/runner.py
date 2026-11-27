from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from faults.catalog import INSTALL_PACKAGE, ROOT, Scenario, get_scenario

SCENARIO_DIR = ROOT / "test-results" / "scenario"
JUNIT_PATH = ROOT / "test-results" / "api" / "junit.xml"
UI_REPORT = ROOT / "tests" / "ui" / "test-results" / "results.json"
INSTALL_LOG = ROOT / "logs" / "install.log"
LINT_LOG = ROOT / "logs" / "lint.log"
BUILD_LOG = ROOT / "logs" / "build.log"
SETUP_LOG = ROOT / "logs" / "setup.log"
COMPOSE_FILE = ROOT / "docker-compose.yml"
_FAILED_UI = frozenset({"failed", "timedOut", "interrupted"})


@dataclass(frozen=True)
class CaseResult:
    """One test name and whether its final attempt failed."""

    name: str
    failed: bool


@dataclass(frozen=True)
class Outcome:
    """Whether a scenario failed the way the manifest describes."""

    scenario_id: str
    label: str
    matched: bool
    reasons: tuple[str, ...]
    api_failed: tuple[str, ...]
    ui_failed: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize the outcome for the scenario artifact."""
        return {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "matched": self.matched,
            "reasons": list(self.reasons),
            "api_failed": list(self.api_failed),
            "ui_failed": list(self.ui_failed),
        }


def execute(scenario_id: str) -> int:
    """Apply one scenario, run it, and return 0 when the failure matches."""
    scenario = get_scenario(scenario_id)
    _prepare_dirs()
    (SCENARIO_DIR / "scenario.json").write_text(
        json.dumps(scenario.to_public_dict(), indent=2) + "\n"
    )
    created = _created_files(scenario)
    _apply(scenario)
    env = _scenario_env(scenario)
    setup_failed = False
    stage_failed: bool | None = None
    try:
        _write_diff(scenario, created)
        if scenario.stage in {"install", "lint", "build"}:
            stage_failed = _run_pretest_stage(scenario, env)
        else:
            setup_failed = _run_services_and_tests(scenario, env)
    finally:
        _revert(scenario)
    outcome = judge(
        scenario,
        parse_junit(JUNIT_PATH) if scenario.run_api and JUNIT_PATH.is_file() else None,
        parse_playwright(UI_REPORT) if scenario.run_ui and UI_REPORT.is_file() else None,
        stage_failed=stage_failed,
        setup_failed=setup_failed,
    )
    (SCENARIO_DIR / "outcome.json").write_text(json.dumps(outcome.to_dict(), indent=2) + "\n")
    print(f"scenario {scenario.id} matched={outcome.matched}")
    for reason in outcome.reasons:
        print(f"  {reason}")
    return 0 if outcome.matched else 1


def gate() -> int:
    """Print the outcome and exit 1 so a finished scenario stays a failed CI run."""
    path = SCENARIO_DIR / "outcome.json"
    if not path.is_file():
        message = "No scenario outcome was written"
    else:
        raw = json.loads(path.read_text())
        if raw.get("matched") is True:
            message = f"Failed as intended: {raw.get('scenario_id')} label={raw.get('label')}"
        else:
            reasons = raw.get("reasons")
            detail = "; ".join(reasons) if isinstance(reasons, list) else "outcome mismatched"
            message = f"Did not fail as intended: {detail}"
    print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"## Fault scenario\n\n{message}\n")
    return 1


def judge(
    scenario: Scenario,
    api_cases: list[CaseResult] | None,
    ui_cases: list[CaseResult] | None,
    *,
    stage_failed: bool | None = None,
    install_failed: bool | None = None,
    setup_failed: bool,
) -> Outcome:
    """Compare recorded results with the scenario's expected failure."""
    reasons: list[str] = []
    api_failed = tuple(case.name for case in api_cases or [] if case.failed)
    ui_failed = tuple(case.name for case in ui_cases or [] if case.failed)
    pretest_failed = stage_failed if stage_failed is not None else install_failed
    if scenario.stage in {"install", "lint", "build"}:
        matched = pretest_failed is True
        reasons.append(
            f"{scenario.stage} step failed"
            if matched
            else f"{scenario.stage} step did not fail as intended"
        )
    elif scenario.whole_run:
        matched, whole_reasons = _judge_whole_run(api_cases, setup_failed)
        reasons.extend(whole_reasons)
    else:
        matched = True
        if scenario.run_api:
            matched = _judge_listed(
                "api",
                api_cases,
                scenario.expect.api_failed,
                scenario.expect.api_passed,
                reasons,
            ) and matched
        if scenario.run_ui:
            matched = _judge_listed(
                "ui",
                ui_cases,
                scenario.expect.ui_failed,
                scenario.expect.ui_passed,
                reasons,
            ) and matched
        if not scenario.run_api and not scenario.run_ui:
            matched = False
            reasons.append("scenario ran no tests")
    return Outcome(
        scenario_id=scenario.id,
        label=scenario.label,
        matched=matched,
        reasons=tuple(reasons),
        api_failed=api_failed,
        ui_failed=ui_failed,
    )


def parse_junit(path: Path) -> list[CaseResult]:
    """Read pytest JUnit XML into test node ids."""
    root = ET.parse(path).getroot()
    cases: list[CaseResult] = []
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            continue
        name = case.attrib.get("name", "")
        file_name = case.attrib.get("file")
        if file_name:
            nodeid = f"{file_name}::{name}"
        else:
            module = case.attrib.get("classname", "").replace(".", "/")
            nodeid = f"{module}.py::{name}"
        failed = case.find("failure") is not None or case.find("error") is not None
        cases.append(CaseResult(nodeid, failed))
    return cases


def parse_playwright(path: Path) -> list[CaseResult]:
    """Read a Playwright JSON report into test titles and final status."""
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("playwright report must be an object")
    found: list[CaseResult] = []
    _walk_suite(payload, found)
    return found


def _judge_whole_run(
    api_cases: list[CaseResult] | None,
    setup_failed: bool,
) -> tuple[bool, list[str]]:
    """Match when every API test failed, or setup failed before any were recorded."""
    if not api_cases:
        if setup_failed:
            return True, ["setup failed before any API test was recorded"]
        return False, ["no API results were recorded for a whole-run outage"]
    passed = [case.name for case in api_cases if not case.failed]
    if passed:
        return False, [f"{len(passed)} API tests passed during a whole-run outage"]
    return True, [f"all {len(api_cases)} API tests failed"]


def _judge_listed(
    layer: str,
    cases: list[CaseResult] | None,
    expected_failed: tuple[str, ...],
    expected_passed: tuple[str, ...],
    reasons: list[str],
) -> bool:
    """Return True when each required name failed or passed as listed."""
    if cases is None:
        reasons.append(f"missing {layer} results")
        return False
    failed = {case.name for case in cases if case.failed}
    passed = {case.name for case in cases if not case.failed}
    matched = True
    for name in expected_failed:
        if name not in failed:
            matched = False
            reasons.append(f"{layer} did not fail: {name}")
    for name in expected_passed:
        if name not in passed:
            matched = False
            reasons.append(f"{layer} did not pass: {name}")
    if matched:
        reasons.append(f"{layer} results matched")
    return matched


def _walk_suite(node: dict[str, object], found: list[CaseResult]) -> None:
    """Collect spec titles from a Playwright suite tree."""
    specs = node.get("specs")
    if isinstance(specs, list):
        for spec in specs:
            if isinstance(spec, dict):
                result = _spec_result(spec)
                if result is not None:
                    found.append(result)
    suites = node.get("suites")
    if isinstance(suites, list):
        for child in suites:
            if isinstance(child, dict):
                _walk_suite(child, found)


def _spec_result(spec: dict[str, object]) -> CaseResult | None:
    """Return the spec title and whether its last attempt failed."""
    title = spec.get("title")
    tests = spec.get("tests")
    if not isinstance(title, str) or not isinstance(tests, list):
        return None
    status: str | None = None
    for test in tests:
        if not isinstance(test, dict):
            continue
        results = test.get("results")
        if not isinstance(results, list) or not results:
            continue
        last = results[-1]
        raw_status = last.get("status") if isinstance(last, dict) else None
        if isinstance(raw_status, str):
            status = raw_status
    if status is None or status == "skipped":
        return None
    return CaseResult(title, status in _FAILED_UI)


def _run_services_and_tests(scenario: Scenario, env: dict[str, str]) -> bool:
    """Start Compose services when needed, run tests, and return setup_failed."""
    setup_failed = False
    if scenario.services and not _compose_up(scenario, env):
        setup_failed = True
        return True
    try:
        if scenario.services or env.get("USE_SHARED_DB") == "1":
            if not _seed(env):
                setup_failed = True
        if scenario.run_api:
            _run_pytest(scenario, env)
        if scenario.run_ui and "db" in scenario.services:
            _seed(env)
        if scenario.run_ui:
            _run_playwright(scenario, env)
    finally:
        if scenario.services:
            _dump_compose_logs(env)
            _compose_down(env)
    return setup_failed


def _prepare_dirs() -> None:
    """Create artifact directories for a scenario run."""
    JUNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    for path in (
        JUNIT_PATH,
        UI_REPORT,
        SETUP_LOG,
        INSTALL_LOG,
        LINT_LOG,
        BUILD_LOG,
        ROOT / "test-results" / "api" / "order-flag-catalog",
        ROOT / "tests" / "ui" / "test-results" / "order-flag",
    ):
        if path.exists():
            path.unlink()


_CLEARED_ENV = (
    "DATABASE_URL",
    "USE_SHARED_DB",
    "PAYMENT_URL",
    "PAYMENT_API_KEY",
    "PAYMENT_TIMEOUT_S",
    "PAYMENT_STUB_MODE",
    "PAYMENT_STUB_DELAY_MS",
    "PAYMENT_STUB_TOKEN",
    "APP_SEED_PASSWORD",
    "FLAKY_SEED",
)


def _scenario_env(scenario: Scenario) -> dict[str, str]:
    """Copy the process environment and overlay the scenario's variables."""
    env = os.environ.copy()
    for key in _CLEARED_ENV:
        env.pop(key, None)
    env.update(scenario.env)
    env.setdefault("APP_LOG_PATH", "logs/app.log")
    env.setdefault("PYTHON", sys.executable)
    return env


def _apply(scenario: Scenario) -> None:
    """Apply the scenario patch when it has one."""
    if scenario.patch is None:
        return
    subprocess.run(
        ["git", "apply", str(ROOT / "faults" / scenario.patch)],
        cwd=ROOT,
        check=True,
    )


def _revert(scenario: Scenario) -> None:
    """Reverse the scenario patch and remove files the patch created."""
    if scenario.patch is None:
        return
    subprocess.run(
        ["git", "apply", "-R", str(ROOT / "faults" / scenario.patch)],
        cwd=ROOT,
        check=False,
    )
    for relative in _created_files(scenario):
        (ROOT / relative).unlink(missing_ok=True)


def _created_files(scenario: Scenario) -> tuple[str, ...]:
    """Return paths a patch adds from /dev/null."""
    if scenario.patch is None:
        return ()
    lines = (ROOT / "faults" / scenario.patch).read_text().splitlines()
    created: list[str] = []
    for index, line in enumerate(lines):
        if not line.startswith("--- /dev/null"):
            continue
        for nxt in lines[index + 1 : index + 4]:
            if nxt.startswith("+++ b/"):
                created.append(nxt.removeprefix("+++ b/").split("\t", 1)[0])
                break
    return tuple(created)


def _write_diff(scenario: Scenario, created: tuple[str, ...]) -> None:
    """Save the scenario patch diff; leave it empty when the fault has no change."""
    if scenario.patch is None and not created:
        (SCENARIO_DIR / "scenario.diff").write_text("")
        return
    tracked = subprocess.run(
        [
            "git",
            "diff",
            "--",
            "app",
            "tests",
            "pyproject.toml",
            "Dockerfile",
            "docker-compose.yml",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    parts = [tracked.stdout]
    for relative in created:
        added = subprocess.run(
            ["git", "diff", "--no-index", "--", "/dev/null", relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        parts.append(added.stdout)
    (SCENARIO_DIR / "scenario.diff").write_text("".join(parts))


def _run_pretest_stage(scenario: Scenario, env: dict[str, str]) -> bool:
    """Run install, lint, or build for a pre-test scenario and return True on failure."""
    if scenario.stage == "install":
        return _run_install(scenario, env)
    if scenario.stage == "lint":
        return _run_lint(env)
    if scenario.stage == "build":
        return _run_build(env)
    return False


def _run_install(scenario: Scenario, env: dict[str, str]) -> bool:
    """Fail the install step via a bad pin, unreachable index, or missing package."""
    if scenario.patch is not None:
        command = [sys.executable, "-m", "pip", "install", "-e", "."]
    elif env.get("PIP_INDEX_URL"):
        # Ask for a package that is not already installed so pip must hit the index.
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--retries",
            "0",
            "--timeout",
            "3",
            "--no-cache-dir",
            INSTALL_PACKAGE,
        ]
    else:
        command = [sys.executable, "-m", "pip", "install", INSTALL_PACKAGE]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = completed.stdout + "\n" + completed.stderr
    INSTALL_LOG.write_text(text)
    return completed.returncode != 0 and (
        "No matching distribution" in text
        or "ERROR" in text
        or "Could not find" in text
        or "Connection refused" in text
        or "Could not resolve" in text
        or "Failed to establish" in text
    )


def _run_lint(env: dict[str, str]) -> bool:
    """Run ruff and return True when lint fails."""
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "app", "triage", "faults", "tests/api"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = completed.stdout + "\n" + completed.stderr
    LINT_LOG.write_text(text)
    return completed.returncode != 0


def _run_build(env: dict[str, str]) -> bool:
    """Build the Compose app image and return True when the build fails."""
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "failure-triage",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "app",
            "build",
            "app",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = completed.stdout + "\n" + completed.stderr
    BUILD_LOG.write_text(text)
    return completed.returncode != 0


def _compose_base() -> list[str]:
    """Return the docker compose command prefix with every profile enabled."""
    return [
        "docker",
        "compose",
        "-p",
        "failure-triage",
        "-f",
        str(COMPOSE_FILE),
        "--profile",
        "db",
        "--profile",
        "pay",
        "--profile",
        "app",
    ]


def _compose_up(scenario: Scenario, env: dict[str, str]) -> bool:
    """Start the scenario's Compose profiles and return False when startup fails."""
    command = [
        "docker",
        "compose",
        "-p",
        "failure-triage",
        "-f",
        str(COMPOSE_FILE),
    ]
    for service in scenario.services:
        command.extend(["--profile", service])
    command.extend(["up", "-d", "--wait", "--build"])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    with SETUP_LOG.open("a", encoding="utf-8") as handle:
        handle.write(completed.stdout)
        handle.write(completed.stderr)
    return completed.returncode == 0


def _compose_down(env: dict[str, str]) -> None:
    """Stop Compose services and drop their volumes."""
    subprocess.run(
        [*_compose_base(), "down", "-v"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _dump_compose_logs(env: dict[str, str]) -> None:
    """Write Compose service logs next to the app log."""
    completed = subprocess.run(
        [*_compose_base(), "logs", "--no-color"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    (ROOT / "logs" / "compose.log").write_text(completed.stdout + completed.stderr)


def _seed(env: dict[str, str]) -> bool:
    """Rebuild demo rows and return False when the database cannot be seeded."""
    completed = subprocess.run(
        [sys.executable, "-m", "app.seed", "--reset"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    with SETUP_LOG.open("a", encoding="utf-8") as handle:
        handle.write(completed.stdout)
        handle.write(completed.stderr)
    return completed.returncode == 0


def _run_pytest(scenario: Scenario, env: dict[str, str]) -> None:
    """Run the API suite, or the scenario's node ids, and keep going on failure."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=line",
        "--junitxml",
        str(JUNIT_PATH),
    ]
    if scenario.api_nodes:
        command.extend(scenario.api_nodes)
    else:
        command.extend(["-m", "app", "tests/api"])
    subprocess.run(command, cwd=ROOT, env=env, check=False)


def _run_playwright(scenario: Scenario, env: dict[str, str]) -> None:
    """Run Playwright, optionally grepping one title, and keep going on failure."""
    command = ["npx", "playwright", "test"]
    if scenario.ui_grep:
        command.extend(["-g", scenario.ui_grep])
    subprocess.run(command, cwd=ROOT / "tests" / "ui", env=env, check=False)

