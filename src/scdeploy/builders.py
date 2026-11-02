"""Independent build providers operating exclusively inside owned workspaces."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol

from scdeploy.config import Build
from scdeploy.errors import DeployError, PolicyError
from scdeploy.execution import Runner, require_nonroot
from scdeploy.security import inside


class IBuildProvider(Protocol):
    """Build provider contract."""

    def build(self, source: Path, output: Path) -> None: ...
    def test(self, source: Path) -> None: ...


def copy_payload(source: Path, output: Path) -> None:
    """Copy application files while rejecting links and development caches."""
    if not source.is_dir():
        raise DeployError("Configured publication directory does not exist")
    ignored = {".git", ".venv", "__pycache__", ".pytest_cache", ".scdeploy-build"}
    for path in source.rglob("*"):
        if ignored.intersection(path.relative_to(source).parts):
            continue
        if path.is_symlink():
            raise PolicyError("Published application cannot contain symbolic links")
        target = output / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        else:
            raise PolicyError("Published application contains a special file")


class Builder:
    """Dispatch ecosystem-specific command plans through a checked runner."""

    def __init__(self, config: Build, runner: Runner):
        self.config, self.runner = config, runner

    def command(self, args: list[str], cwd: Path) -> str:
        """Apply the configured build deadline and controlled environment."""
        return self.runner.run(args, cwd=cwd, env=self.config.environment).stdout

    def python(self, root: Path) -> str:
        """Create a build-local virtual environment; it is never packaged."""
        c = self.config
        venv = root / ".scdeploy-build"
        self.command([c.python_executable, "-m", "venv", str(venv)], root)
        executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        packages = ["pytest"] if c.tests else []
        if c.distribution:
            packages.append("build")
        if packages:
            self.command([str(executable), "-m", "pip", "install", *packages], root)
        if c.requirements:
            req = inside(root, c.requirements)
            self.command([str(executable), "-m", "pip", "install", "-r", str(req)], root)
        return str(executable)

    def test(self, source: Path) -> None:
        """Run tests without altering a deployed application."""
        require_nonroot()
        c = self.config
        root = inside(source, c.working_directory)
        if c.provider == "dotnet":
            self.command(["dotnet", "test", "-c", c.configuration], root)
        elif c.provider == "node":
            self.command(["npm", "test"], root)
        elif c.provider == "python":
            self.command([self.python(root), "-m", "pytest"], root)
        else:
            if not c.test_commands:
                raise DeployError("Custom tests require test_commands")
            for command in c.test_commands:
                self.command(command, root)

    def build(self, source: Path, output: Path) -> None:
        """Build and test before emitting files into an isolated artifact tree."""
        require_nonroot()
        c = self.config
        root = inside(source, c.working_directory)
        output.mkdir(parents=True)
        if os.name == "posix":
            output.chmod(0o2770)
        if c.provider == "dotnet":
            if not c.projects:
                raise DeployError("dotnet builds require projects")
            for project in c.projects:
                path = str(inside(root, project.path))
                common = ["-r", c.runtime, "-p:SelfContained=" + str(c.self_contained).lower()]
                if c.restore:
                    restore = ["dotnet", "restore", path, *common]
                    if c.nuget_config:
                        restore += ["--configfile", str(inside(root, c.nuget_config))]
                    self.command(restore, root)
                self.command(["dotnet", "build", path, "-c", c.configuration, "--no-restore", *common], root)
            if c.tests:
                self.test(source)
            for project in c.projects:
                self.command(["dotnet", "publish", str(inside(root, project.path)), "-c", c.configuration,
                              "-r", c.runtime, "--self-contained", str(c.self_contained).lower(),
                              "--no-restore", "-o", str(inside(output, project.output))], root)
            return
        if c.provider == "node":
            if c.node_major:
                version = self.command(["node", "--version"], root).strip().lstrip("v").split(".")[0]
                if version != str(c.node_major):
                    raise DeployError("Installed Node.js does not match node_major")
            self.command(["npm", "ci" if (root / "package-lock.json").is_file() else "install"], root)
            if c.tests:
                self.test(source)
            self.command(["npm", "run", "build"], root)
        elif c.provider == "python":
            executable = self.python(root)
            if c.tests:
                self.command([executable, "-m", "pytest"], root)
            if c.distribution:
                self.command([executable, "-m", "build", "--outdir", c.publish_directory], root)
        else:
            if not c.commands:
                raise DeployError("Custom builds require commands")
            for command in c.commands:
                self.command(command, root)
            if c.tests:
                self.test(source)
        copy_payload(inside(root, c.publish_directory), output)


PROVIDERS = {name: Builder for name in ("dotnet", "node", "python", "command")}
