"""Journaled systemd deployment with atomic switching and service-aware recovery."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import ssl
import tempfile
import time
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from packaging.version import InvalidVersion, Version

from scdeploy.artifacts import extract, verify
from scdeploy.config import Config, identifier
from scdeploy.errors import DeployError, PolicyError, RollbackError, VerificationError
from scdeploy.execution import Runner
from scdeploy.locks import lock
from scdeploy.security import inside, no_symlink_parents
from scdeploy.state import State, now

log = logging.getLogger(__name__)


class IDeploymentProvider(Protocol):
    """Deployment contract for independent service implementations."""

    def deploy(self, archive: Path, approved: bool = False) -> dict: ...


class IHealthChecker(Protocol):
    """A health checker must fail with an actionable domain error."""

    def check(self, config: Config) -> None: ...


class Systemd:
    """Constrain privileged actions to an explicitly configured service allowlist."""

    def __init__(self, services: list[str], runner: Runner):
        self.services, self.runner = services, runner

    def call(self, action: str, service: str, check: bool = True):
        """Use sudo noninteractively; installation grants exact start/stop commands."""
        if service not in self.services or action not in {"start", "stop", "restart", "is-active", "status"}:
            raise PolicyError("Service action is not authorized")
        args = ["/usr/bin/systemctl", action, service]
        if action in {"start", "stop", "restart"} and os.name == "posix" and os.geteuid() != 0:
            args = ["/usr/bin/sudo", "-n", *args]
        return self.runner.run(args, check=check, timeout=60)

    def active(self, service: str) -> bool:
        """Require an active service, respecting systemctl's return code."""
        result = self.call("is-active", service, check=False)
        return result.returncode == 0 and result.stdout.strip() == "active"

    def preflight(self, config: Config) -> None:
        """Refuse legacy units that do not actually execute the configured current link."""
        for service in self.services:
            result = self.runner.run(["/usr/bin/systemctl", "show", service,
                                      "--property=WorkingDirectory,ExecStart,User,LoadState"], timeout=30)
            values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            current = config.deploy.current_link
            working = values.get("WorkingDirectory", "")
            if (values.get("LoadState") != "loaded" or values.get("User") in {None, "", "root"} or
                    not (working == current or working.startswith(current + "/")) or
                    current + "/" not in values.get("ExecStart", "")):
                raise PolicyError("Service requires reviewed migration to the configured current link and a non-root User")

    def diagnostics(self) -> dict:
        """Read bounded service status and journal snippets without mutations."""
        result = {}
        for service in self.services:
            status = self.call("status", service, check=False)
            journal = self.runner.run(["/usr/bin/journalctl", "-u", service, "--no-pager", "-n", "80"], check=False)
            result[service] = {"status": status.stdout, "journal": journal.stdout}
        return result


class HealthChecker:
    """Retry HTTP, TCP, systemd or trusted commands within a total deadline."""

    def __init__(self, services: Systemd, runner: Runner):
        self.services, self.runner = services, runner

    def check(self, config: Config) -> None:
        """Check both process state and application-specific readiness."""
        for service in config.deploy.services:
            if not self.services.active(service):
                raise DeployError("Application service is not active")
        for check in config.deploy.healthchecks:
            deadline = time.monotonic() + check.timeout_seconds
            healthy = False
            for _ in range(check.attempts):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                timeout = min(5.0, remaining)
                try:
                    if check.type == "http":
                        request = urllib.request.Request(check.url, headers={"User-Agent": "scdeploy-health"})
                        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                            body = response.read(65536).decode("utf-8", errors="replace")
                            healthy = response.status == check.expected_status and (
                                check.body_contains is None or check.body_contains in body)
                    elif check.type == "tcp":
                        with socket.create_connection((check.host, check.port), timeout):
                            healthy = True
                    elif check.type == "systemd":
                        healthy = self.services.active(check.service)
                    else:
                        healthy = self.runner.run(check.command, check=False, timeout=timeout).returncode == 0
                except (OSError, DeployError):
                    healthy = False
                if healthy:
                    break
                time.sleep(min(check.interval_seconds, max(0, deadline - time.monotonic())))
            if not healthy:
                raise DeployError(f"{check.type} healthcheck failed")


