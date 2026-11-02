"""Bounded email/Telegram reports; notification failures cannot undo deployments."""

from __future__ import annotations

import html
import json
import logging
import smtplib
import socket
import ssl
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from scdeploy.config import Config
from scdeploy.security import profile, redact, register_secret
from scdeploy.state import now

log = logging.getLogger(__name__)


class INotificationProvider(Protocol):
    """Send a structured operation report."""

    def send(self, report: dict) -> None: ...


def html_report(report: dict) -> str:
    """Escape HTML and color log severity, retaining at most 32 KiB."""
    text = redact(json.dumps(report, indent=2, ensure_ascii=False))[-32768:]
    lines = []
    for line in text.splitlines():
        level = line.upper()
        color = "#fee2e2" if "ERROR" in level or "FAILED" in level else "#fef3c7" if "WARN" in level else "#e0e7ff"
        lines.append(f'<div style="background:{color};white-space:pre-wrap">{html.escape(line)}</div>')
    return "<!doctype html><html><body>" + "\n".join(lines) + "</body></html>"


class Email:
    """Send HTML reports using verified STARTTLS or implicit TLS."""

    def __init__(self, settings: dict):
        self.settings = settings

    def send(self, report: dict) -> None:
        """Never offer a plaintext authentication or unverified TLS mode."""
        c = self.settings
        message = EmailMessage()
        message["From"] = c["from"]
        message["To"] = ", ".join(c["to"])
        message["Subject"] = f"SCDEPLOY {report['project']} {report['operation']} {report['status']}"
        message.set_content(redact(json.dumps(report, indent=2))[-32768:])
        message.add_alternative(html_report(report), subtype="html")
        context = ssl.create_default_context()
        if c.get("tls", "starttls") == "implicit":
            server = smtplib.SMTP_SSL(c["host"], c.get("port", 465), timeout=20, context=context)
        elif c.get("tls", "starttls") == "starttls":
            server = smtplib.SMTP(c["host"], c.get("port", 587), timeout=20)
        else:
            raise ValueError("Unsupported SMTP TLS mode")
        with server:
            if c.get("tls", "starttls") == "starttls":
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            if c.get("username"):
                server.login(c["username"], c["password"])
            server.send_message(message)


class Telegram:
    """Send small Bot API messages with a verified TLS connection."""

    def __init__(self, settings: dict):
        self.settings = settings
        register_secret(settings["token"])

    def send(self, report: dict) -> None:
        """Limit Telegram payload size and avoid logging the token-bearing URL."""
        c = self.settings
        data = json.dumps({"chat_id": c["chat_id"], "text": redact(json.dumps(report, ensure_ascii=False))[:3500]}).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{c['token']}/sendMessage",
                                         data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            result = json.loads(response.read(65536))
            if not result.get("ok"):
                raise ValueError("Telegram rejected notification")


def notify(config: Config, secrets: Path, report: dict) -> list[str]:
    """Isolate failures per channel and return safe warning messages."""
    warnings = []
    report = {"server": socket.gethostname(), "ended": now(), **report}
    success = report.get("status") in {"success", "unchanged", "available"}
    for name, provider in [("email", Email), ("telegram", Telegram)]:
        setting = getattr(config.notifications, name)
        if not setting.enabled or (success and not setting.on_success) or (not success and not setting.on_failure):
            continue
        try:
            provider(profile(secrets, setting.profile)).send(report)
        except Exception:
            warning = f"{name} notification failed"
            log.warning(warning)
            warnings.append(warning)
    return warnings
