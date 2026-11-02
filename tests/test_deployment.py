"""Deployment transactions tested with real temporary links and fake services."""


import pytest

from scdeploy.errors import BusyError, DeployError, PolicyError, RollbackError
from scdeploy.locks import lock


def test_first_install_and_repeat(manager, artifact):
    archive = artifact()
    assert manager.deploy(archive, True)["status"] == "success"
    assert manager.link.resolve().name == "1.0.0"
    assert manager.services.active("sample.service")
    assert manager.deploy(archive, True)["status"] == "unchanged"
    assert len(manager.state.history("sample")) == 1


def test_update_and_manual_rollback(manager, artifact):
    manager.deploy(artifact(), True)
    manager.deploy(artifact("1.1.0"), True)
    assert manager.state.project("sample")["installed_version"] == "1.1.0"
    manager.rollback(True)
    assert manager.link.resolve().name == "1.0.0"
    assert manager.services.active("sample.service")


@pytest.mark.parametrize("stage", ["stop", "start", "health"])
def test_failed_update_restores_process_and_state(manager, artifact, stage):
    manager.deploy(artifact(), True)
    if stage == "health":
        manager.health.failures = 1
    else:
        manager.services.fail[stage] = 1
    with pytest.raises(DeployError):
        manager.deploy(artifact("1.1.0"), True)
    assert manager.link.resolve().name == "1.0.0"
    assert manager.services.active("sample.service")
    assert manager.state.project("sample")["installed_version"] == "1.0.0"
    assert manager.state.history("sample")[-1]["data"]["rollback"] == "success"


def test_failed_first_install_stops_services(manager, artifact):
    manager.health.failures = 1
    with pytest.raises(DeployError):
        manager.deploy(artifact(), True)
    assert not manager.link.exists()
    assert not manager.services.active("sample.service")
    assert not manager.state.project("sample").get("installed_version")


def test_failed_rollback_is_explicit_and_recoverable(manager, artifact):
    manager.deploy(artifact(), True)
    manager.services.fail["start"] = 2
    with pytest.raises(RollbackError):
        manager.deploy(artifact("1.1.0"), True)
    assert manager.state.project("sample")["recovery_required"]
    assert manager.state.history("sample")[-1]["status"] == "rollback_failed"
    assert manager.recover(True)["recovered"]
    assert manager.link.resolve().name == "1.0.0"
    assert manager.services.active("sample.service")


def test_pending_operation_recovery(manager, artifact):
    manager.deploy(artifact(), True)
    old = manager.state.project("sample")
    old_target = str(manager.link.resolve())
    manager.deploy(artifact("1.1.0"), True)
    operation = manager.state.begin("sample", "deploy", previous=old_target,
                                    service_states={"sample.service": True}, old_state=old,
                                    fingerprint=manager.fingerprint())
    assert manager.recover(True)["recovered"] == [operation]
    assert manager.state.project("sample")["installed_version"] == "1.0.0"
    assert manager.link.resolve().name == "1.0.0"


def test_project_lock_blocks_concurrent_request(manager, artifact):
    archive = artifact()
    with lock(manager.locks / "sample.lock"):
        with pytest.raises(BusyError):
            manager.deploy(archive, True)
    assert not manager.link.exists()


def test_no_approval_or_observation(manager, artifact):
    archive = artifact()
    with pytest.raises(PolicyError):
        manager.deploy(archive)
    manager.config.deploy.observe_only = True
    with pytest.raises(PolicyError):
        manager.deploy(archive, True)


def test_real_current_directory_is_preserved(manager, artifact):
    manager.link.mkdir(parents=True)
    data = manager.link / "valuable.txt"
    data.write_text("keep")
    with pytest.raises(PolicyError):
        manager.deploy(artifact(), True)
    assert data.read_text() == "keep"


def test_downgrade_rejected(manager, artifact):
    manager.deploy(artifact("2.0.0"), True)
    with pytest.raises(PolicyError):
        manager.deploy(artifact("1.0.0"), True)


def test_automatic_default_denies(manager, artifact):
    with pytest.raises(PolicyError):
        manager.deploy(artifact(), automatic=True)
