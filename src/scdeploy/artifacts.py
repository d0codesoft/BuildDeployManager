"""Immutable artifact storage, signed manifests and constrained TAR extraction."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from scdeploy.config import identifier, relative
from scdeploy.errors import VerificationError
from scdeploy.security import inside, secure_file
from scdeploy.state import now


class IArtifactProvider(Protocol):
    """Contract for managed versioned artifact storage."""

    def list(self, project: str) -> list[Path]: ...


def digest(path: Path) -> str:
    """Compute a streaming SHA-256 digest."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(manifest: dict) -> bytes:
    """Sign the exact canonical JSON representation excluding the signature."""
    return json.dumps({k: v for k, v in manifest.items() if k != "signature"},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def inventory(directory: Path) -> list[dict]:
    """Inventory only regular files; artifact symlinks are deliberately unsupported."""
    result = []
    for item in sorted(directory.rglob("*")):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise VerificationError("Artifact contains a link or special file")
        if item.is_file() and item.relative_to(directory).as_posix() != "manifest.json":
            result.append({"path": item.relative_to(directory).as_posix(),
                           "size": item.stat().st_size, "sha256": digest(item),
                           "executable": bool(item.stat().st_mode & 0o111)})
    return result


def create_manifest(directory: Path, project: str, version: str, commit: str,
                    runtime: str, configuration: str, signing_key: Path | None = None,
                    source_branch: str | None = None) -> dict:
    """Generate an inventory and optionally authenticate it with Ed25519."""
    identifier(project)
    identifier(version)
    manifest = {"schema_version": 1, "project": project, "version": version,
                "commit": commit, "runtime": runtime, "build_configuration": configuration,
                "created_at": now(), "files": inventory(directory)}
    if source_branch:
        manifest["source_branch"] = source_branch
    if not manifest["files"]:
        raise VerificationError("Cannot publish an empty artifact")
    if signing_key:
        secure_file(signing_key)
        key = load_pem_private_key(signing_key.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise VerificationError("Expected an Ed25519 signing key")
        manifest["signature"] = base64.b64encode(key.sign(canonical(manifest))).decode()
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify(directory: Path, project: str, public_key: Path | None,
           require_signature: bool = True) -> dict:
    """Check trusted provenance and every file before stopping application services."""
    try:
        path = inside(directory, "manifest.json")
        if path.stat().st_size > 8_000_000:
            raise ValueError
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["schema_version"] != 1 or manifest["project"] != project:
            raise ValueError
        identifier(manifest["version"])
        if not isinstance(manifest["commit"], str) or not isinstance(manifest["runtime"], str):
            raise ValueError
        if require_signature or manifest.get("signature"):
            if not public_key:
                raise VerificationError("No administrator-configured public key")
            # The configured key is an administrative trust anchor, never a
            # key downloaded alongside the artifact or editable by the build worker.
            if public_key.is_symlink() or not public_key.is_file():
                raise VerificationError("Trust anchor must be a regular file")
            if os.name == "posix" and public_key.stat().st_mode & 0o022:
                raise VerificationError("Trust anchor must not be group/other writable")
            key = load_pem_public_key(public_key.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError
            key.verify(base64.b64decode(manifest["signature"], validate=True), canonical(manifest))
        if manifest["files"] != inventory(directory):
            raise VerificationError("Artifact file inventory mismatch")
        return manifest
    except (OSError, ValueError, KeyError, TypeError, InvalidSignature):
        raise VerificationError("Invalid manifest, signature or file inventory") from None


def extract(archive: Path, destination: Path, max_bytes: int = 4 * 1024**3,
            max_files: int = 100_000) -> None:
    """Reject aliases, duplicate names, devices, links and archive bombs."""
    if destination.exists() and any(destination.iterdir()):
        raise VerificationError("Extraction requires an empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for number, member in enumerate(tar):
                if number >= max_files:
                    raise VerificationError("Archive file count limit exceeded")
                relative(member.name)
                name = Path(member.name).as_posix()
                if name in seen or not (member.isfile() or member.isdir()):
                    raise VerificationError("Duplicate, link or special TAR member")
                seen.add(name)
                total += member.size
                if total > max_bytes or member.size < 0:
                    raise VerificationError("Archive size limit exceeded")
                target = inside(destination, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise VerificationError("Unreadable TAR member")
                    with stream, target.open("xb") as output:
                        shutil.copyfileobj(stream, output, 1024 * 1024)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, OSError):
        raise VerificationError("Invalid or truncated artifact archive") from None


class ArtifactStore:
    """Keep artifacts immutable using atomic no-overwrite publication."""

    def __init__(self, root: Path):
        self.root = root

    def list(self, project: str) -> list[Path]:
        """List artifacts belonging to exactly one project."""
        return sorted((self.root / identifier(project)).glob("*.tar.gz"))

    def publish(self, directory: Path, manifest: dict) -> Path:
        """Archive a completed build, refusing conflicting version reuse."""
        parent = self.root / identifier(manifest["project"])
        parent.mkdir(parents=True, exist_ok=True)
        name = "-".join(identifier(manifest[k]) for k in ("project", "version", "runtime")) + ".tar.gz"
        target = parent / name
        fd, temporary = tempfile.mkstemp(dir=parent, suffix=".tmp")
        os.close(fd)
        try:
            with tarfile.open(temporary, "w:gz") as tar:
                for item in sorted(directory.rglob("*")):
                    if item.is_symlink():
                        raise VerificationError("Cannot publish symlink")
                    tar.add(item, arcname=item.relative_to(directory).as_posix(), recursive=False)
            # Hard-link creation is atomic and refuses to overwrite an existing version.
            if os.name == "nt":
                # Windows rename refuses to overwrite an existing destination.
                os.rename(temporary, target)
            else:
                os.link(temporary, target)
        except FileExistsError:
            raise VerificationError("Artifact version already exists; choose a new version") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target
