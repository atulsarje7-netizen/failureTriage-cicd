"""Unit tests for local evidence collection."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from triage.collect.local import collect_local
from triage.config import ArtifactPaths, TriageConfig


def test_collect_local_from_fixture_tree(fixtures_dir: Path, tmp_path: Path) -> None:
    """A local tree shaped like Iteration-3 artifacts yields a readable bundle."""
    root = tmp_path / "repo"
    api_dir = root / "test-results" / "api"
    ui_dir = root / "tests" / "ui" / "test-results"
    api_dir.mkdir(parents=True)
    ui_dir.mkdir(parents=True)
    shutil.copy(fixtures_dir / "junit_api.xml", api_dir / "junit.xml")
    shutil.copy(fixtures_dir / "playwright_results.json", ui_dir / "results.json")
    shutil.copytree(fixtures_dir / "exchanges", api_dir / "exchanges")
    trace_dest = ui_dir / "checkout-checkout-places-an-order-visible-in-history"
    trace_dest.mkdir(parents=True)
    shutil.copy(
        fixtures_dir / "traces" / "checkout-fail" / "trace.zip",
        trace_dest / "trace.zip",
    )
    # Point the report attachment at the copied trace.
    report = json.loads((ui_dir / "results.json").read_text(encoding="utf-8"))
    for suite in report["suites"]:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                for result in test.get("results", []):
                    for attachment in result.get("attachments", []):
                        if attachment.get("name") == "trace":
                            attachment["path"] = str(trace_dest / "trace.zip")
    (ui_dir / "results.json").write_text(json.dumps(report), encoding="utf-8")

    (root / "test-results" / "scenario").mkdir(parents=True)
    (root / "test-results" / "scenario" / "scenario.json").write_text(
        json.dumps(
            {
                "id": "product-checkout-500",
                "label": "product_bug",
                "flow_id": "checkout",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    out = tmp_path / "bundle.json"
    cfg = TriageConfig(artifacts=ArtifactPaths())
    bundle = collect_local(root=root, config=cfg, out_path=out)

    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "1"
    assert saved["run"]["run_id"] == "product-checkout-500"
    assert saved["examined"]["examined"] >= 1
    assert saved["examined"]["total"] >= saved["examined"]["examined"]
    assert any(flow["flow_id"] == "checkout" for flow in saved["flows"])
    checkout = next(flow for flow in bundle.flows if flow.flow_id == "checkout")
    assert checkout.api in {"failed", "mixed"}
    assert checkout.ui == "failed"
    # Enough signal to tell a cross-layer checkout product failure from a locator flake.
    assert bundle.totals.failed >= 1
    assert any(
        call.status_code == 500
        for test in bundle.tests
        if test.layer == "api"
        for call in test.http_calls
    )
    assert any(call.status_code == 500 for call in checkout.ui_http_statuses)
