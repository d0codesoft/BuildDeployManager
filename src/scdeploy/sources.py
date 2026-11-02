"""Git and authenticated HTTPS artifact source providers."""

from __future__ import annotations

import json
import re
import shutil
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from scdeploy.config import Source
from scdeploy.errors import DeployError, VerificationError
from scdeploy.execution import Runner
from scdeploy.security import inside, register_secret


class ISourceProvider(Protocol):
    """Source retrieval contract independent of orchestration."""

    def check(self) -> dict: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward credentials automatically across origin boundaries."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpClient:
    """Bounded TLS-verified transport with explicit trusted redirect hosts."""

    def __init__(self, hosts: list[str], token: str = "", timeout: int = 30):
        self.hosts = set(hosts)
        self.token = token
        self.timeout = timeout
        register_secret(token)
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def get(self, url: str, output: Path | None = None, accept: str = "application/json") -> bytes:
        """Limit metadata to 8 MiB and binary artifacts to 4 GiB."""
        origin = urllib.parse.urlsplit(url).hostname
        for _ in range(6):
            parts = urllib.parse.urlsplit(url)
            if (parts.scheme != "https" or parts.hostname not in self.hosts or
                    parts.username or parts.password or parts.port not in {None, 443}):
                raise VerificationError("Untrusted HTTPS artifact origin")
            headers = {"Accept": accept, "User-Agent": "scdeploy/0.1", "X-GitHub-Api-Version": "2022-11-28"}
            if self.token and parts.hostname == origin:
                headers["Authorization"] = "Bearer " + self.token
            try:
                response = self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    url = urllib.parse.urljoin(url, exc.headers.get("Location", ""))
                    exc.close()
                    continue
                exc.close()
                raise DeployError(f"HTTPS source returned status {exc.code}") from None
            except (OSError, urllib.error.URLError):
                raise DeployError("HTTPS request failed (network or TLS validation)") from None
            limit = 4 * 1024**3 if output else 8 * 1024**2
            total = 0
            chunks = []
            try:
                with response:
                    stream = output.open("xb") if output else None
                    try:
                        while chunk := response.read(1024 * 1024):
                            total += len(chunk)
                            if total > limit:
                                raise VerificationError("Download exceeds size limit")
                            if stream:
                                stream.write(chunk)
                            else:
                                chunks.append(chunk)
                    finally:
                        if stream:
                            stream.close()
            except (OSError, ValueError):
                raise DeployError("Incomplete HTTPS response") from None
            return b"".join(chunks)
        raise VerificationError("Too many HTTPS redirects")

    def json(self, url: str) -> dict:
        """Read structured metadata without leaking a credential-bearing URL."""
        try:
            value = json.loads(self.get(url))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise DeployError("Invalid HTTPS metadata") from None


class GitSource:
    """Use fresh owned checkouts and credentials outside command-line arguments."""

    def __init__(self, config: Source, runner: Runner, credentials: dict | None = None):
        self.config, self.runner = config, runner
        self.credentials = credentials or {}
        self.repository = (f"https://github.com/{config.repository}.git"
                           if config.type.startswith("github") else config.repository)

    @contextmanager
    def auth(self) -> Iterator[dict[str, str]]:
        """Supply PAT/App installation tokens through an askpass pipe environment."""
        token = self.credentials.get("token", "")
        if not token:
            yield {}
            return
        register_secret(token)
        with tempfile.TemporaryDirectory(prefix="scdeploy-askpass-") as directory:
            script = Path(directory) / "askpass.py"
            script.write_text(
                "#!/usr/bin/env python3\nimport os,sys\n"
                "print('x-access-token' if 'username' in sys.argv[1].lower() "
                "else os.environ['SCDEPLOY_GIT_TOKEN'])\n", encoding="utf-8")
            script.chmod(0o700)
            yield {"GIT_ASKPASS": str(script), "SCDEPLOY_GIT_TOKEN": token}

    def check(self) -> dict:
        """Inspect the remote without modifying a local Git worktree."""
        ref = f"refs/tags/{self.config.tag}" if self.config.tag else f"refs/heads/{self.config.branch}"
        with self.auth() as env:
            result = self.runner.run(["git", "ls-remote", self.repository, ref, ref + "^{}"], env=env)
        lines = result.stdout.strip().splitlines()
        if not lines:
            raise DeployError("Remote Git ref does not exist")
        commit = lines[-1].split()[0]
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise DeployError("Invalid remote Git commit")
        return {"commit": commit, "version": self.config.tag or commit[:12]}

    def fetch(self, destination: Path, commit: str | None = None) -> str:
        """Clone only into a fresh managed workspace; never reset user checkouts."""
        expected = commit or self.check()["commit"]
        if not re.fullmatch(r"[0-9a-f]{40,64}", expected):
            raise DeployError("A full Git commit SHA is required")
        if destination.exists():
            raise DeployError("Fetch destination must not exist")
        with self.auth() as env:
            self.runner.run(["git", "clone", "--no-checkout", "--", self.repository, str(destination)], env=env)
            self.runner.run(["git", "fetch", "origin", expected], cwd=destination, env=env)
            self.runner.run(["git", "checkout", "--detach", expected], cwd=destination, env=env)
            actual = self.runner.run(["git", "rev-parse", "HEAD"], cwd=destination, env=env).stdout.strip()
        if actual != expected:
            raise VerificationError("Git checkout commit mismatch")
        return actual


