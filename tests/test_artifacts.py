"""Adversarial archive and provenance tests."""

import io
import tarfile

import pytest

from scdeploy.artifacts import extract, verify
from scdeploy.errors import ConfigError, VerificationError


def test_signed_roundtrip(artifact, keys, tmp_path):
    archive = artifact(signing_key=keys[0])
    output = tmp_path / "extracted"
    extract(archive, output)
    assert verify(output, "sample", keys[1])["version"] == "1.0.0"
    (output / "app.txt").write_text("tampered")
    with pytest.raises(VerificationError):
        verify(output, "sample", keys[1])


def test_wrong_key(artifact, keys, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    archive = artifact(signing_key=keys[0])
    output = tmp_path / "extracted"
    extract(archive, output)
    key = Ed25519PrivateKey.generate()
    public = tmp_path / "other.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(VerificationError):
        verify(output, "sample", public)


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE),
                                      ("/absolute", tarfile.REGTYPE),
                                      ("C:/escape", tarfile.REGTYPE),
                                      ("alias", tarfile.SYMTYPE),
                                      ("hard", tarfile.LNKTYPE),
                                      ("device", tarfile.CHRTYPE)])
def test_unsafe_tar(tmp_path, name, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "../../outside"
        tar.addfile(member)
    with pytest.raises((VerificationError, ConfigError)):
        extract(archive, tmp_path / "extract")


def test_duplicate_tar(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for _ in range(2):
            member = tarfile.TarInfo("same")
            tar.addfile(member)
    with pytest.raises(VerificationError):
        extract(archive, tmp_path / "out")


def test_size_limit(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("large")
        member.size = 10
        tar.addfile(member, io.BytesIO(b"0123456789"))
    with pytest.raises(VerificationError):
        extract(archive, tmp_path / "out", max_bytes=5)


def test_unsigned_rejected(artifact, tmp_path):
    output = tmp_path / "out"
    extract(artifact(), output)
    with pytest.raises(VerificationError):
        verify(output, "sample", None)


def test_project_mismatch(artifact, tmp_path):
    output = tmp_path / "out"
    extract(artifact(), output)
    with pytest.raises(VerificationError):
        verify(output, "different", None, False)
