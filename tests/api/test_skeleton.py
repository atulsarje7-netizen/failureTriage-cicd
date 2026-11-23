from pathlib import Path

import yaml
from triage import __version__


def test_package_version() -> None:
    assert __version__ == "0.0.0"


def test_triage_config_loads() -> None:
    config_path = Path(__file__).resolve().parents[2] / "triage.config.yaml"
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert config["ci"]["provider"] == "github_actions"
    assert "product" in config["paths"]
    assert "test" in config["paths"]