class RemoteArtifactSource:
    """Resolve exact successful workflow runs, GitHub releases or HTTPS API metadata."""

    def __init__(self, config: Source, credentials: dict | None = None):
        self.config = config
        hosts = config.trusted_hosts or (["api.github.com", "github.com", "release-assets.githubusercontent.com",
                                          "productionresultssa0.blob.core.windows.net"]
                                         if config.type.startswith("github") else [])
        self.http = HttpClient(hosts, (credentials or {}).get("token", ""))

    def check(self, commit: str | None = None) -> dict:
        """Never select an unvalidated latest workflow run."""
        c = self.config
        base = f"https://api.github.com/repos/{c.repository}"
        if c.type == "http":
            if not c.metadata_url:
                raise DeployError("HTTP sources require metadata_url")
            data = self.http.json(c.metadata_url)
            required = {"version", "commit", "artifact_url"}
            if not required <= data.keys():
                raise VerificationError("Incomplete update API metadata")
            return {k: data[k] for k in required}
        if c.type == "github-release":
            if not c.tag:
                raise DeployError("GitHub Releases require an explicit tag")
            data = self.http.json(base + "/releases/tags/" + urllib.parse.quote(c.tag, safe=""))
            if data.get("draft") or data.get("prerelease") or data.get("tag_name") != c.tag:
                raise VerificationError("Release is not a published stable tag")
            assets = [a for a in data.get("assets", []) if a["name"] == c.artifact_name]
            if len(assets) != 1:
                raise VerificationError("Release artifact is missing or ambiguous")
            sha = commit or GitSource(c, Runner()).check()["commit"]
            return {"version": c.tag, "commit": sha, "artifact_url": assets[0]["url"]}
        if c.type != "github-actions" or not c.workflow:
            raise DeployError("GitHub Actions require a configured workflow")
        # Determine the branch HEAD independently, then require an exact matching run.
        if not commit:
            data = self.http.json(base + "/commits/" + urllib.parse.quote(c.branch, safe=""))
            commit = data["sha"]
        workflow = self.http.json(base + "/actions/workflows/" + urllib.parse.quote(c.workflow, safe=""))
        query = urllib.parse.urlencode({"branch": c.branch, "head_sha": commit, "status": "success", "per_page": 100})
        runs = self.http.json(base + f"/actions/workflows/{workflow['id']}/runs?{query}")
        matches = [r for r in runs.get("workflow_runs", []) if
                   r.get("workflow_id") == workflow["id"] and r.get("status") == "completed" and
                   r.get("conclusion") == "success" and r.get("head_branch") == c.branch and
                   r.get("head_sha") == commit and
                   r.get("head_repository", {}).get("full_name") == c.repository and
                   r.get("event") in {"push", "workflow_dispatch", "release"}]
        if not matches:
            raise VerificationError("No successful trusted workflow run for selected commit")
        run = max(matches, key=lambda r: r["id"])
        data = self.http.json(base + f"/actions/runs/{run['id']}/artifacts?per_page=100")
        assets = [a for a in data.get("artifacts", []) if
                  a.get("name") == c.artifact_name and not a.get("expired") and
                  a.get("workflow_run", {}).get("head_sha") == commit]
        if len(assets) != 1:
            raise VerificationError("No unique artifact matching workflow commit")
        return {"version": commit[:12], "commit": commit,
                "artifact_url": assets[0]["archive_download_url"], "zip": True}

    def download(self, metadata: dict, destination: Path) -> None:
        """Fetch signed application TAR; Actions ZIP must contain exactly one TAR."""
        if not metadata.get("zip"):
            self.http.get(metadata["artifact_url"], destination, "application/octet-stream")
            return
        wrapper = destination.with_suffix(".zip")
        try:
            self.http.get(metadata["artifact_url"], wrapper, "application/octet-stream")
            with zipfile.ZipFile(wrapper) as archive:
                entries = archive.infolist()
                if len(entries) != 1 or not entries[0].filename.endswith(".tar.gz") or entries[0].file_size > 4 * 1024**3:
                    raise VerificationError("Actions ZIP must contain one signed TAR.GZ")
                inside(destination.parent, entries[0].filename)
                with archive.open(entries[0]) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
        except zipfile.BadZipFile:
            raise VerificationError("Invalid Actions ZIP") from None
        finally:
            wrapper.unlink(missing_ok=True)
