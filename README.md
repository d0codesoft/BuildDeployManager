# SCODE Build & Deploy Manager

GitHub releases are built by `.github/workflows/release.yml`. Set the version in
`pyproject.toml` and `src/scdeploy/__init__.py`, commit the changes, then push the
matching tag (for example, `v0.1.0`). You can also run the workflow manually on `main`; it creates the matching
version tag at the selected commit. Existing tags must point to that same commit. It runs tests, builds a wheel and source distribution, and
publishes a source ZIP plus SHA-256 checksums with the release. After successful
publication it deletes older published stable releases and their assets, keeping
Git tags, drafts and prereleases. Re-running a tag that already has a release
fails before cleanup. The workflow uses the built-in `GITHUB_TOKEN` with
`contents: write`; no additional secret is required.

`scdeploy` updates independent applications on Ubuntu 24.04 using the primary pipeline **Git/GitHub branch HEAD → clean clone → local build/test/publish → new release → atomic switch → healthcheck**. It compares the upstream commit with the installed commit first and skips unchanged versions. Python 3.12+, PyYAML, cryptography and packaging are the runtime dependencies.

```bash
sudo apt-get install python3 python3-venv python3-pip sudo util-linux
sudo bash scripts/install.sh
sudo -u scdeploy scdeploy doctor --json
```

The installer changes only manager files and its dedicated identities. Build tools are opt-in (`scripts/install-tools.sh`); application units, Nginx, databases, mail, SSH and firewall rules are not rewritten. The timer is **not enabled automatically**.

Configure a project by copying a YAML example to `/etc/scdeploy/projects/NAME.yaml`. Examples start in observation mode. New projects require no Python changes. Secrets are protected profiles, never YAML values. Build commands execute as `scdeploy-build`; deployment control executes as `scdeploy`, with exact sudo authorization for selected systemd services.

```bash
sudo -u scdeploy scdeploy list
sudo -u scdeploy scdeploy validate scloud
sudo -u scdeploy scdeploy import-legacy scloud --json
sudo -u scdeploy scdeploy deploy scloud --dry-run
sudo -u scdeploy scdeploy deploy scloud --approve
sudo -u scdeploy scdeploy deploy scloud --approve --force
# Optional separate build and externally prepared signed archive installation:
sudo -u scdeploy scdeploy build scloud --version 1.4.2
sudo -u scdeploy scdeploy deploy scloud --artifact /path/to/signed.tar.gz --dry-run
sudo -u scdeploy scdeploy deploy scloud --artifact /path/to/signed.tar.gz --approve
sudo -u scdeploy scdeploy rollback scloud --approve
sudo -u scdeploy scdeploy recover scloud --approve
```

Production deploys require migration review, `observe_only: false`, matching service paths and explicit approval. Local builds are authenticated by manager-owned successful-build records, full manifests and archive SHA-256; externally prepared artifacts require signatures by default. Read [SCLOUD migration](docs/migration-scloud.md) before changing an existing installation. Version `0.1.0` awaits the live Ubuntu acceptance run; local Windows tests do not certify systemd or real SDK builds.

Available commands: `list`, `show`, `validate`, `check`, `fetch`, `build`, `test`, `artifacts`, `deploy`, `status`, `health`, `releases`, `rollback`, `history`, `logs`, `doctor`, `check-all`, `recover`, `import-legacy`. Options work before or after the command: `--dry-run`, `--verbose`, `--json`, `--version VERSION`, `--commit SHA`, `--artifact PATH`, `--approve`, `--force`, `--no-notify`, `--signing-key PATH`, directory overrides and `--max-builds`. Bare `scdeploy --version` prints the manager version.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

See [installation](docs/installation.md), [configuration](docs/configuration.md), [providers](docs/project-providers.md), [deployment](docs/deployment.md), [rollback](docs/rollback.md), [security](docs/security.md), [audit](docs/audit.md), [troubleshooting](docs/troubleshooting.md), [examples](docs/examples.md) and [acceptance status](docs/implementation-status.md).
