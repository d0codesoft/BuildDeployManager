"""Schema and project isolation regression tests."""

from dataclasses import asdict

import pytest
import yaml

from scdeploy.config import Config, decode, load, load_projects, validate
from scdeploy.errors import ConfigError


def test_valid_yaml(config, tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(asdict(config)))
    assert load(path) == config


@pytest.mark.parametrize("mutation", [
    lambda d: d.pop("project"),
    lambda d: d.update(schema_version=2),
    lambda d: d["build"].update(provider="unknown"),
    lambda d: d["build"].update(tests="yes"),
    lambda d: d["project"].update(name="../../escape"),
    lambda d: d["deploy"].update(release_directory="relative"),
    lambda d: d["build"].update(publish_directory="../outside"),
    lambda d: d.update(password="never-echo-this-value"),
    lambda d: d["deploy"].update(services=["--bad.service"]),
    lambda d: d["updates"].update(mode="auto"),
    lambda d: d["source"].update(repository="https://user:secret@example.com/repo"),
])
def test_invalid_fields(config, mutation):
    data = asdict(config)
    mutation(data)
    with pytest.raises(ConfigError):
        validate(decode(Config, data))


def test_duplicate_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("project: {}\nproject: {}\n")
    with pytest.raises(ConfigError):
        load(p)


def test_duplicate_identity(config, tmp_path):
    for name in ["one", "two"]:
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(asdict(config)))
    with pytest.raises(ConfigError, match="Duplicate"):
        load_projects(tmp_path)


def test_overlapping_project_roots(config, tmp_path):
    one = asdict(config)
    two = asdict(config)
    two["project"]["name"] = "second"
    for name, data in [("one", one), ("two", two)]:
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="overlap"):
        load_projects(tmp_path)
