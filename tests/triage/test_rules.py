"""Unit tests for rules-only labels, clustering, and the run trace."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from triage.classify.pipeline import citation_supported, run_rules
from triage.cli import app
from triage.collect.assemble import build_bundle
from triage.collect.junit import JUnitParser
from triage.collect.playwright_json import PlaywrightJsonParser
from triage.config import TriageConfig
from triage.schemas.bundle import (
    EvidenceBundle,
    FileChange,
    FileKind,
    HttpCall,
    PipelineStep,
    RunMetadata,
)
from triage.schemas.bundle import TestResult as Result
from triage.signals.causes import cause_tags
from triage.signals.signature import signature_parts

ROOT = Path(__file__).resolve().parents[2]


def _bundle(
    tests: list[Result],
    changes: list[FileChange] | None = None,
    logs: dict[str, str] | None = None,
    steps: list[PipelineStep] | None = None,
) -> EvidenceBundle:
    """Build a bundle whose flows come from the supplied tests."""
    return build_bundle(
        run=RunMetadata(run_id="case", source="local"),
        tests=tests,
        changes=changes or [],
        logs=logs or {},
        steps=steps or [],
    )


def _change(path: str, kind: FileKind) -> FileChange:
    """One changed file with a class the rules can see."""
    return FileChange(path=path, kind=kind, added_lines=1, removed_lines=1)


def _labels(bundle: EvidenceBundle, **config: float) -> list[str]:
    """Return the labels run_rules assigns, optionally raising the abstain threshold."""
    threshold = config.get("abstain_below", 0.75)
    report, _trace = run_rules(bundle, TriageConfig(abstain_below=threshold))
    return [verdict.label for verdict in report.verdicts]


def test_signature_ignores_line_numbers() -> None:
    """The same function at two line numbers is one signature."""
    left = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        status="failed",
        file="tests/api/test_orders.py",
        stack='File "tests/api/test_orders.py", line 10, in test_checkout\nAssertionError: id=abc',
    )
    right = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        status="failed",
        file="tests/api/test_orders.py",
        stack='File "tests/api/test_orders.py", line 99, in test_checkout\nAssertionError: id=def',
    )
    other = Result(
        test_id="tests/api/test_orders.py::test_other",
        layer="api",
        status="failed",
        file="tests/api/test_orders.py",
        stack='File "tests/api/test_orders.py", line 10, in test_other\nAssertionError',
    )
    assert signature_parts(left)[0] == signature_parts(right)[0]
    assert signature_parts(left)[0] != signature_parts(other)[0]


def test_whole_run_outage_is_one_verdict() -> None:
    """Every executed test failing with a connection error is one environment verdict."""
    tests = [
        Result(
            test_id=f"tests/api/test_health.py::test_{name}",
            layer="api",
            flow_id="health",
            status="failed",
            file="tests/api/test_health.py",
            message="psycopg.OperationalError: connection refused",
            stack=(
                'File "tests/api/conftest.py", line 12, in api_client\n'
                "OperationalError: connection refused"
            ),
        )
        for name in ("health", "login", "catalog")
    ]
    bundle = _bundle(tests)
    report, trace = run_rules(bundle, TriageConfig())
    assert [verdict.label for verdict in report.verdicts] == ["environment_issue"]
    assert report.verdicts[0].rule == "whole_run_outage"
    assert len(report.verdicts[0].test_ids) == 3
    assert {event.name for event in trace.events} >= {
        "summarise_bundle",
        "classify_diff",
        "detect_outage",
        "cluster_failures",
        "apply_rules",
    }
    assert all(event.error is None and event.latency_ms >= 0 for event in trace.events)


def test_install_failure_is_one_environment_verdict() -> None:
    """A missing distribution before any test runs is an install environment issue."""
    bundle = _bundle(
        [],
        logs={"install": "ERROR: No matching distribution found for missing==0.0.0"},
        steps=[
            PipelineStep(job_name="local", name="Install dependencies", conclusion="failure"),
        ],
    )
    report, _trace = run_rules(bundle, TriageConfig())
    assert len(report.verdicts) == 1
    verdict = report.verdicts[0]
    assert verdict.label == "environment_issue"
    assert verdict.stage == "install"
    assert "dependency-install" in verdict.tags
    assert all(citation_supported(cite, bundle) for cite in verdict.evidence)


def test_bad_pin_is_build_break() -> None:
    """A dependency pin in the change that cannot resolve is build_break, not environment."""
    bundle = _bundle(
        [],
        changes=[_change("pyproject.toml", "dependency")],
        logs={
            "install": (
                "ERROR: No matching distribution found for "
                "failure-triage-missing-dependency"
            )
        },
        steps=[
            PipelineStep(job_name="local", name="Install dependencies", conclusion="failure"),
        ],
    )
    report, _trace = run_rules(bundle, TriageConfig())
    assert [item.label for item in report.verdicts] == ["build_break"]
    assert report.verdicts[0].stage == "install"


def test_lint_and_docker_pull_stage_labels() -> None:
    """Lint from a product change is build_break; an image pull failure is environment."""
    lint_bundle = _bundle(
        [],
        changes=[_change("app/shop.py", "product")],
        logs={"lint": "app/shop.py:1:1: F401 `unused_lint_marker_app` imported but unused"},
        steps=[PipelineStep(job_name="local", name="Lint", conclusion="failure")],
    )
    lint_report, _ = run_rules(lint_bundle, TriageConfig())
    assert lint_report.verdicts[0].label == "build_break"
    assert lint_report.verdicts[0].stage == "lint"

    pull_bundle = _bundle(
        [],
        changes=[_change("Dockerfile", "infra")],
        logs={
            "build": (
                "ERROR: failed to solve: failed to fetch anonymous token: "
                "unexpected status from GET request to https://ghcr.io/token: 403 Forbidden"
            )
        },
        steps=[PipelineStep(job_name="local", name="Build", conclusion="failure")],
    )
    pull_report, _ = run_rules(pull_bundle, TriageConfig())
    assert pull_report.verdicts[0].label == "environment_issue"
    assert pull_report.verdicts[0].stage == "build"


def test_cross_layer_product_bug_and_recovered_flaky(fixtures_dir: Path) -> None:
    """A checkout 500 on both layers is a product bug, and a recovered UI test is flaky."""
    api = JUnitParser().parse(fixtures_dir / "junit_api.xml")
    ui = PlaywrightJsonParser().parse(fixtures_dir / "playwright_results.json")
    bundle = _bundle(api + ui, changes=[_change("app/shop.py", "product")])
    report, _trace = run_rules(bundle, TriageConfig())
    by_flow: dict[str | None, list[str]] = {}
    for verdict in report.verdicts:
        by_flow.setdefault(verdict.flow_id, []).append(verdict.label)
    assert by_flow["checkout"] == ["product_bug", "product_bug"] or set(by_flow["checkout"]) == {
        "product_bug"
    }
    assert "flaky_test" in by_flow["login"]
    checkout = [item for item in report.verdicts if item.flow_id == "checkout"]
    assert all("cross-layer" in item.tags for item in checkout)
    for verdict in report.verdicts:
        assert verdict.evidence
        assert all(citation_supported(cite, bundle) for cite in verdict.evidence)


def test_layer_matrix_cases() -> None:
    """Diff class and layer outcomes pick product, test, or abstain."""
    api_fail = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        title="test_checkout",
        message="AssertionError: assert 1 == 1299",
    )
    api_pass = Result(
        test_id="tests/api/test_orders.py::test_empty",
        layer="api",
        flow_id="checkout",
        status="passed",
        file="tests/api/test_orders.py",
    )
    ui_fail = Result(
        test_id="checkout.spec.ts > checkout places an order",
        layer="ui",
        flow_id="checkout",
        status="failed",
        file="checkout.spec.ts",
        title="checkout places an order",
        message=(
            "Error: locator.click: Timeout 30000ms exceeded.\n"
            "waiting for getByTestId('place-order')"
        ),
    )
    ui_pass = Result(
        test_id="checkout.spec.ts > checkout empty",
        layer="ui",
        flow_id="checkout",
        status="passed",
        file="checkout.spec.ts",
    )
    product = [_change("app/shop.py", "product")]
    tests_only = [_change("tests/api/test_orders.py", "test")]
    web = [_change("app/web/templates/checkout.html", "product_ui")]

    assert _labels(_bundle([api_fail, ui_pass], product)) == ["product_bug"]
    assert _labels(_bundle([api_fail, ui_pass], tests_only)) == ["test_bug"]
    assert _labels(_bundle([ui_fail, api_pass], web)) == ["product_bug"]
    assert _labels(_bundle([ui_fail, api_pass], tests_only)) == ["test_bug"]

    ui_5xx = ui_fail.model_copy(
        update={
            "http_calls": [HttpCall(method="POST", url="http://shop/checkout", status_code=500)],
        }
    )
    assert _labels(_bundle([ui_5xx, api_pass], tests_only)) == ["needs_human"]


def test_never_passed_is_not_flaky() -> None:
    """An intermittent failure with no passing attempt abstains instead of flaky_test."""
    failed = Result(
        test_id="tests/api/test_products.py::test_list_products",
        layer="api",
        flow_id="catalog",
        status="failed",
        file="tests/api/test_products.py",
        title="test_list_products",
        message="AssertionError: intermittent catalog failure",
    )
    labels = _labels(_bundle([failed], [_change("tests/api/test_products.py", "test")]))
    assert labels == ["needs_human"]
    assert "flaky_test" not in labels

    recovered = Result(
        test_id="login.spec.ts > alice can log in",
        layer="ui",
        flow_id="login",
        status="flaky",
        file="login.spec.ts",
        title="alice can log in",
        message="Error: Timeout 5000ms exceeded.",
    )
    assert _labels(_bundle([recovered])) == ["flaky_test"]


def test_payment_and_secret_without_a_diff_are_environment() -> None:
    """No code diff plus a dependency or auth failure is environment_issue on that flow."""
    catalog = Result(
        test_id="tests/api/test_products.py::test_list_products",
        layer="api",
        flow_id="catalog",
        status="passed",
        file="tests/api/test_products.py",
    )
    api = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        title="test_checkout",
        message="AssertionError: assert 503 == 201 payment unavailable",
    )
    ui = Result(
        test_id="checkout.spec.ts > checkout places an order",
        layer="ui",
        flow_id="checkout",
        status="failed",
        file="checkout.spec.ts",
        title="checkout places an order",
        message="Error: expect(locator).toBeVisible() failed",
    )
    assert set(_labels(_bundle([catalog, api, ui]))) == {"environment_issue"}

    login_api = Result(
        test_id="tests/api/test_auth.py::test_login_success",
        layer="api",
        flow_id="login",
        status="failed",
        file="tests/api/test_auth.py",
        title="test_login_success",
        message="AssertionError: assert 401 == 200",
    )
    login_ui = Result(
        test_id="login.spec.ts > alice can log in",
        layer="ui",
        flow_id="login",
        status="failed",
        file="login.spec.ts",
        title="alice can log in",
        message=(
            "Error: locator.click: Timeout 30000ms exceeded.\n"
            "waiting for getByTestId('catalog-heading')"
        ),
    )
    assert set(_labels(_bundle([catalog, login_api, login_ui]))) == {"environment_issue"}


def test_resolved_text_mismatch_is_not_a_missing_selector() -> None:
    """A locator that resolved to the wrong text is an assertion, not a missing selector."""
    message = (
        "Error: expect(locator).toHaveText(expected) failed\n"
        "Locator: getByTestId('cart-count')\n"
        'Expected: "1"\n'
        'Received: "0"\n'
        "Timeout: 5000ms\n"
        "waiting for getByTestId('cart-count')\n"
        'locator resolved to <span data-testid="cart-count">0</span>\n'
        'unexpected value "0"'
    )
    assert cause_tags(message, []) == ["assertion-mismatch"]
    missing = (
        "Error: locator.click: Timeout 30000ms exceeded.\nwaiting for getByTestId('place-order')"
    )
    assert cause_tags(missing, []) == ["selector-not-found"]


def test_login_auth_outage_covers_later_ui_assertions() -> None:
    """A broken login explains a later cart-badge assertion when nothing else changed."""
    badge = (
        "Error: expect(locator).toHaveText(expected) failed\n"
        "Locator: getByTestId('cart-count')\n"
        'Expected: "1"\n'
        'Received: "0"\n'
        "waiting for getByTestId('cart-count')\n"
        "locator resolved to <span>0</span>"
    )
    catalog_api = Result(
        test_id="tests/api/test_products.py::test_list_products",
        layer="api",
        flow_id="catalog",
        status="passed",
        file="tests/api/test_products.py",
    )
    catalog_ui = Result(
        test_id="catalog.spec.ts > add to cart updates the badge",
        layer="ui",
        flow_id="catalog",
        status="failed",
        file="catalog.spec.ts",
        title="add to cart updates the badge",
        message=badge,
    )
    login_api = Result(
        test_id="tests/api/test_auth.py::test_login_success",
        layer="api",
        flow_id="login",
        status="failed",
        file="tests/api/test_auth.py",
        title="test_login_success",
        message="AssertionError: assert 401 == 200",
    )
    login_ui = Result(
        test_id="login.spec.ts > alice can log in",
        layer="ui",
        flow_id="login",
        status="failed",
        file="login.spec.ts",
        title="alice can log in",
        message=(
            "Error: locator.click: Timeout 30000ms exceeded.\nwaiting for getByTestId('catalog')"
        ),
    )
    env_labels = _labels(_bundle([catalog_api, catalog_ui, login_api, login_ui]))
    assert env_labels
    assert set(env_labels) == {"environment_issue"}

    product = [_change("app/security.py", "product")]
    product_labels = _labels(_bundle([catalog_api, catalog_ui, login_api, login_ui], product))
    assert set(product_labels) == {"product_bug"}


def test_payment_failed_with_a_product_diff_stays_a_product_bug() -> None:
    """HTTP 500 that mentions payment is a product bug when app code changed."""
    api = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        title="test_checkout",
        message='AssertionError: assert 500 == 201 {"detail":"payment failed"}',
    )
    ui = Result(
        test_id="checkout.spec.ts > checkout places an order",
        layer="ui",
        flow_id="checkout",
        status="failed",
        file="checkout.spec.ts",
        title="checkout places an order",
        message="Error: expect(locator).toBeVisible() failed",
    )
    passed = Result(
        test_id="tests/api/test_products.py::test_list_products",
        layer="api",
        flow_id="catalog",
        status="passed",
        file="tests/api/test_products.py",
    )
    labels = _labels(_bundle([passed, api, ui], [_change("app/shop.py", "product")]))
    assert labels
    assert set(labels) == {"product_bug"}


def test_same_frame_clusters_failures() -> None:
    """Two tests that blow up in the same helper become one verdict."""
    stack = 'File "tests/api/conftest.py", line 4, in client\nOperationalError: connection refused'
    tests = [
        Result(
            test_id="tests/api/test_orders.py::test_one",
            layer="api",
            flow_id="checkout",
            status="failed",
            message="OperationalError: connection refused",
            stack=stack,
        ),
        Result(
            test_id="tests/api/test_orders.py::test_two",
            layer="api",
            flow_id="checkout",
            status="failed",
            message="OperationalError: connection refused",
            stack=stack,
        ),
        Result(
            test_id="tests/api/test_products.py::test_list_products",
            layer="api",
            flow_id="catalog",
            status="passed",
        ),
    ]
    report, _trace = run_rules(_bundle(tests), TriageConfig())
    assert len(report.verdicts) == 1
    assert report.verdicts[0].label == "environment_issue"
    assert len(report.verdicts[0].test_ids) == 2


def test_runner_launch_and_short_timeout() -> None:
    """A missing browser is an environment issue, and a 100ms wait is not a locator bug."""
    launch = Result(
        test_id="login.spec.ts > guest can open login",
        layer="ui",
        flow_id="login",
        status="failed",
        file="login.spec.ts",
        title="guest can open login",
        message="Error: browserType.launch: Executable doesn't exist at /tmp/chrome",
    )
    api = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        title="test_checkout",
        message="AssertionError: assert 500 == 201",
    )
    labels = _labels(_bundle([launch, api], [_change("app/shop.py", "product")]))
    assert "environment_issue" in labels
    assert "product_bug" in labels

    late = Result(
        test_id="checkout.spec.ts > late checkout banner",
        layer="ui",
        flow_id="checkout",
        status="failed",
        file="checkout.spec.ts",
        title="late checkout banner",
        message=(
            "Error: expect(locator).toBeVisible() failed\n"
            "Timeout: 100ms\nError: element(s) not found"
        ),
    )
    passed = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="passed",
        file="tests/api/test_orders.py",
    )
    late_bundle = _bundle([late, passed], [_change("tests/ui/specs/checkout.spec.ts", "test")])
    assert _labels(late_bundle) == ["needs_human"]


def test_threshold_abstains_when_confidence_is_low() -> None:
    """A clear rule drops to needs_human when the configured threshold is higher."""
    api = Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        title="test_checkout",
        message="AssertionError: assert 1 == 1299",
    )
    ui = Result(
        test_id="checkout.spec.ts > visible",
        layer="ui",
        flow_id="checkout",
        status="passed",
        file="checkout.spec.ts",
    )
    labels = _labels(
        _bundle([api, ui], [_change("app/shop.py", "product")]),
        abstain_below=0.99,
    )
    assert labels == ["needs_human"]


def test_cli_run_writes_verdicts_and_trace(tmp_path: Path) -> None:
    """triage run --bundle writes a verdict file and a trace file."""
    bundle = _bundle(
        [
            Result(
                test_id="tests/api/test_orders.py::test_checkout",
                layer="api",
                flow_id="checkout",
                status="failed",
                file="tests/api/test_orders.py",
                title="test_checkout",
                message="AssertionError: assert 1 == 1299",
            )
        ],
        [_change("tests/api/test_orders.py", "test")],
    )
    path = tmp_path / "bundle.json"
    path.write_text(bundle.model_dump_json(), encoding="utf-8")
    out = tmp_path / "verdicts.json"
    result = CliRunner().invoke(
        app,
        ["run", "--bundle", str(path), "--out", str(out), "--trace", str(tmp_path / "trace.json")],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert (tmp_path / "trace.json").is_file()
    assert "test_bug" in result.output


def test_triage_package_does_not_import_the_demo_app() -> None:
    """The agent stays reusable: triage/ does not import app, tests, or faults."""
    for path in (ROOT / "triage").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import app", "from app"))
            assert not stripped.startswith(("import tests", "from tests"))
            assert not stripped.startswith(("import faults", "from faults"))
