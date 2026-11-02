"""Stable domain errors and CLI exit codes."""


class DeployError(Exception):
    """An operation failed without disclosing confidential command output."""

    code = 1


class ConfigError(DeployError):
    """Configuration or a safety invariant is invalid."""

    code = 2


class PolicyError(DeployError):
    """Administrative authorization is missing."""

    code = 3


class BusyError(DeployError):
    """Another process owns a required lock."""

    code = 4


class VerificationError(DeployError):
    """An artifact cannot be trusted."""

    code = 5


class RollbackError(DeployError):
    """Service recovery failed and requires operator intervention."""

    code = 6
