"""Additional regressions for crash safety and OS/filesystem boundaries."""

import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from scdeploy.config import load_scheduled_projects
from scdeploy.deployment import atomic_link
from scdeploy.errors import PolicyError, VerificationError
from scdeploy.execution import Runner
from scdeploy.security import register_secret


def test_corrupt_payload_does_not_stop_current(manager, artifact, tmp_path):
    archive = artifact()
    manager.deploy(archive, True)
    count = len(manager.services.calls)
    bad = tmp_path / "corrupt.tar.gz"
    bad.write_bytes(b"not an archive")
    with pytest.raises(VerificationError):
        manager.deploy(bad, True)
    assert len(manager.services.calls) == count
    assert manager.services.active("sample.service")


def test_runtime_mismatch(manager, artifact):
    archive = artifact()
    manager.config.build.runtime = "linux-arm64"
    with pytest.raises(VerificationError, match="runtime"):
        manager.deploy(archive, True)
    assert not manager.services.calls


def test_conflicting_reused_version(manager, artifact):
    archive = artifact()
    manager.deploy(archive, True)
    (manager.link.resolve() / "app.txt").write_text("changed")
    with pytest.raises(VerificationError):
        manager.deploy(archive, True)


def test_interruption_rolls_back(manager, artifact, monkeypatch):
    manager.deploy(artifact(), True)
    original = manager.health.check
    count = [0]
    def interrupt(config):
        count[0] += 1
        if count[0] == 1:
            raise KeyboardInterrupt
        return original(config)
    monkeypatch.setattr(manager.health, "check", interrupt)
    with pytest.raises(KeyboardInterrupt):
        manager.deploy(artifact("1.1.0"), True)
    assert manager.link.resolve().name == "1.0.0"
    assert manager.services.active("sample.service")


def test_retention_keeps_current_previous_and_history(manager, artifact):
    manager.config.deploy.rollback.keep_releases = 2
    for number in range(1, 5):
        manager.deploy(artifact(f"1.0.{number}"), True)
    assert len(list(manager.root.glob("1.*"))) == 2
    assert len(manager.state.history("sample")) == 4
    assert Path(manager.state.project("sample")["previous_release"]).exists()


def test_malformed_config_does_not_disable_other_projects(config, tmp_path):
    (tmp_path / "good.yaml").write_text(yaml.safe_dump(asdict(config)))
    (tmp_path / "bad.yaml").write_text("unknown: bad")
    projects, errors = load_scheduled_projects(tmp_path)
    assert list(projects) == ["sample"]
    assert "bad.yaml" in errors


def test_colliding_scheduler_roots_disable_both(config, tmp_path):
    one = asdict(config)
    two = asdict(config)
    two["project"]["name"] = "other"
    (tmp_path / "one.yaml").write_text(yaml.safe_dump(one))
    (tmp_path / "two.yaml").write_text(yaml.safe_dump(two))
    projects, errors = load_scheduled_projects(tmp_path)
    assert not projects
    assert len(errors) == 2


def test_output_is_bounded_and_redacted():
    register_secret("known-secret-fixture")
    result = Runner().run([sys.executable, "-c", "print('x'*200000); print('known-secret-fixture')"])
    assert len(result.stdout) <= 65536
    assert "known-secret-fixture" not in result.stdout


def test_automatic_policy_requires_signed_branch(manager, artifact, keys):
    c = manager.config
    c.source.type = "http"
    c.source.branch = "main"
    c.source.public_key = str(keys[1])
    c.deploy.require_signature = True
    c.updates.mode = "automatic"
    c.updates.require_approval = False
    c.updates.allowed_branches = ["main"]
    archive = artifact(signing_key=keys[0])
    with pytest.raises(VerificationError, match="source_branch"):
        manager.deploy(archive, automatic=True)
    assert not manager.services.calls


def test_authorized_automatic_install(manager, artifact, keys):
    c = manager.config
    c.source.type = "http"
    c.source.branch = "main"
    c.source.public_key = str(keys[1])
    c.deploy.require_signature = True
    c.updates.mode = "automatic"
    c.updates.require_approval = False
    c.updates.allowed_branches = ["main"]
    archive = artifact(signing_key=keys[0], source_branch="main")
    assert manager.deploy(archive, automatic=True)["status"] == "success"
    manager.state.update("sample", consecutive_failures=3)
    with pytest.raises(PolicyError, match="suspended"):
        manager.deploy(archive, automatic=True)


@pytest.mark.skipif(os.name != "posix", reason="Real symlink atomicity requires Unix; Windows transactions use explicit adapter")
def test_real_atomic_link(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    current = tmp_path / "current"
    atomic_link(current, old)
    assert current.resolve() == old
    atomic_link(current, new)
    assert current.resolve() == new
    assert not list(tmp_path.glob(".current-*"))
    atomic_link(current, None)
    assert not current.exists()


@pytest.mark.skipif(os.name != "posix", reason="Unix ownership and symlink policy")
def test_symlink_ancestor_is_rejected(manager, tmp_path, artifact):
    outside = tmp_path / "outside"
    outside.mkdir()
    manager.root.parent.mkdir(parents=True)
    manager.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PolicyError):
        manager.deploy(artifact(), True)
