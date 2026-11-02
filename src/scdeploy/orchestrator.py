"""Application use cases shared by CLI, timers and integration tests."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

from scdeploy.artifacts import ArtifactStore, create_manifest, digest
from scdeploy.builders import Builder
from scdeploy.config import Config
from scdeploy.deployment import DeploymentManager, HealthChecker, Systemd
from scdeploy.errors import DeployError
from scdeploy.execution import Runner, require_nonroot
from scdeploy.locks import build_slot, lock
from scdeploy.notifications import notify
from scdeploy.security import profile
from scdeploy.sources import GitSource, RemoteArtifactSource
from scdeploy.state import State, now

log = logging.getLogger(__name__)


class Orchestrator:
    """Coordinate independent providers without coupling domain operations to argparse."""

    def __init__(self, config: Config, data: Path, logs: Path, secrets: Path,
                 max_builds: int = 2, no_notify: bool = False):
        self.config, self.data, self.logs, self.secrets = config, data, logs, secrets
        self.max_builds, self.no_notify = max_builds, no_notify
        self.state = State(data / "state.db")
        self.runner = Runner(config.build.timeout_seconds)
        self.store = ArtifactStore(data / "artifacts")
        self.services = Systemd(config.deploy.services, self.runner)
        self.health = HealthChecker(self.services, self.runner)
        self.deployment = DeploymentManager(config, self.state, data / "locks", self.services, self.health, self.runner)

    def credentials(self) -> dict:
        """Load only the source credentials required by this operation."""
        return profile(self.secrets, self.config.source.auth_profile) if self.config.source.auth_profile else {}

    def source(self):
        """Select a provider by validated configuration."""
        if self.config.source.type in {"git", "github"}:
            return GitSource(self.config.source, self.runner, self.credentials())
        return RemoteArtifactSource(self.config.source, self.credentials())

    def report(self, operation: str, status: str, started: str, **values) -> None:
        """Send a compact report, never changing the primary operation result."""
        if not self.no_notify:
            state = self.state.project(self.config.project.name)
            notify(self.config, self.secrets, {
                "project": self.config.project.name, "operation": operation, "status": status,
                "started": started, "old_version": state.get("previous_version"),
                "new_version": state.get("installed_version"), "commit": state.get("installed_commit"),
                "healthcheck": state.get("healthcheck"), **values,
            })

    def check(self) -> dict:
        """Compare remote availability with the installed version, recording no deployment permission."""
        operation = self.state.begin(self.config.project.name, "check")
        self.runner.context = {"project_id": self.config.project.name, "correlation_id": operation}
        try:
            metadata = self.source().check()
        except BaseException as error:
            self.state.finish(operation, "failed", error_type=type(error).__name__)
            raise
        old = self.state.project(self.config.project.name)
        changed = metadata["commit"] != old.get("installed_commit")
        if self.config.source.type in {"http", "github-release"}:
            changed = changed or metadata["version"] != old.get("installed_version")
        self.state.update(self.config.project.name, available_version=metadata["version"], available_commit=metadata["commit"], checked_at=now())
        if changed and self.config.updates.mode in {"notify", "automatic"} and metadata["commit"] != old.get("notified_commit"):
            self.report("new-version", "available", now(), available={k: metadata[k] for k in ("version", "commit")})
            self.state.update(self.config.project.name, notified_commit=metadata["commit"])
        self.state.finish(operation, "success", available_version=metadata["version"], commit=metadata["commit"])
        return {"version": metadata["version"], "commit": metadata["commit"], "update_available": changed}

    def fetch(self, commit: str | None = None) -> dict:
        """Fetch into a new managed workspace without touching an existing checkout."""
        require_nonroot()
        if self.config.source.type not in {"git", "github"}:
            raise DeployError("fetch is for Git sources; use deploy for ready artifacts")
        with lock(self.data / "locks" / f"build-{self.config.project.name}.lock"):
            operation = self.state.begin(self.config.project.name, "fetch")
            workspace = self.data / "workspaces" / self.config.project.name / uuid.uuid4().hex
            workspace.parent.mkdir(parents=True, exist_ok=True)
            try:
                sha = self.source().fetch(workspace, commit)
            except BaseException as error:
                self.state.finish(operation, "failed", error_type=type(error).__name__)
                raise
            self.state.finish(operation, "success", commit=sha, workspace=str(workspace))
            return {"workspace": str(workspace), "commit": sha}

    def build(self, version: str | None = None, commit: str | None = None,
              signing_key: Path | None = None, test_only: bool = False,
              source_branch: str | None = None) -> dict:
        """Use a fresh workspace for every build and publish only after tests pass."""
        require_nonroot()
        started = now()
        project = self.config.project.name
        with lock(self.data / "locks" / f"build-{project}.lock"), build_slot(self.data / "locks", self.max_builds):
            operation = self.state.begin(project, "test" if test_only else "build")
            self.runner.log_path = self.logs / project / f"{operation}.log"
            workroot = self.data / "workspaces" / project
            workroot.mkdir(parents=True, exist_ok=True)
            try:
                if self.config.source.type not in {"git", "github"}:
                    raise DeployError("Remote artifact projects do not require local builds")
                with tempfile.TemporaryDirectory(dir=workroot) as temp:
                    root = Path(temp)
                    source = root / "source"
                    sha = self.source().fetch(source, commit)
                    runner = Runner(self.config.build.timeout_seconds, self.runner.log_path)
                    runner.context = {"project_id": project, "build_id": operation, "correlation_id": operation}
                    # Resource limits are delegated to the user's systemd manager.
                    if self.config.build.cpu_quota or self.config.build.memory_max:
                        runner.prefix = ["/usr/bin/systemd-run", "--user", "--scope", "--quiet"]
                        if self.config.build.cpu_quota:
                            runner.prefix += ["-p", "CPUQuota=" + self.config.build.cpu_quota]
                        if self.config.build.memory_max:
                            runner.prefix += ["-p", "MemoryMax=" + self.config.build.memory_max]
                        runner.prefix += ["--"]
                    if os.name == "posix":
                        import grp
                        group = grp.getgrnam("scdeploy-build").gr_gid
                        # The build identity can reach only workspaces, never manager
                        # secrets, state or staged deployment artifacts.
                        self.data.chmod(0o711)
                        (self.data / "workspaces").chmod(0o711)
                        workroot.chmod(0o711)
                        os.chown(root, -1, group)
                        root.chmod(0o2770)
                        for path in [source, *source.rglob("*")]:
                            if path.is_symlink():
                                raise DeployError("Source symlinks are not allowed in a build workspace")
                            os.chown(path, -1, group)
                            mode = 0o2770 if path.is_dir() else 0o770 if path.stat().st_mode & 0o111 else 0o660
                            path.chmod(mode)
                        # Custom environment is administrator-controlled; do not forward
                        # source tokens, profile secrets or manager HOME to the worker.
                        runner.prefix += ["/usr/bin/sudo", "-n", "-u", "scdeploy-build", "/usr/bin/env", "-i", "--",
                                          "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/var/lib/scdeploy-build",
                                          *[f"{k}={v}" for k, v in self.config.build.environment.items()],
                                          sys.executable, "-m", "scdeploy.worker"]
                    builder = Builder(self.config.build, runner)
                    if test_only:
                        builder.test(source)
                        result = {"status": "success", "commit": sha}
                    else:
                        output = root / "publish"
                        builder.build(source, output)
                        release_version = version or self.config.source.tag or f"{sha[:12]}-{operation[:8]}"
                        manifest = create_manifest(output, project, release_version, sha,
                                                   self.config.build.runtime, self.config.build.configuration, signing_key,
                                                   source_branch or (self.config.source.branch if not commit and not self.config.source.tag else None))
                        artifact = self.store.publish(output, manifest)
                        artifact.parent.chmod(0o700)
                        result = {"status": "success", "artifact": str(artifact), "manifest": manifest,
                                  "build_id": operation, "artifact_sha256": digest(artifact),
                                  "commit_version": not version and not self.config.source.tag,
                                  "source_repository": self.config.source.repository}
                self.state.finish(operation, "success", result=result)
                self.state.update(project, build_id=operation)
                self.report("build", "success", started, build_id=operation, commit=sha)
                return result
            except BaseException as error:
                self.state.finish(operation, "failed", error_type=type(error).__name__)
                self.report("build", "failed", started, error_type=type(error).__name__)
                raise

    def deploy_git(self, approved: bool, automatic: bool, force: bool,
                   version: str | None, commit: str | None, signing_key: Path | None) -> dict:
        """Pin upstream HEAD, rebuild a clean checkout, then publish and switch releases."""
        project = self.config.project.name
        with lock(self.data / "locks" / f"update-{project}.lock"):
            pending = [operation for operation in self.state.history(project, pending=True)
                       if operation["kind"] in {"deploy", "rollback"}]
            if pending:
                self.deployment.authorize(approved, automatic)
                with lock(self.data / "locks" / f"{project}.lock"):
                    self.deployment.boundary()
                    self.deployment.recover_locked()
            available = self.check()
            selected = commit or available["commit"]
            baseline = self.state.project(project).get("installed_commit")
            if selected == baseline and not force:
                return {"status": "unchanged", "commit": selected,
                        "version": self.state.project(project).get("installed_version")}
            self.deployment.authorize(approved, automatic)
            self.deployment.boundary()
            self.services.preflight(self.config)
            branch = self.config.source.branch if selected == available["commit"] and not self.config.source.tag else None
            built = self.build(version, selected, signing_key, source_branch=branch)
            expected = {"commit": selected, "version": built["manifest"]["version"], "installed_commit": baseline}
            return self.deployment.deploy(Path(built["artifact"]), approved, automatic, expected,
                                          build_id=built["build_id"])

    def deploy(self, archive: Path | None, approved: bool, automatic: bool = False,
               force: bool = False, version: str | None = None, commit: str | None = None,
               signing_key: Path | None = None) -> dict:
        """Prefer a Git-to-local-build pipeline; ready artifacts remain optional."""
        require_nonroot()
        started = now()
        self.runner.log_path = self.logs / self.config.project.name / f"deploy-{uuid.uuid4().hex}.log"
        failures_before = self.state.project(self.config.project.name).get("consecutive_failures", 0)
        request = self.state.begin(self.config.project.name, "deploy-request", approved=approved, automatic=automatic)
        try:
            expected = None
            if archive:
                result = self.deployment.deploy(archive, approved, automatic)
            elif self.config.source.type in {"git", "github"}:
                result = self.deploy_git(approved, automatic, force, version, commit, signing_key)
            else:
                self.deployment.authorize(approved, automatic)
                source = self.source()
                expected = source.check()
                self.data.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=self.data) as temporary:
                    artifact = Path(temporary) / "download.tar.gz"
                    source.download(expected, artifact)
                    result = self.deployment.deploy(artifact, approved, automatic, expected)
            self.report("deploy", result["status"], started, result=result)
            self.state.finish(request, "success", result=result)
            return result
        except BaseException as error:
            current = self.state.project(self.config.project.name).get("consecutive_failures", 0)
            if current == failures_before:
                self.state.update(self.config.project.name, consecutive_failures=current + 1)
            history = self.state.history(self.config.project.name)
            rollback = history[-1]["data"].get("rollback") if history else None
            self.state.finish(request, "failed", error_type=type(error).__name__, rollback=rollback)
            self.report("deploy", "failed", started, error_type=type(error).__name__, rollback=rollback)
            raise

    def scheduled(self) -> dict:
        """Only explicitly authorized automatic projects may deploy from the timer."""
        metadata = self.check()
        if self.config.updates.mode == "automatic" and metadata["update_available"]:
            self.deployment.authorize(False, automatic=True)
            return self.deploy(None, False, automatic=True)
        return metadata
