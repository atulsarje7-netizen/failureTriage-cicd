"""Unit tests for unified-diff parsing and path classification."""

from __future__ import annotations

from pathlib import Path

from triage.collect.diff import classify_path, parse_unified_diff
from triage.collect.local import collect_local
from triage.config import PathClasses, TriageConfig, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_parse_product_and_ui_patches() -> None:
    """Scenario patches class product code, product UI, and new test files."""
    checkout = (ROOT / "faults/scenarios/product-checkout-500.patch").read_text(encoding="utf-8")
    changes = parse_unified_diff(checkout, PathClasses())
    assert [(item.path, item.kind) for item in changes] == [("app/shop.py", "product")]
    assert changes[0].added_lines == 3

    ui = (ROOT / "faults/scenarios/product-ui-place-order-testid.patch").read_text(encoding="utf-8")
    ui_changes = parse_unified_diff(ui, PathClasses())
    assert ui_changes[0].path == "app/web/templates/checkout.html"
    assert ui_changes[0].kind == "product_ui"

    created = (ROOT / "faults/scenarios/flaky-ui-order-dependent.patch").read_text(encoding="utf-8")
    created_changes = parse_unified_diff(created, PathClasses())
    assert created_changes[0].path == "tests/ui/specs/order-flag.spec.ts"
    assert created_changes[0].kind == "test"
    assert created_changes[0].added_lines > 0


def test_classify_path_prefers_the_longest_prefix() -> None:
    """app/web files are product UI even though they also sit under app/."""
    paths = PathClasses()
    assert classify_path("app/web/pages.py", paths) == "product_ui"
    assert classify_path("app/api/orders.py", paths) == "product"
    assert classify_path("tests/api/test_orders.py", paths) == "test"
    assert classify_path("docker-compose.yml", paths) == "config"
    assert classify_path("README.md", paths) == "other"


def test_collect_local_reads_scenario_diff(tmp_path: Path) -> None:
    """A local collect stores classed changes from scenario.diff."""
    root = tmp_path / "repo"
    scenario = root / "test-results" / "scenario"
    scenario.mkdir(parents=True)
    patch = (ROOT / "faults/scenarios/product-checkout-500.patch").read_text(encoding="utf-8")
    (scenario / "scenario.diff").write_text(patch, encoding="utf-8")
    bundle = collect_local(root=root, config=TriageConfig())
    assert bundle.changes[0].path == "app/shop.py"
    assert bundle.changes[0].kind == "product"


def test_load_config_reads_paths_and_threshold() -> None:
    """triage.config.yaml supplies path classes and the abstain threshold."""
    config = load_config()
    assert "app/web/" in config.paths.product_ui
    assert config.abstain_below == 0.75
