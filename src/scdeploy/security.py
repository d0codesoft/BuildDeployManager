"""Filesystem boundaries, confidential profiles and output redaction."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from scdeploy.config import identifier, relative
from scdeploy.errors import ConfigError, PolicyError

_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Register exact confidential values before external calls can fail."""
    if value:
        _secrets.add(value)


def redact(value: object) -> str:
    """Remove known secrets, common tokens and URL credentials from output."""
    text = str(value)
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)((?:password|token|secret|authorization)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text


def secure_file(path: Path) -> None:
    """Require a regular confidential file with no group/other access on Unix."""
    if path.is_symlink() or not path.is_file():
        raise PolicyError("Secret file must be a regular file")
    info = path.stat()
    if os.name == "posix" and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid not in {0, os.getuid()}):
        raise PolicyError("Secret file requires mode 0600 and trusted ownership")


def profile(directory: Path, name: str) -> dict:
    """Read a protected JSON profile, including systemd credential directories."""
    identifier(name)
    path = directory / f"{name}.json"
    secure_file(path)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        for key, value in result.items():
            if any(x in key.lower() for x in ("token", "password", "secret")):
                register_secret(str(value))
        return result
    except (ValueError, OSError):
        raise ConfigError("Invalid secret profile") from None


def no_symlink_parents(path: Path) -> None:
    """Disallow filesystem aliases at privileged directory boundaries."""
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise PolicyError("Managed directory has a symbolic-link ancestor")


def inside(root: Path, value: str, *, allow_leaf_link: bool = False) -> Path:
    """Resolve a relative path without traversing links or escaping root."""
    relative(value)
    root = root.absolute()
    target = root / value
    checked = target.parent if allow_leaf_link else target
    for part in [checked, *checked.parents]:
        if part == root.parent:
            break
        if part.is_symlink():
            raise PolicyError("Symbolic link inside managed input")
    if not target.resolve().is_relative_to(root.resolve()):
        raise PolicyError("Path escapes managed root")
    return target


def trusted_configs(directory: Path) -> None:
    """Privileged command configurations must be writable only by administrators."""
    if os.name != "posix":
        return
    for path in [directory, *directory.glob("*.yaml")]:
        no_symlink_parents(path)
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise PolicyError("Project configuration must be root-owned and not group-writable")
