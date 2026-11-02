"""Versioned, strictly typed configuration using standard-library dataclasses."""

from __future__ import annotations

import re
import types
import math
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints
from urllib.parse import urlsplit

import yaml

from scdeploy.errors import ConfigError


def identifier(value: str) -> str:
    """Reject path components and option-like identifiers."""
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value) or value in {".", ".."}:
        raise ConfigError("Invalid identifier")
    return value


def relative(value: str) -> str:
    """Validate a portable relative path before touching a filesystem."""
    if not value or "\\" in value or ":" in value or "\x00" in value:
        raise ConfigError("Invalid relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts:
        raise ConfigError("Path must remain inside its managed directory")
    return value


@dataclass
class Project:
    name: str
    description: str = ""
    enabled: bool = True
    environment: str = "production"


@dataclass
class Source:
    type: Literal["git", "github", "github-actions", "github-release", "http"]
    repository: str = ""
    branch: str = "main"
    tag: str | None = None
    auth_profile: str | None = None
    workflow: str | None = None
    artifact_name: str = "application"
    metadata_url: str | None = None
    trusted_hosts: list[str] = field(default_factory=list)
    # Public Ed25519 keys are administrator-controlled trust anchors.
    public_key: str | None = None


@dataclass
class BuildProject:
    path: str
    output: str


@dataclass
class Build:
    provider: Literal["dotnet", "node", "python", "command"] = "dotnet"
    configuration: Literal["Release", "Debug"] = "Release"
    runtime: str = "linux-x64"
    self_contained: bool = True
    projects: list[BuildProject] = field(default_factory=list)
    restore: bool = True
    tests: bool = True
    timeout_seconds: int = 1800
    working_directory: str = "."
    publish_directory: str = "dist"
    nuget_config: str | None = None
    node_major: int | None = None
    python_executable: str = "python3"
    requirements: str | None = None
    distribution: bool = False
    commands: list[list[str]] = field(default_factory=list)
    test_commands: list[list[str]] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    cpu_quota: str | None = None
    memory_max: str | None = None


@dataclass
class Healthcheck:
    type: Literal["http", "tcp", "systemd", "command"] = "http"
    url: str = ""
    expected_status: int = 200
    body_contains: str | None = None
    host: str = "127.0.0.1"
    port: int = 0
    service: str = ""
    command: list[str] = field(default_factory=list)
    timeout_seconds: int = 60
    interval_seconds: float = 2.0
    attempts: int = 30


@dataclass
class Rollback:
    enabled: bool = True
    keep_releases: int = 5


@dataclass
class Deploy:
    release_directory: str
    current_link: str
    provider: Literal["systemd-symlink"] = "systemd-symlink"
    services: list[str] = field(default_factory=list)
    healthchecks: list[Healthcheck] = field(default_factory=list)
    rollback: Rollback = field(default_factory=Rollback)
    shared: dict[str, str] = field(default_factory=dict)
    pre_deploy: list[list[str]] = field(default_factory=list)
    # Observation is the safe default for imported installations.
    observe_only: bool = True
    require_signature: bool = True


@dataclass
class Updates:
    mode: Literal["manual", "notify", "automatic"] = "manual"
    check_interval: str = "30m"
    allowed_branches: list[str] = field(default_factory=list)
    require_approval: bool = True
    allow_downgrade: bool = False
    maintenance_start_utc: int = 0
    maintenance_end_utc: int = 24
    max_failures: int = 3


@dataclass
class Notification:
    enabled: bool = False
    profile: str = ""
    on_success: bool = True
    on_failure: bool = True


@dataclass
class Notifications:
    email: Notification = field(default_factory=Notification)
    telegram: Notification = field(default_factory=Notification)


@dataclass
class Config:
    project: Project
    source: Source
    deploy: Deploy
    schema_version: Literal[1] = 1
    build: Build = field(default_factory=Build)
    updates: Updates = field(default_factory=Updates)
    notifications: Notifications = field(default_factory=Notifications)


def decode(expected: Any, value: Any, location: str = "config") -> Any:
    """Recursively validate exact types, required fields and unknown keys."""
    origin, args = get_origin(expected), get_args(expected)
    if origin is Literal:
        if value not in args or type(value) is not type(args[0]):
            raise ConfigError(f"Unsupported value at {location}")
        return value
    if origin in (Union, types.UnionType):
        for item in args:
            try:
                return decode(item, value, location)
            except ConfigError:
                pass
        raise ConfigError(f"Invalid type at {location}")
    if is_dataclass(expected):
        if not isinstance(value, dict):
            raise ConfigError(f"Expected mapping at {location}")
        known = {f.name for f in fields(expected)}
        if set(value) - known:
            raise ConfigError(f"Unknown fields at {location}")
        hints = get_type_hints(expected)
        try:
            return expected(**{k: decode(hints[k], v, f"{location}.{k}") for k, v in value.items()})
        except TypeError:
            raise ConfigError(f"Missing required fields at {location}") from None
    if origin is list:
        if not isinstance(value, list):
            raise ConfigError(f"Expected list at {location}")
        return [decode(args[0], x, location) for x in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"Expected mapping at {location}")
        return {decode(args[0], k, location): decode(args[1], v, location) for k, v in value.items()}
    if type(value) is not expected:
        if expected is float and type(value) is int:
            return float(value)
        raise ConfigError(f"Invalid type at {location}")
    return value


def validate(config: Config) -> Config:
    """Validate cross-field constraints and paths before an operation."""
    c = config
    identifier(c.project.name)
    for path in [c.deploy.release_directory, c.deploy.current_link]:
        if not (Path(path).is_absolute() or PurePosixPath(path).is_absolute()) or ".." in Path(path).parts:
            raise ConfigError("Deployment paths must be absolute and normalized")
    releases, link = Path(c.deploy.release_directory), Path(c.deploy.current_link)
    if releases.parent != link.parent or link == releases or releases.parent == Path(releases.anchor):
        raise ConfigError("Releases and current must be siblings inside a dedicated application directory")
    for service in c.deploy.services:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]*\.service", service):
            raise ConfigError("Invalid systemd service name")
    if c.build.timeout_seconds <= 0 or c.deploy.rollback.keep_releases < 2:
        raise ConfigError("Timeout must be positive; retain at least two releases")
    if not c.deploy.rollback.enabled:
        raise ConfigError("Automatic recovery cannot be disabled")
    relative(c.build.working_directory)
    relative(c.build.publish_directory)
    outputs = []
    for item in c.build.projects:
        relative(item.path)
        relative(item.output)
        out = PurePosixPath(item.output)
        if any(out == x or out in x.parents or x in out.parents for x in outputs):
            raise ConfigError("Build outputs must not overlap")
        outputs.append(out)
    for path in [c.build.nuget_config, c.build.requirements]:
        if path:
            relative(path)
    for key in c.build.environment:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError("Invalid build environment variable name")
    for target, shared in c.deploy.shared.items():
        relative(target)
        relative(shared)
        if target == "manifest.json":
            raise ConfigError("Cannot replace manifest with shared state")
    if not re.fullmatch(r"[1-9][0-9]*[smh]", c.updates.check_interval):
        raise ConfigError("check_interval must be a positive duration, e.g. 30m")
    if not 0 <= c.updates.maintenance_start_utc < 24 or not 0 < c.updates.maintenance_end_utc <= 24:
        raise ConfigError("Invalid UTC maintenance window")
    if c.updates.max_failures < 1:
        raise ConfigError("max_failures must be positive")
    for check in c.deploy.healthchecks:
        if (check.timeout_seconds <= 0 or check.attempts <= 0 or check.interval_seconds < 0
                or not math.isfinite(check.interval_seconds)):
            raise ConfigError("Invalid healthcheck bounds")
        if check.type == "http" and urlsplit(check.url).scheme not in {"http", "https"}:
            raise ConfigError("Invalid HTTP healthcheck URL")
        if check.type == "tcp" and not 1 <= check.port <= 65535:
            raise ConfigError("Invalid TCP healthcheck port")
        if check.type == "systemd" and check.service not in c.deploy.services:
            raise ConfigError("Healthcheck service must belong to the project")
        if check.type == "command" and not check.command:
            raise ConfigError("Empty healthcheck command")
    for profile in [c.source.auth_profile, c.notifications.email.profile, c.notifications.telegram.profile]:
        if profile:
            identifier(profile)
    if c.source.type.startswith("github"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", c.source.repository):
            raise ConfigError("GitHub repository must be owner/name")
    if c.source.type == "github-actions" and not c.source.workflow:
        raise ConfigError("GitHub Actions requires workflow")
    if c.source.type == "github-release" and not c.source.tag:
        raise ConfigError("GitHub Releases requires tag")
    if c.source.type == "http" and (not c.source.metadata_url or not c.source.trusted_hosts):
        raise ConfigError("HTTP source requires metadata_url and trusted_hosts")
    if c.source.metadata_url:
        url = urlsplit(c.source.metadata_url)
        if url.scheme != "https" or url.username or url.password or not url.hostname:
            raise ConfigError("Metadata URL must be HTTPS without embedded credentials")
    for channel in [c.notifications.email, c.notifications.telegram]:
        if channel.enabled and not channel.profile:
            raise ConfigError("Enabled notifications require a profile reference")
    for ref in [c.source.branch, c.source.tag]:
        if ref and (ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref) or ".." in ref):
            raise ConfigError("Invalid Git ref")
    if c.source.type == "git":
        repo = c.source.repository
        parts = urlsplit(repo)
        if not repo or parts.password or (parts.scheme == "https" and parts.username):
            raise ConfigError("Invalid Git repository or embedded credentials")
        if parts.scheme not in {"https", "ssh", "file", ""} or repo.startswith("-"):
            raise ConfigError("Unsupported Git transport")
    return c


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML fields instead of silently overriding them."""


def _mapping(loader: UniqueLoader, node: Any) -> dict:
    result = {}
    for key, value in node.value:
        k = loader.construct_object(key)
        if not isinstance(k, str) or k in result:
            raise ConfigError("Duplicate or non-string YAML field")
        result[k] = loader.construct_object(value)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load(path: Path) -> Config:
    """Read an administrator-owned YAML configuration without echoing values."""
    try:
        if path.stat().st_size > 1_000_000:
            raise ConfigError("Configuration exceeds size limit")
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        return validate(decode(Config, data))
    except (OSError, yaml.YAMLError, RecursionError):
        raise ConfigError("Cannot read project YAML") from None


def load_projects(directory: Path) -> dict[str, Config]:
    """Load all projects, rejecting duplicate identities and overlapping roots."""
    result: dict[str, Config] = {}
    roots: list[Path] = []
    for path in sorted(directory.glob("*.yaml")):
        c = load(path)
        root = Path(c.deploy.release_directory).parent.resolve()
        if c.project.name in result:
            raise ConfigError("Duplicate project ID")
        if any(root == r or root in r.parents or r in root.parents for r in roots):
            raise ConfigError("Project deployment directories overlap")
        roots.append(root)
        result[c.project.name] = c
    return result


def load_scheduled_projects(directory: Path) -> tuple[dict[str, Config], dict[str, str]]:
    """Isolate invalid project files without allowing conflicting roots to run."""
    result: dict[str, Config] = {}
    errors: dict[str, str] = {}
    names: dict[str, str] = {}
    roots: dict[str, Path] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            c = load(path)
            name = c.project.name
            root = Path(c.deploy.release_directory).parent.resolve()
            conflicts = [n for n, r in roots.items() if root == r or root in r.parents or r in root.parents]
            if name in names or conflicts:
                affected = set(conflicts) | ({name} if name in names else set())
                for other in affected:
                    result.pop(other, None)
                    errors[names[other]] = "Duplicate identity or overlapping application directory"
                raise ConfigError("Duplicate identity or overlapping application directory")
            names[name], roots[name] = path.name, root
            result[name] = c
        except ConfigError as error:
            errors[path.name] = str(error)
    return result, errors
