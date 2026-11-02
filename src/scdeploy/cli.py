"""Small CLI adapter over independently testable application services."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scdeploy import __version__
from scdeploy.config import load_projects, load_scheduled_projects
from scdeploy.errors import ConfigError, DeployError
from scdeploy.execution import require_nonroot
from scdeploy.logging_setup import setup
from scdeploy.migration import inspect_scloud
from scdeploy.orchestrator import Orchestrator
from scdeploy.security import redact, trusted_configs
from scdeploy.state import now

COMMANDS = ["show", "validate", "check", "fetch", "build", "test", "artifacts", "deploy",
            "status", "health", "releases", "rollback", "history", "logs", "recover", "import-legacy"]


def parser() -> argparse.ArgumentParser:
    """Support common options before or after the subcommand."""
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--config-dir", type=Path)
    common.add_argument("--data-dir", type=Path)
    common.add_argument("--log-dir", type=Path)
    common.add_argument("--secrets-dir", type=Path)
    common.add_argument("--max-builds", type=int)
    for option in ["dry-run", "verbose", "json", "approve", "no-notify", "force"]:
        common.add_argument("--" + option, action="store_true")
    common.add_argument("--version")
    common.add_argument("--commit")
    common.add_argument("--artifact", type=Path)
    common.add_argument("--signing-key", type=Path)
    root = argparse.ArgumentParser(prog="scdeploy", parents=[common])
    root.add_argument("--tool-version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    for command in ["list", "doctor", "check-all"]:
        sub.add_parser(command, parents=[common])
    for command in COMMANDS:
        sub.add_parser(command, parents=[common]).add_argument("project")
    return root


def output(value: object, json_output: bool) -> None:
    """Keep output structured and redact at the user-facing boundary."""
    text = json.dumps(value, indent=2, default=str, ensure_ascii=True)
    print(redact(text))


def execute(args: argparse.Namespace) -> object:
    """Dispatch commands with a strict no-side-effect dry-run fast path."""
    if args.max_builds < 1:
        raise ConfigError("max-builds must be positive")
    config_errors = {}
    if args.command == "check-all":
        configs, config_errors = load_scheduled_projects(args.config_dir)
    else:
        configs = load_projects(args.config_dir)
    if args.command == "doctor":
        return {"python": sys.version.split()[0], "platform": sys.platform,
                "tools": {name: shutil.which(name) for name in ["git", "dotnet", "node", "npm", "systemctl", "sudo"]},
                "projects": list(configs), "production_actions": False}
    if args.command == "list":
        return [asdict(c.project) for c in configs.values()]
    if args.command == "check-all":
        if args.dry_run:
            return {"dry_run": True, "projects": list(configs), "action": "scheduled-policy-check"}
        trusted_configs(args.config_dir)
        results = {name: {"status": "invalid-config", "error": error} for name, error in config_errors.items()}
        failed = bool(config_errors)
        for name, config in configs.items():
            if not config.project.enabled:
                continue
            try:
                engine = Orchestrator(config, args.data_dir, args.log_dir, args.secrets_dir,
                                      args.max_builds, args.no_notify)
                previous = engine.state.project(name).get("checked_at")
                interval = config.updates.check_interval
                seconds = int(interval[:-1]) * {"s": 1, "m": 60, "h": 3600}[interval[-1]]
                if previous and (datetime.fromisoformat(now()) - datetime.fromisoformat(previous)).total_seconds() < seconds:
                    results[name] = {"status": "not-due"}
                    continue
                results[name] = engine.scheduled()
            except Exception as error:
                failed = True
                results[name] = {"status": "failed", "error": redact(error) if isinstance(error, DeployError) else type(error).__name__}
        if failed:
            output(results, args.json)
            raise DeployError("One or more scheduled projects failed")
        return results
    if args.project not in configs:
        raise ConfigError("Unknown project ID")
    config = configs[args.project]
    if args.command == "validate":
        return {"project": args.project, "valid": True}
    if args.command == "show":
        return asdict(config)
    if args.dry_run:
        return {"dry_run": True, "project": args.project, "operation": args.command,
                "artifact": str(args.artifact) if args.artifact else None,
                "release_directory": config.deploy.release_directory,
                "current_link": config.deploy.current_link,
                "services": config.deploy.services,
                "observe_only": config.deploy.observe_only,
                "pipeline": (["check-remote-commit", "fresh-clone", "build", "test", "publish", "switch-release", "healthcheck"]
                             if config.source.type in {"git", "github"} and not args.artifact else ["verify-artifact", "switch-release", "healthcheck"]),
                "force": args.force,
                "approval_required": True, "executed": False}
    trusted_configs(args.config_dir)
    if args.command in {"deploy", "rollback", "recover", "build", "test", "fetch"}:
        require_nonroot()
    setup(args.log_dir, args.verbose, args.json)
    engine = Orchestrator(config, args.data_dir, args.log_dir, args.secrets_dir, args.max_builds, args.no_notify)
    if args.command == "check":
        return engine.check()
    if args.command == "fetch":
        return engine.fetch(args.commit)
    if args.command in {"build", "test"}:
        return engine.build(args.version, args.commit, args.signing_key, args.command == "test")
    if args.command == "artifacts":
        return [str(p) for p in engine.store.list(args.project)]
    if args.command == "deploy":
        return engine.deploy(args.artifact, args.approve, force=args.force, version=args.version,
                             commit=args.commit, signing_key=args.signing_key)
    if args.command == "rollback":
        started = now()
        try:
            result = engine.deployment.rollback(args.approve, args.version)
            engine.report("rollback", "success", started, result=result)
            return result
        except BaseException:
            engine.report("rollback", "failed", started)
            raise
    if args.command == "recover":
        return engine.deployment.recover(args.approve)
    if args.command == "status":
        return {"project": engine.state.project(args.project),
                "services": {s: engine.services.active(s) for s in config.deploy.services}}
    if args.command == "health":
        engine.health.check(config)
        return {"healthy": True}
    if args.command == "releases":
        root = Path(config.deploy.release_directory)
        return [p.name for p in sorted(root.glob("*")) if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")]
    if args.command == "history":
        return engine.state.history(args.project)
    if args.command == "logs":
        return engine.services.diagnostics()
    if args.command == "import-legacy":
        report = inspect_scloud(engine.runner)
        operation = engine.state.begin(args.project, "legacy-observation", report=report)
        engine.state.finish(operation, "success")
        return report
    raise ConfigError("Unsupported command")


def main(argv: list[str] | None = None) -> int:
    """Expose predictable exit codes and safe errors for interactive and unattended use."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--version"]:
        print(__version__)
        return 0
    args = parser().parse_args(arguments)
    defaults = dict(config_dir=Path("/etc/scdeploy/projects"), data_dir=Path("/var/lib/scdeploy"),
                    log_dir=Path("/var/log/scdeploy"), secrets_dir=Path(os.environ.get("CREDENTIALS_DIRECTORY", "/etc/scdeploy/secrets")),
                    max_builds=2, dry_run=False, verbose=False, json=False, approve=False,
                    no_notify=False, force=False, version=None, commit=None, artifact=None, signing_key=None)
    for key, value in defaults.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    try:
        output(execute(args), args.json)
        return 0
    except DeployError as error:
        print(json.dumps({"error": redact(error), "code": error.code}), file=sys.stderr)
        return error.code
    except KeyboardInterrupt:
        print('{"error":"Operation cancelled","code":130}', file=sys.stderr)
        return 130
    except Exception as error:
        print(json.dumps({"error": "Unexpected failure; see operation history", "type": type(error).__name__, "code": 1}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
