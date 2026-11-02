"""Isolated fixtures; no production paths or real service mutations."""

import os
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scdeploy.artifacts import ArtifactStore, create_manifest
from scdeploy.config import Build, Config, Deploy, Project, Source
from scdeploy.deployment import DeploymentManager
from scdeploy.errors import DeployError
from scdeploy.execution import Runner
from scdeploy.state import State
from scdeploy.orchestrator import Orchestrator


class FakeSource:
    """Deterministic source for orchestrator tests without external Git."""

    def check(self):
        return {"commit": "a" * 40, "version": "a" * 12}

    def fetch(self, destination, commit=None):
        destination.mkdir()
        (destination / "source.txt").write_text("input")
        return "a" * 40


@pytest.fixture
def engine(config, tmp_path, monkeypatch):
    engine = Orchestrator(config, tmp_path / "data", tmp_path / "logs", tmp_path / "secrets", no_notify=True)
    monkeypatch.setattr(engine, "source", lambda: FakeSource())
    monkeypatch.setattr("scdeploy.orchestrator.require_nonroot", lambda: None)
    if os.name == "posix":
        monkeypatch.setattr("grp.getgrnam", lambda name: SimpleNamespace(gr_gid=os.getgid()))
    return engine


@pytest.fixture
def config(tmp_path):
    app = tmp_path / "app"
    return Config(project=Project("sample", environment="staging"),
                  source=Source("git", repository="https://example.com/repo.git"),
                  build=Build(provider="command", tests=False),
                  deploy=Deploy(str(app / "releases"), str(app / "current"),
                                services=["sample.service"], observe_only=False, require_signature=False))


class FakeServices:
    def __init__(self):
        self.states = {"sample.service": False}
        self.calls = []
        self.fail = {}

    def preflight(self, config):
        pass

    def active(self, name):
        return self.states[name]

    def call(self, action, name):
        self.calls.append((action, name))
        if self.fail.get(action, 0):
            self.fail[action] -= 1
            raise DeployError("simulated service failure")
        self.states[name] = action != "stop"


class FakeHealth:
    def __init__(self):
        self.failures = 0

    def check(self, config):
        if self.failures:
            self.failures -= 1
            raise DeployError("simulated unhealthy application")


@pytest.fixture
def manager(config, tmp_path, monkeypatch):
    manager = DeploymentManager(config, State(tmp_path / "state.db"), tmp_path / "locks",
                                FakeServices(), FakeHealth(), Runner())
    if os.name == "nt":
        # The managed Windows sandbox lacks SeCreateSymbolicLinkPrivilege.
        # Exercise transaction logic with an explicit link adapter; Ubuntu CI
        # uses real symlinks and os.replace without this adapter.
        cls = type(manager.link)
        original_resolve, original_islink, original_exists = cls.resolve, cls.is_symlink, cls.exists
        target = [None]
        monkeypatch.setattr(cls, "resolve", lambda p, *a, **kw: target[0] if p == manager.link and target[0] else original_resolve(p, *a, **kw))
        monkeypatch.setattr(cls, "is_symlink", lambda p: bool(target[0]) if p == manager.link else original_islink(p))
        monkeypatch.setattr(cls, "exists", lambda p: bool(target[0]) or original_exists(p) if p == manager.link else original_exists(p))

        def switch(link, value):
            if original_exists(link) and not original_islink(link):
                from scdeploy.errors import PolicyError
                raise PolicyError("Real directory preserved")
            target[0] = value

        monkeypatch.setattr("scdeploy.deployment.atomic_link", switch)
    return manager


@pytest.fixture
def artifact(tmp_path):
    def make(version="1.0.0", project="sample", signing_key=None, source_branch=None):
        output = tmp_path / f"payload-{project}-{version}"
        output.mkdir()
        (output / "app.txt").write_text("version " + version)
        manifest = create_manifest(output, project, version, "a" * 40, "linux-x64", "Release", signing_key, source_branch)
        return ArtifactStore(tmp_path / "artifacts").publish(output, manifest)
    return make


@pytest.fixture
def keys(tmp_path):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo))
    return private, public
