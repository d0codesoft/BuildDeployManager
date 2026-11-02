"""TLS, redaction, notification isolation and dry-run invariants."""

import json
import ssl
from dataclasses import asdict
from unittest.mock import MagicMock

import pytest
import yaml

from scdeploy.cli import main
from scdeploy.notifications import Email, Telegram, html_report, notify
from scdeploy.security import inside, redact, register_secret
from scdeploy.errors import ConfigError


def test_email_verifies_tls(monkeypatch):
    server = MagicMock()
    server.__enter__.return_value = server
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: server)
    Email({"host": "mail.example.invalid", "from": "sender@example.invalid", "to": ["admin@example.invalid"]}).send(
        {"project": "sample", "operation": "build", "status": "success"})
    context = server.starttls.call_args.kwargs["context"]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    server.send_message.assert_called_once()


def test_notification_failures_are_isolated(config, tmp_path, monkeypatch):
    config.notifications.email.enabled = True
    config.notifications.email.profile = "smtp"
    config.notifications.telegram.enabled = True
    config.notifications.telegram.profile = "telegram"
    monkeypatch.setattr("scdeploy.notifications.profile", lambda *a: {"token": "test-value"})
    monkeypatch.setattr(Email, "send", lambda *a: (_ for _ in ()).throw(ssl.SSLCertVerificationError()))
    monkeypatch.setattr(Telegram, "send", lambda *a: (_ for _ in ()).throw(OSError()))
    assert len(notify(config, tmp_path, {"project": "sample", "operation": "build", "status": "success"})) == 2


def test_bounded_html_and_redaction():
    register_secret("private-test-value")
    report = html_report({"log": "x" * 100_000 + " private-test-value <script> ERROR"})
    assert len(report) < 40000
    assert "private-test-value" not in report
    assert "<script>" not in report
    assert "#fee2e2" in report
    assert "private-test-value" not in redact("private-test-value")


def test_no_path_escape(tmp_path):
    with pytest.raises(ConfigError):
        inside(tmp_path, "../outside")


@pytest.mark.parametrize("command", ["deploy", "build", "fetch", "test", "rollback", "recover", "check"])
def test_dry_run_creates_no_state_or_logs(config, tmp_path, capsys, command):
    directory = tmp_path / "configs"
    directory.mkdir()
    (directory / "sample.yaml").write_text(yaml.safe_dump(asdict(config)))
    data, logs = tmp_path / "state-must-not-exist", tmp_path / "logs-must-not-exist"
    assert main([command, "sample", "--config-dir", str(directory), "--data-dir", str(data),
                 "--log-dir", str(logs), "--dry-run", "--json"]) == 0
    assert not data.exists() and not logs.exists()
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] and not result["executed"]


def test_cli_version_and_unknown_project(tmp_path, capsys):
    assert main(["--version"]) == 0
    assert main(["validate", "missing", "--config-dir", str(tmp_path)]) == 2


def test_options_before_command(config, tmp_path, capsys):
    (tmp_path / "sample.yaml").write_text(yaml.safe_dump(asdict(config)))
    assert main(["--config-dir", str(tmp_path), "--dry-run", "build", "sample"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]


def test_global_build_limit(tmp_path):
    from scdeploy.locks import build_slot
    from scdeploy.errors import BusyError
    with build_slot(tmp_path, 1):
        with pytest.raises(BusyError):
            with build_slot(tmp_path, 1):
                pytest.fail("Second build should not enter")
