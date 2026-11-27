import tomllib
from pathlib import Path

import yaml

from triage import __version__


def test_package_version() -> None:
    """Runtime package version matches the version declared in project metadata."""
    project_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with project_path.open("rb") as stream:
        project = tomllib.load(stream)
    assert __version__ == project["project"]["version"]


def test_triage_config_loads() -> None:
    """triage.config.yaml loads and names GitHub Actions plus path classes."""
    config_path = Path(__file__).resolve().parents[2] / "triage.config.yaml"
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert config["ci"]["provider"] == "github_actions"
    assert "product" in config["paths"]
    assert "test" in config["paths"]
    assert "api_junit" in config["artifacts"]
    assert config["flow_path_rules"]
