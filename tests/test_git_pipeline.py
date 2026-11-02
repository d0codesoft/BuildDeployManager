"""The primary Git clone/build/publish/release pipeline and its trust boundary."""

from pathlib import Path

import pytest

from scdeploy.artifacts import digest
from scdeploy.builders import Builder
from scdeploy.errors import DeployError, PolicyError, VerificationError


class Upstream:
    def __init__(self):
        self.sha = "a" * 40
        self.checkouts = []

    def check(self):
        return {"commit": self.sha, "version": self.sha[:12]}

    def fetch(self, destination, commit=None):
        selected = commit or self.sha
        self.checkouts.append((destination, selected))
        destination.mkdir()
        (destination / "source").write_text(selected)
        return selected


@pytest.fixture
def pipeline(engine, manager, monkeypatch):
    upstream = Upstream()
    manager.state = engine.state
    engine.deployment = manager
    engine.services = manager.services
    engine.config.deploy.require_signature = True
    monkeypatch.setattr(engine, "source", lambda: upstream)
    built = []
    def build(self, source, output):
        built.append((source, len(manager.services.calls)))
        output.mkdir()
        (output / "application").write_text((source / "source").read_text())
    monkeypatch.setattr(Builder, "build", build)
    return engine, manager, upstream, built


def test_git_deploy_builds_and_installs_without_remote_signature(pipeline):
    engine, manager, upstream, built = pipeline
    result = engine.deploy(None, True)
    assert result["status"] == "success"
    assert engine.state.project("sample")["installed_commit"] == upstream.sha
    assert len(upstream.checkouts) == len(built) == 1
    assert built[0][1] == 0  # Build completed before any service was stopped.
    records = [op for op in engine.state.history("sample") if op["kind"] == "build"]
    record = records[0]["data"]["result"]
    assert record["artifact_sha256"] == digest(Path(record["artifact"]))
    assert "signature" not in record["manifest"]
    assert manager.services.active("sample.service")


def test_same_commit_skips_clone_build_and_service_actions(pipeline):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    calls = list(manager.services.calls)
    assert engine.deploy(None, True)["status"] == "unchanged"
    assert len(upstream.checkouts) == len(built) == 1
    assert manager.services.calls == calls


def test_new_commit_forces_clean_checkout_and_publishes_new_release(pipeline):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    previous = engine.state.project("sample")["installed_release"]
    upstream.sha = "b" * 40
    engine.deploy(None, True)
    assert engine.state.project("sample")["installed_commit"] == "b" * 40
    assert engine.state.project("sample")["previous_release"] == previous
    assert upstream.checkouts[0][0] != upstream.checkouts[1][0]
    assert upstream.checkouts[1][1] == "b" * 40


def test_force_rebuilds_same_commit_and_local_rollback_works(pipeline):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    original = manager.link.resolve()
    engine.deploy(None, True, force=True)
    assert len(upstream.checkouts) == 2
    assert manager.link.resolve() != original
    manager.rollback(True)
    assert manager.link.resolve() == original
    assert manager.services.active("sample.service")


def test_failed_source_build_leaves_current_running(pipeline, monkeypatch):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    original, calls = manager.link.resolve(), list(manager.services.calls)
    upstream.sha = "b" * 40
    def fail(*args):
        raise DeployError("build failed")
    monkeypatch.setattr(Builder, "build", fail)
    with pytest.raises(DeployError):
        engine.deploy(None, True)
    assert manager.link.resolve() == original
    assert manager.services.calls == calls
    assert engine.state.project("sample")["installed_commit"] == "a" * 40


def test_unsigned_external_archive_cannot_claim_local_build(pipeline):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    archive = engine.store.list("sample")[0]
    with pytest.raises(VerificationError):
        manager.deploy(archive, True)
    with pytest.raises(VerificationError):
        manager.deploy(archive, True, build_id="invented")


def test_changed_local_archive_is_rejected(pipeline, tmp_path):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    record = next(op for op in engine.state.history("sample") if op["kind"] == "build")
    tampered = tmp_path / "changed.tar.gz"
    tampered.write_bytes(Path(record["data"]["result"]["artifact"]).read_bytes() + b"tampered")
    with pytest.raises(VerificationError, match="changed"):
        manager.deploy(tampered, True, build_id=record["id"])


def test_automatic_git_build_uses_branch_policy_and_local_attestation(pipeline):
    engine, manager, upstream, built = pipeline
    engine.config.updates.mode = "automatic"
    engine.config.updates.require_approval = False
    engine.config.updates.allowed_branches = ["main"]
    assert engine.scheduled()["status"] == "success"
    upstream.sha = "b" * 40
    assert engine.scheduled()["status"] == "success"
    assert len(built) == 2


def test_concurrent_manual_change_during_build_aborts_stale_pipeline(pipeline, monkeypatch):
    engine, manager, upstream, built = pipeline
    engine.deploy(None, True)
    upstream.sha = "b" * 40
    original = Builder.build
    def change_current(self, source, output):
        original(self, source, output)
        engine.state.update("sample", installed_commit="c" * 40)
    monkeypatch.setattr(Builder, "build", change_current)
    with pytest.raises(PolicyError, match="changed during"):
        engine.deploy(None, True)
    assert manager.link.resolve().name.startswith("a" * 12)
