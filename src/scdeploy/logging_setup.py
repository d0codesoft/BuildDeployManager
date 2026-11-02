"""Structured, rotating logs with redaction at the final formatting boundary."""

import json
import logging
import logging.handlers
import sys
from pathlib import Path

from scdeploy.security import redact


class SafeFormatter(logging.Formatter):
    """Redact formatted messages, interpolated arguments and exception text."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line with stable operation identifiers."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"level": record.levelname, "message": redact(record.getMessage()),
                           **{k: redact(v) for k, v in getattr(record, "context", {}).items()}})


def setup(directory: Path | None, verbose: bool = False, json_output: bool = False) -> None:
    """Console uses stderr so CLI JSON on stdout stays machine-readable."""
    logger = logging.getLogger("scdeploy")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for old in logger.handlers[:]:
        logger.removeHandler(old)
        old.close()
    formatter = JsonFormatter() if json_output else SafeFormatter(
        "%(asctime)s %(levelname)s %(context)s %(message)s", defaults={"context": {}})
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)
    if directory:
        directory.mkdir(parents=True, exist_ok=True)
        file = logging.handlers.RotatingFileHandler(directory / "scdeploy.log", maxBytes=5_000_000,
                                                    backupCount=5, encoding="utf-8")
        file.setFormatter(formatter)
        logger.addHandler(file)
        if Path("/dev/log").exists():
            syslog = logging.handlers.SysLogHandler(address="/dev/log")
            syslog.setFormatter(formatter)
            logger.addHandler(syslog)
