"""Use-case integration with isolated providers and real artifact/state storage."""

from pathlib import Path

import pytest

from scdeploy.builders import Builder
from scdeploy.errors import DeployError


def test_build_use_case_publishes_only_success(engine, monkeypatch):
    def build(self, source, output):
        output.mkdir()
        (output / "application").write_text("tested application")
    monkeypatch.setattr(Builder, "build", build)
    result = engine.build("1.0.0")
    assert Path(result["artifact"]).is_file()
    history = engine.state.history("sample")
    assert history[-1]["status"] == "success"
    assert engine.state.project("sample")["build_id"] == history[-1]["id"]


def test_failed_build_preserves_installed_version(engine, monkeypatch):
    engine.state.update("sample", installed_version="0.9.0", installed_commit="b" * 40)
    def fail(*args):
        raise DeployError("simulated build failure")
    monkeypatch.setattr(Builder, "build", fail)
    with pytest.raises(DeployError):
        engine.build("1.0.0")
    assert engine.state.project("sample")["installed_version"] == "0.9.0"
    assert not engine.store.list("sample")
    assert engine.state.history("sample")[-1]["status"] == "failed"


def test_same_commit_with_explicit_version_is_not_new_update(engine):
    engine.state.update("sample", installed_version="1.0.0", installed_commit="a" * 40)
    assert not engine.check()["update_available"]
    assert engine.state.history("sample")[-1]["kind"] == "check"


@pytest.mark.parametrize("outcome", ["success", "build-failure", "fetch-failure", "publish-failure", "interrupt", "test"])
def test_build_cleans_temporary_workspace(engine, monkeypatch, outcome):
    workspaces = []
    original_fetch = engine.source().fetch

    def fetch(destination, commit=None):
        workspaces.append(destination.parent)
        sha = original_fetch(destination, commit)
        if outcome == "fetch-failure":
            raise DeployError("checkout failed after creating files")
        return sha

    source = engine.source()
    monkeypatch.setattr(source, "fetch", fetch)
    monkeypatch.setattr(engine, "source", lambda: source)

    def build(self, source, output):
        cache = source / "node_modules" / "cache"
        cache.mkdir(parents=True)
        (cache / "temporary").write_text("build cache")
        output.mkdir()
        (output / "application").write_text("application")
        if outcome == "build-failure":
            raise DeployError("build failed after creating files")
        if outcome == "interrupt":
            raise KeyboardInterrupt

    monkeypatch.setattr(Builder, "build", build)
    monkeypatch.setattr(Builder, "test", lambda self, source: (source / "test-cache").write_text("cache"))
    if outcome == "publish-failure":
        def fail_publish(*args):
            raise DeployError("publication failed")
        monkeypatch.setattr(engine.store, "publish", fail_publish)

    if outcome in {"build-failure", "fetch-failure", "publish-failure", "interrupt"}:
        with pytest.raises(KeyboardInterrupt if outcome == "interrupt" else DeployError):
            engine.build("1.0.0")
    else:
        result = engine.build("1.0.0", test_only=outcome == "test")
        if outcome == "success":
            assert Path(result["artifact"]).is_file()

    assert workspaces
    assert all(not workspace.exists() for workspace in workspaces)
    assert list((engine.data / "workspaces" / "sample").iterdir()) == []
