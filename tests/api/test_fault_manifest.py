from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from faults.catalog import ROOT, get_scenario, load_scenarios
from faults.runner import CaseResult, judge, parse_junit, parse_playwright


def test_manifest_has_ten_scenarios_per_label() -> None:
    """The catalog has ten scenarios for each primary label."""
    counts = Counter(scenario.label for scenario in load_scenarios())
    assert counts == {
        "product_bug": 10,
        "test_bug": 10,
        "flaky_test": 10,
        "environment_issue": 10,
    }


def test_catalog_covers_layer_matrix_cases() -> None:
    """Paired checkout failures, a UI-only product bug, and a stale locator are present."""
    checkout = get_scenario("product-checkout-500")
    assert checkout.flow_id == "checkout"
    assert "cross-layer" in checkout.tags
    assert checkout.expect.api_failed
    assert checkout.expect.ui_failed
    ui_only = get_scenario("product-ui-place-order-testid")
    assert ui_only.label == "product_bug"
    assert "selector-not-found" in ui_only.tags
    assert not ui_only.expect.api_failed
    assert ui_only.expect.api_passed
    locator = get_scenario("test-ui-locator-place-order")
    assert locator.label == "test_bug"
    assert "selector-not-found" in locator.tags
    assert get_scenario("env-dependency-install").stage == "install"


def test_each_patch_applies_and_reverts() -> None:
    """Every scenario patch applies to this tree and reverses cleanly."""
    for scenario in load_scenarios():
        if scenario.patch is None:
            continue
        path = str(ROOT / "faults" / scenario.patch)
        check = subprocess.run(
            ["git", "apply", "--check", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, f"{scenario.id}\n{check.stderr}"
        subprocess.run(["git", "apply", path], cwd=ROOT, check=True)
        try:
            reverse = subprocess.run(
                ["git", "apply", "-R", path],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            assert reverse.returncode == 0, f"revert {scenario.id}\n{reverse.stderr}"
        except Exception:
            subprocess.run(["git", "apply", "-R", path], cwd=ROOT, check=False)
            raise
        assert not (ROOT / "tests" / "ui" / "specs" / "order-flag.spec.ts").exists()


def test_judge_accepts_the_listed_results() -> None:
    """A scenario matches when the named tests fail and pass as listed."""
    scenario = get_scenario("product-order-total-doubled")
    api = [CaseResult(name, True) for name in scenario.expect.api_failed]
    api.extend(CaseResult(name, False) for name in scenario.expect.api_passed)
    ui = [CaseResult(name, False) for name in scenario.expect.ui_passed]
    outcome = judge(scenario, api, ui, install_failed=None, setup_failed=False)
    assert outcome.matched, outcome.reasons


def test_judge_rejects_a_missing_failure() -> None:
    """A listed failure that passed does not match."""
    scenario = get_scenario("test-api-checkout-wrong-total")
    api = [CaseResult(name, False) for name in scenario.expect.api_failed]
    outcome = judge(scenario, api, [], install_failed=None, setup_failed=False)
    assert not outcome.matched


def test_judge_whole_run_and_install() -> None:
    """A whole-run outage matches only when every API test failed, and a bad install matches."""
    outage = get_scenario("env-database-down")
    failed = judge(
        outage,
        [CaseResult("tests/api/test_health.py::test_health_reports_database_and_payment", True)],
        None,
        install_failed=None,
        setup_failed=True,
    )
    assert failed.matched, failed.reasons
    leaked = judge(
        outage,
        [CaseResult("tests/api/test_products.py::test_list_products", False)],
        None,
        install_failed=None,
        setup_failed=True,
    )
    assert not leaked.matched
    install = get_scenario("env-dependency-install")
    assert judge(install, None, None, install_failed=True, setup_failed=False).matched
    assert not judge(install, None, None, install_failed=False, setup_failed=False).matched


def test_parse_junit_node_ids(tmp_path: Path) -> None:
    """JUnit failures become pytest node ids and skips are ignored."""
    path = tmp_path / "junit.xml"
    checkout = "test_checkout_creates_order_and_clears_cart"
    path.write_text(
        "\n".join(
            [
                '<?xml version="1.0" ?>',
                "<testsuite>",
                '  <testcase classname="tests.api.test_orders"',
                f'    name="{checkout}" file="tests/api/test_orders.py">',
                '    <failure message="assert 500"/>',
                "  </testcase>",
                '  <testcase classname="tests.api.test_products"',
                '    name="test_list_products" file="tests/api/test_products.py"/>',
                '  <testcase classname="tests.api.test_auth"',
                '    name="test_login_success" file="tests/api/test_auth.py">',
                '    <skipped message="no"/>',
                "  </testcase>",
                "</testsuite>",
            ]
        )
    )
    cases = {case.name: case.failed for case in parse_junit(path)}
    assert cases["tests/api/test_orders.py::test_checkout_creates_order_and_clears_cart"] is True
    assert cases["tests/api/test_products.py::test_list_products"] is False
    assert len(cases) == 2


def test_parse_playwright_final_attempt(tmp_path: Path) -> None:
    """Playwright titles use the last attempt and ignore skipped specs."""
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "title": "checkout.spec.ts",
                        "specs": [
                            {
                                "title": "checkout places an order visible in history",
                                "tests": [
                                    {
                                        "results": [
                                            {"status": "failed"},
                                            {"status": "passed"},
                                        ]
                                    }
                                ],
                            },
                            {
                                "title": "late checkout banner",
                                "tests": [{"results": [{"status": "timedOut"}]}],
                            },
                        ],
                        "suites": [
                            {
                                "specs": [
                                    {
                                        "title": "deliberate failure for trace artifacts",
                                        "tests": [{"results": [{"status": "skipped"}]}],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        )
    )
    cases = {case.name: case.failed for case in parse_playwright(path)}
    assert cases["checkout places an order visible in history"] is False
    assert cases["late checkout banner"] is True
    assert "deliberate failure for trace artifacts" not in cases
