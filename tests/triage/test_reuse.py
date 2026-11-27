"""Prove Iteration 10 reuse: a second layout triages with config only."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

from triage.classify.pipeline import run_rules
from triage.cli import app
from triage.collect.local import collect_local
from triage.config import load_config

ROOT = Path(__file__).resolve().parents[2]
SECOND = ROOT / "examples" / "second-repo"


def test_second_repo_collect_and_rules_need_config_only() -> None:
    """A backend/e2e layout yields product_bug cross-layer from its own config."""
    cfg = load_config(SECOND / "triage.config.yaml")
    bundle = collect_local(root=SECOND, config=cfg)

    assert bundle.run.repository == "example/inventory-service"
    assert any(change.kind == "product" for change in bundle.changes)
    assert any(change.path.startswith("backend/") for change in bundle.changes)
    checkout = next(flow for flow in bundle.flows if flow.flow_id == "checkout")
    assert checkout.api == "failed"
    assert checkout.ui == "failed"

    report, _trace = run_rules(bundle, cfg)
    labels = [verdict.label for verdict in report.verdicts]
    assert "product_bug" in labels
    product = next(v for v in report.verdicts if v.label == "product_bug")
    assert "cross-layer" in product.tags
    assert product.flow_id == "checkout"


def test_second_repo_cli_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI collect + run against the second repo uses only its config path."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    bundle_out = tmp_path / "bundle.json"
    verdicts_out = tmp_path / "verdicts.json"
    config = str(SECOND / "triage.config.yaml")

    collect = runner.invoke(
        app,
        [
            "collect",
            "local",
            "--root",
            str(SECOND),
            "--config",
            config,
            "--out",
            str(bundle_out),
        ],
    )
    assert collect.exit_code == 0, collect.output
    assert bundle_out.is_file()

    classify = runner.invoke(
        app,
        [
            "run",
            "--bundle",
            str(bundle_out),
            "--config",
            config,
            "--out",
            str(verdicts_out),
            "--engine",
            "rules",
            "--no-verify",
        ],
    )
    assert classify.exit_code == 0, classify.output
    assert "product_bug" in classify.output
    assert verdicts_out.is_file()


def test_triage_package_never_imports_demo_app() -> None:
    """Installed agent code stays independent of the demo shop."""
    triage_root = ROOT / "triage"
    for path in triage_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app"), path
                    assert not alias.name.startswith("faults"), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app"), path
                assert not node.module.startswith("faults"), path


def test_config_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Packaged installs resolve triage.config.yaml from the process cwd."""
    from triage import config as config_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "triage.config.yaml").write_text(
        "paths:\n  product: [backend/]\n",
        encoding="utf-8",
    )
    assert config_mod.DEFAULT_CONFIG_PATH == Path("triage.config.yaml")
    loaded = load_config()
    assert loaded.paths.product == ["backend/"]
