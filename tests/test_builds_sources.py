"""Provider plans, failure propagation and local Git integration."""

import os
import subprocess
import sys

import pytest

from scdeploy.builders import Builder
from scdeploy.config import Build, BuildProject, Source
from scdeploy.errors import DeployError, VerificationError
from scdeploy.execution import Runner
from scdeploy.sources import GitSource, HttpClient, RemoteArtifactSource


class FakeRunner:
    def __init__(self, fail=None):
        self.commands = []
        self.fail = fail

    def run(self, args, **kwargs):
        self.commands.append(args)
        if len(args) > 1 and args[1] == self.fail:
            raise DeployError("Simulated tool failure")
        return subprocess.CompletedProcess(args, 0, "22.0.0\n", "")


@pytest.mark.parametrize("failure", ["restore", "build", "test", "publish"])
def test_dotnet_failure_propagates(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("scdeploy.builders.require_nonroot", lambda: None)
    c = Build(projects=[BuildProject("server.csproj", "server")])
    runner = FakeRunner(failure)
    with pytest.raises(DeployError):
        Builder(c, runner).build(tmp_path, tmp_path / "published")
    assert runner.commands[-1][1] == failure


def test_dotnet_separate_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr("scdeploy.builders.require_nonroot", lambda: None)
    c = Build(tests=False, projects=[BuildProject("one.csproj", "one"), BuildProject("two.csproj", "two")])
    runner = FakeRunner()
    Builder(c, runner).build(tmp_path, tmp_path / "out")
    published = [command[-1] for command in runner.commands if command[1] == "publish"]
    assert len(set(published)) == 2
    assert all("--no-restore" in command for command in runner.commands if command[1] == "publish")


def test_node_lockfile_and_version(tmp_path, monkeypatch):
    monkeypatch.setattr("scdeploy.builders.require_nonroot", lambda: None)
    (tmp_path / "package-lock.json").write_text("{}")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "server.js").write_text("console.log('ok')")
    runner = FakeRunner()
    Builder(Build(provider="node", node_major=22), runner).build(tmp_path, tmp_path / "out")
    assert ["npm", "ci"] in runner.commands
    assert (tmp_path / "out/server.js").exists()


def test_custom_command_real_execution(tmp_path, monkeypatch):
    monkeypatch.setattr("scdeploy.builders.require_nonroot", lambda: None)
    script = "from pathlib import Path; Path('dist').mkdir(); Path('dist/app').write_text('ready')"
    c = Build(provider="command", tests=False, commands=[[sys.executable, "-c", script]])
    Builder(c, Runner()).build(tmp_path, tmp_path / "out")
    assert (tmp_path / "out/app").read_text() == "ready"


def test_python_provider_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("scdeploy.builders.require_nonroot", lambda: None)
    (tmp_path / "requirements.txt").write_text("")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/application.whl").write_text("fixture")
    runner = FakeRunner()
    c = Build(provider="python", requirements="requirements.txt", distribution=True)
    Builder(c, runner).build(tmp_path, tmp_path / "out")
    assert any("venv" in command for command in runner.commands)
    assert any("pytest" in command for command in runner.commands)
    assert any("--outdir" in command for command in runner.commands)


def test_timeout_kills_process(tmp_path):
    with pytest.raises(DeployError, match="timed out"):
        Runner(timeout=1).run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)


def test_nonzero_is_error():
    with pytest.raises(DeployError, match="exit 7"):
        Runner().run([sys.executable, "-c", "raise SystemExit(7)"])


def test_git_new_existing_no_changes_and_update(tmp_path):
    runner = Runner()
    remote = tmp_path / "remote"
    remote.mkdir()
    runner.run(["git", "init", "-b", "main"], cwd=remote)
    runner.run(["git", "config", "user.email", "test@example.invalid"], cwd=remote)
    runner.run(["git", "config", "user.name", "Isolated Test"], cwd=remote)
    (remote / "app").write_text("first")
    runner.run(["git", "add", "app"], cwd=remote)
    runner.run(["git", "commit", "-m", "first"], cwd=remote)
    source = GitSource(Source("git", repository=remote.as_uri()), runner)
    probe = runner.run(["git", "ls-remote", remote.as_uri(), "refs/heads/main"], check=False)
    if os.name == "nt" and "NtCreateDirectoryObject" in probe.stdout:
        pytest.skip("Windows sandbox blocks Git's MSYS subprocess; real Git integration runs in Ubuntu CI")
    first = source.check()
    assert source.fetch(tmp_path / "checkout") == first["commit"]
    assert source.check() == first
    with pytest.raises(DeployError, match="must not exist"):
        source.fetch(tmp_path / "checkout")
    (remote / "app").write_text("second")
    runner.run(["git", "commit", "-am", "second"], cwd=remote)
    assert source.check()["commit"] != first["commit"]
    assert source.fetch(tmp_path / "other", first["commit"]) == first["commit"]


@pytest.mark.parametrize("failure", ["authentication", "unavailable"])
def test_git_remote_failure(tmp_path, failure):
    class FailedRunner:
        def run(self, *args, **kwargs):
            raise DeployError(failure)
    with pytest.raises(DeployError):
        GitSource(Source("git", repository="https://example.invalid/repo"), FailedRunner()).check()


@pytest.mark.parametrize("url", ["http://trusted.invalid/app", "https://evil.invalid/app",
                                 "https://user:password@trusted.invalid/app", "https://trusted.invalid:444/app"])
def test_untrusted_http_before_network(url):
    with pytest.raises(VerificationError):
        HttpClient(["trusted.invalid"]).get(url)


def test_actions_validates_run_commit_and_origin(monkeypatch):
    c = Source("github-actions", repository="org/repo", branch="main", workflow="ci.yml")
    source = RemoteArtifactSource(c)
    sha = "a" * 40
    run = {"id": 7, "workflow_id": 2, "status": "completed", "conclusion": "success",
           "head_branch": "main", "head_sha": sha, "head_repository": {"full_name": "org/repo"}, "event": "push"}
    def response(url):
        if "/artifacts?" in url:
            return {"artifacts": [{"name": "application", "expired": False,
                                    "workflow_run": {"head_sha": sha}, "archive_download_url": "https://api.github.com/artifact"}]}
        if "/runs?" in url:
            return {"workflow_runs": [run]}
        return {"id": 2}
    monkeypatch.setattr(source.http, "json", response)
    assert source.check(sha)["commit"] == sha
    run["conclusion"] = "failure"
    with pytest.raises(VerificationError):
        source.check(sha)
    run["conclusion"] = "success"
    run["head_repository"] = {"full_name": "attacker/fork"}
    with pytest.raises(VerificationError):
        source.check(sha)


def test_https_redirect_strips_authorization(monkeypatch):
    import io
    import urllib.error
    from email.message import Message
    client = HttpClient(["first.invalid", "second.invalid"], "fixture-token")
    requests = []
    def open_request(request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            headers = Message()
            headers["Location"] = "https://second.invalid/artifact"
            raise urllib.error.HTTPError(request.full_url, 302, "redirect", headers, None)
        return io.BytesIO(b"payload")
    monkeypatch.setattr(client.opener, "open", open_request)
    assert client.get("https://first.invalid/artifact") == b"payload"
    assert requests[0].get_header("Authorization") == "Bearer fixture-token"
    assert requests[1].get_header("Authorization") is None
