"""Bounded subprocess execution without a command shell."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from collections import deque
from pathlib import Path

from scdeploy.errors import DeployError, PolicyError
from scdeploy.security import redact

log = logging.getLogger(__name__)


class Runner:
    """Execute checked commands with isolated environment and process-group cancellation."""

    def __init__(self, timeout: int = 1800, log_path: Path | None = None):
        self.timeout = timeout
        self.log_path = log_path
        self.prefix: list[str] = []
        self.context: dict[str, str] = {}

    def run(self, args: list[str], cwd: Path | None = None,
            env: dict[str, str] | None = None, check: bool = True,
            timeout: float | None = None) -> subprocess.CompletedProcess:
        """Keep raw stdout off logs; bound returned diagnostics to 64 KiB."""
        if not args or any(not isinstance(a, str) or "\x00" in a for a in args):
            raise DeployError("Invalid command argument vector")
        environment = {k: v for k, v in os.environ.items() if k in {
            "PATH", "HOME", "USER", "SYSTEMROOT", "TEMP", "TMP", "LANG", "SSH_AUTH_SOCK",
            "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
        }}
        environment.update({"GIT_TERMINAL_PROMPT": "0", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"})
        environment.update(env or {})
        command = self.prefix + args
        log.info("Execute %s", redact(args[0]), extra={"context": self.context})
        try:
            chunks: deque[bytes] = deque(maxlen=16)
            proc = subprocess.Popen(command, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=os.name == "posix")

            def collect() -> None:
                assert proc.stdout is not None
                with proc.stdout:
                    while block := proc.stdout.read(4096):
                        chunks.append(block)

            reader = threading.Thread(target=collect, daemon=True)
            reader.start()

            def terminate() -> None:
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    elif proc.poll() is None:
                        proc.kill()
                except ProcessLookupError:
                    pass

            try:
                proc.wait(timeout=timeout or self.timeout)
            except BaseException:
                terminate()
                proc.wait()
                reader.join(timeout=5)
                raise
            # Do not leave ordinary background descendants modifying the workspace.
            terminate()
            reader.join(timeout=5)
            if reader.is_alive():
                raise DeployError("Command left an unmanaged background process")
            text = redact(b"".join(chunks).decode("utf-8", errors="replace"))
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as stream:
                    stream.write(text + "\n")
            if check and proc.returncode:
                raise DeployError(f"Command failed: {Path(args[0]).name}, exit {proc.returncode}; see operation log")
            return subprocess.CompletedProcess(args, proc.returncode, text, "")
        except subprocess.TimeoutExpired:
            raise DeployError("Command timed out; process group terminated") from None
        except OSError:
            raise DeployError(f"Cannot execute {Path(args[0]).name}") from None


def require_nonroot() -> None:
    """Never execute repository-controlled build commands as root."""
    if os.name == "posix" and os.geteuid() == 0:
        raise PolicyError("Builds must run as the unprivileged scdeploy user")