def atomic_link(link: Path, target: Path | None) -> None:
    """Swap links in one rename; never replace real files or directories."""
    no_symlink_parents(link.parent)
    if link.exists() and not link.is_symlink():
        raise PolicyError("Current path is a real file or directory; migration is required")
    if target is None:
        link.unlink(missing_ok=True)
    else:
        temporary = link.parent / (".current-" + uuid.uuid4().hex)
        try:
            temporary.symlink_to(target, target_is_directory=True)
            os.replace(temporary, link)
        finally:
            temporary.unlink(missing_ok=True)
    if os.name == "posix":
        fd = os.open(link.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sync_tree(root: Path) -> None:
    """Persist staged immutable files and directories before publishing the link."""
    if os.name != "posix":
        return
    paths = list(root.rglob("*"))
    for path in paths:
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for directory in [p for p in reversed(paths) if p.is_dir() and not p.is_symlink()] + [root]:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class DeploymentManager:
    """Use a write-ahead operation journal to coordinate DB, filesystem and services."""

    def __init__(self, config: Config, state: State, locks: Path,
                 services: Systemd, health: IHealthChecker, runner: Runner):
        self.config, self.state, self.locks = config, state, locks
        self.services, self.health, self.runner = services, health, runner
        self.project = config.project.name
        self.root = Path(config.deploy.release_directory)
        self.link = Path(config.deploy.current_link)

    def fingerprint(self) -> str:
        """Detect configuration changes before replaying a recovery journal."""
        return hashlib.sha256(json.dumps(asdict(self.config.deploy), sort_keys=True).encode()).hexdigest()

    def boundary(self) -> None:
        """Reject aliases and unmanaged release locations before any write."""
        no_symlink_parents(self.root)
        no_symlink_parents(self.link.parent)
        if self.link.is_symlink():
            target = self.link.resolve()
            if target.parent != self.root.resolve() or not target.is_dir():
                raise PolicyError("Current link does not point to a managed release")
        elif self.link.exists():
            raise PolicyError("Current path is not a managed symlink")

    def authorize(self, approved: bool, automatic: bool = False) -> None:
        """Observation and automatic policies cannot be bypassed by a timer."""
        c = self.config
        if not c.project.enabled or c.deploy.observe_only:
            raise PolicyError("Project is disabled or in observation mode")
        if automatic:
            from datetime import datetime, timezone
            hour = datetime.now(timezone.utc).hour
            start, end = c.updates.maintenance_start_utc, c.updates.maintenance_end_utc
            in_window = start <= hour < end if start < end else hour >= start or hour < end
            local_source = c.source.type in {"git", "github"}
            if (c.updates.mode != "automatic" or c.updates.require_approval or
                    (not local_source and (not c.deploy.require_signature or not c.source.public_key)) or
                    c.source.branch not in c.updates.allowed_branches or not in_window):
                raise PolicyError("Automatic deployment policy does not authorize this update")
            if self.state.project(self.project).get("consecutive_failures", 0) >= c.updates.max_failures:
                raise PolicyError("Automatic deployment is suspended after repeated failures")
        elif not approved:
            raise PolicyError("Deployment requires --approve for this selected artifact")

    def version_policy(self, manifest: dict, old: dict, approved: bool,
                       commit_version: bool = False) -> None:
        """Reject downgrade unless an administrator explicitly enabled it."""
        previous = old.get("installed_version")
        if commit_version:
            # Branch HEAD is authoritative for locally built commit-based releases.
            return
        if not previous or previous == manifest["version"]:
            return
        try:
            downgrade = Version(manifest["version"]) < Version(previous)
        except InvalidVersion:
            if not approved:
                raise PolicyError("Automatic updates require comparable version numbers") from None
            return
        if downgrade and not self.config.updates.allow_downgrade:
            raise PolicyError("Downgrade is disabled; use the explicit rollback command")

    def restore(self, data: dict) -> None:
        """Restore old link, readiness and original per-service active states."""
        if data["fingerprint"] != self.fingerprint():
            raise RollbackError("Deployment configuration changed; restore it before recovery")
        previous = Path(data["previous"]) if data.get("previous") else None
        if previous and (previous.parent != self.root or not previous.is_dir() or previous.is_symlink()):
            raise RollbackError("Previous release is unavailable")
        if previous:
            self.verify_release(previous)
        errors = []
        # A stop error must not permit switching files under a live process.
        for service in self.config.deploy.services:
            try:
                self.services.call("stop", service)
            except DeployError:
                errors.append(service)
        if errors:
            raise RollbackError("Cannot stop services during rollback")
        atomic_link(self.link, previous)
        for service, active in data["service_states"].items():
            if active:
                self.services.call("start", service)
        if previous and all(data["service_states"].values()):
            self.health.check(self.config)
        for service, active in data["service_states"].items():
            if self.services.active(service) != active:
                raise RollbackError("Rollback service state differs from the saved state")
        restored = data.get("old_state", {})
        self.state.replace(self.project, {**restored, "recovery_required": False})

    def recover_locked(self) -> list[str]:
        """Replay unfinished deployments under the same project lock."""
        recovered = []
        for operation in self.state.history(self.project, pending=True):
            if operation["kind"] not in {"deploy", "rollback"}:
                continue
            try:
                self.restore(operation["data"])
                self.state.finish(operation["id"], "recovered", rollback="success")
                recovered.append(operation["id"])
            except Exception:
                self.state.finish(operation["id"], "rollback_failed", rollback="failed")
                self.state.update(self.project, recovery_required=True)
                raise RollbackError("Interrupted operation recovery failed") from None
        return recovered

    def recover(self, approved: bool) -> dict:
        """Explicitly recover after a reboot before accepting another deployment."""
        self.authorize(approved)
        with lock(self.locks / f"{self.project}.lock"):
            self.boundary()
            return {"recovered": self.recover_locked()}

    def switch(self, target: Path, manifest: dict, kind: str, approved: bool,
               artifact_hash: str = "") -> dict:
        """Journal before stopping any service; compensate every failed stage."""
        old = self.state.project(self.project)
        previous = str(self.link.resolve()) if self.link.is_symlink() else None
        if previous == str(target):
            return {"status": "unchanged", "version": manifest["version"]}
        states = {s: self.services.active(s) for s in self.config.deploy.services}
        data = {"previous": previous, "target": str(target), "service_states": states,
                "old_state": old, "fingerprint": self.fingerprint(), "approved": approved,
                "actor": os.environ.get("SUDO_USER", os.environ.get("USER", "unknown")),
                "artifact_hash": artifact_hash, "manifest": manifest}
        operation = self.state.begin(self.project, kind, **data)
        self.runner.context = {"project_id": self.project, "deployment_id": operation, "correlation_id": operation}
        if self.runner.log_path:
            self.runner.log_path = self.runner.log_path.parent / f"{operation}.log"
        try:
            for service in self.config.deploy.services:
                self.services.call("stop", service)
            atomic_link(self.link, target)
            for service in self.config.deploy.services:
                self.services.call("start", service)
            self.health.check(self.config)
            self.state.update(self.project, installed_version=manifest["version"],
                              installed_commit=manifest["commit"], previous_version=old.get("installed_version"),
                              previous_release=previous, installed_release=str(target),
                              artifact_id=artifact_hash, installation_date=now(), installation_result="success",
                              source_repository=self.config.source.repository, approval=approved,
                              healthcheck="success", deployment_id=operation, consecutive_failures=0,
                              recovery_required=False)
            self.state.finish(operation, "success", healthcheck="success")
            return {"status": "success", "version": manifest["version"], "operation_id": operation}
        except BaseException as error:
            try:
                self.restore(data)
                # A failed first install must not retain metadata written before a crash.
                self.state.update(self.project, consecutive_failures=old.get("consecutive_failures", 0) + 1)
                self.state.finish(operation, "failed", rollback="success", healthcheck="failed",
                                  error_type=type(error).__name__)
            except BaseException:
                self.state.finish(operation, "rollback_failed", rollback="failed")
                self.state.update(self.project, recovery_required=True)
                raise RollbackError("Deployment failed and rollback failed; run recover") from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise DeployError("Deployment failed; previous service state restored") from error

    def deploy(self, archive: Path, approved: bool = False, automatic: bool = False,
               expected: dict | None = None, build_id: str | None = None) -> dict:
        """Verify and stage a release without touching the running application."""
        from scdeploy.artifacts import digest
        self.authorize(approved, automatic)
        with lock(self.locks / f"{self.project}.lock"):
            self.boundary()
            self.recover_locked()
            if expected and "installed_commit" in expected:
                if self.state.project(self.project).get("installed_commit") != expected["installed_commit"]:
                    raise PolicyError("Installed release changed during the build; retry the update")
            self.services.preflight(self.config)
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".staging-", dir=self.root) as tmp:
                stage = Path(tmp)
                snapshot = stage / "artifact.tar.gz"
                # Verify the exact bytes copied into private staging, avoiding archive TOCTOU.
                shutil.copyfile(archive, snapshot)
                artifact_hash = digest(snapshot)
                payload = stage / "payload"
                extract(snapshot, payload)
                record = self.local_build(build_id) if build_id else None
                if record and artifact_hash != record["artifact_sha256"]:
                    raise VerificationError("Local build archive changed after publication")
                manifest = self.verify_payload(payload, record=record)
                if automatic and manifest.get("source_branch") != self.config.source.branch:
                    raise VerificationError("Automatic update requires a trusted matching source_branch")
                if automatic and self.config.source.type in {"git", "github"} and not record:
                    raise VerificationError("Automatic Git deployment requires a recorded local build")
                if manifest["runtime"] != self.config.build.runtime:
                    raise VerificationError("Artifact runtime does not match configured runtime")
                expected_keys = ("commit",) if expected and expected.get("zip") else ("commit", "version")
                if expected and any(manifest.get(k) != expected[k] for k in expected_keys):
                    raise VerificationError("Downloaded artifact does not match selected update")
                self.version_policy(manifest, self.state.project(self.project), approved,
                                    bool(record and record.get("commit_version")))
                target = self.root / identifier(manifest["version"])
                if target.exists():
                    existing = self.verify_release(target)
                    if existing != manifest:
                        raise VerificationError("Installed version conflicts with selected artifact")
                else:
                    for command in self.config.deploy.pre_deploy:
                        self.runner.run(command, cwd=payload)
                    self.verify_payload(payload, record=record)
                    self.attach_shared(payload)
                    sync_tree(payload)
                    os.rename(payload, target)
                    if os.name == "posix":
                        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                result = self.switch(target, manifest, "deploy", approved, artifact_hash)
            try:
                self.cleanup()
            except OSError:
                log.warning("Deployment succeeded; release retention cleanup needs administrator attention")
                result["cleanup_warning"] = True
            return result

    def local_build(self, build_id: str) -> dict:
        """Load a completed manager-owned build attestation from the audit ledger."""
        for operation in self.state.history(self.project):
            if operation["id"] == build_id and operation["kind"] == "build" and operation["status"] == "success":
                result = operation["data"].get("result", {})
                if "artifact_sha256" in result and "manifest" in result:
                    return result
        raise VerificationError("No successful local build record for this project")

    def verify_payload(self, directory: Path, record: dict | None = None,
                       retained: bool = False) -> dict:
        """Authenticate local builds via the protected ledger, remote files via signatures."""
        public_key = Path(self.config.source.public_key) if self.config.source.public_key else None
        manifest = verify(directory, self.project, public_key, require_signature=False)
        if retained and record is None:
            for operation in self.state.history(self.project):
                result = operation["data"].get("result", {})
                if (operation["kind"] == "build" and operation["status"] == "success"
                        and result.get("manifest") == manifest and result.get("artifact_sha256")):
                    record = result
                    break
        if record is not None and record.get("manifest") != manifest:
            raise VerificationError("Payload does not match the successful local build")
        if self.config.deploy.require_signature and not manifest.get("signature") and record is None:
            raise VerificationError("Remote or unrecorded artifacts require a trusted signature")
        return manifest

    def attach_shared(self, payload: Path) -> None:
        """Attach pre-existing mutable state without copying or overwriting it."""
        root = self.root.parent / "shared"
        no_symlink_parents(root)
        for name, value in self.config.deploy.shared.items():
            source = inside(root, value)
            target = inside(payload, name)
            if not source.exists() or target.exists():
                raise PolicyError("Shared source must exist and publication target must be absent")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=source.is_dir())

    def rollback(self, approved: bool, version: str | None = None) -> dict:
        """Explicitly switch to a retained release while journaling rollback itself."""
        self.authorize(approved)
        with lock(self.locks / f"{self.project}.lock"):
            self.boundary()
            self.recover_locked()
            self.services.preflight(self.config)
            old = self.state.project(self.project)
            target = self.root / identifier(version) if version else Path(old.get("previous_release") or "")
            if target.parent != self.root or not target.is_dir() or target.is_symlink():
                raise PolicyError("No retained release available for rollback")
            manifest = self.verify_release(target)
            return self.switch(target, manifest, "rollback", approved)

    def verify_release(self, target: Path) -> dict:
        """Verify immutable payload while checking configured shared link destinations."""
        # Shared links are added after manifest verification and are excluded from the signed inventory.
        if self.config.deploy.shared:
            with tempfile.TemporaryDirectory(prefix=".verify-", dir=self.root) as tmp:
                copy = Path(tmp)
                shared = set(self.config.deploy.shared)
                for path in target.rglob("*"):
                    name = path.relative_to(target).as_posix()
                    if name in shared:
                        expected = inside(self.root.parent / "shared", self.config.deploy.shared[name])
                        if not path.is_symlink() or path.resolve() != expected.resolve():
                            raise VerificationError("Shared link destination changed")
                        continue
                    if path.is_symlink():
                        raise VerificationError("Unexpected release symlink")
                    dest = copy / path.relative_to(target)
                    if path.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, dest)
                return self.verify_payload(copy, retained=True)
        return self.verify_payload(target, retained=True)

    def cleanup(self) -> None:
        """Retain current, previous and newest releases; never remove shared state."""
        state = self.state.project(self.project)
        protected = {state.get("installed_release"), state.get("previous_release")}
        releases = sorted((p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()
                           and not p.name.startswith(".") and (p / "manifest.json").is_file()),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        keep = {str(p) for p in releases[:self.config.deploy.rollback.keep_releases]} | protected
        for release in releases:
            if str(release) not in keep:
                shutil.rmtree(release)
