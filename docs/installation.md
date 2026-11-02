# Installation and manager recovery

Ubuntu 24.04 LTS and Python 3.12+ are required by the installer. Other Ubuntu releases fail closed until verified. Bootstrap required packages explicitly:

```bash
sudo apt-get update
sudo apt-get install python3 python3-venv python3-pip sudo util-linux
sudo bash scripts/install.sh
sudo -u scdeploy scdeploy doctor --json
```

The source tree supplied to the root installer must be administrator-trusted. Dependencies need an accessible trusted Python package index or a prepared wheelhouse (`PIP_NO_INDEX=1 PIP_FIND_LINKS=/path/to/wheels`). No network trust bypass exists.

The installer creates root-owned manager releases under `/opt/scdeploy/releases`, atomically switches `/opt/scdeploy/current`, retains `/opt/scdeploy/previous`, installs a wrapper in `/usr/local/bin`, creates `scdeploy` and `scdeploy-build`, and stores configuration examples with `.yaml.example` extensions so they are not enabled automatically. Rerunning does not overwrite project YAML or secrets. `scdeploy` may invoke any command only as the unprivileged build identity; application root actions are granted separately as exact systemctl commands.

```bash
sudo bash scripts/install-tools.sh git
sudo bash scripts/install-tools.sh dotnet10
sudo bash scripts/install-tools.sh node
```

Tool packages must exist in the administrator-configured trusted APT sources. Node major versions are checked by project configuration; install an appropriate supported version separately. The manager never changes package repositories automatically.

For a **new**, nonexistent application directory:

```bash
sudo install -o root -g scdeploy -m 0640 projects/examples/example-node.yaml /etc/scdeploy/projects/example-node.yaml
sudo /opt/scdeploy/current/venv/bin/python scripts/provision-project.py /etc/scdeploy/projects/example-node.yaml --approve
```

The provisioner refuses existing directories, supports only `/opt/<project-id>` and does not create/change service units. Create the application service as a non-root runtime identity, with WorkingDirectory under the configured current link and absolute ExecStart referencing that same link. Grant read/execute access to released files and only required write access to shared state.

After reviewing update policies:

```bash
sudo systemctl enable --now scdeploy-check.timer
systemctl list-timers scdeploy-check.timer
journalctl -u scdeploy-check.service --no-pager -n 100
```

CPUQuota/MemoryMax for the whole scheduled job are in the systemd unit. Per-build quotas use `systemd-run --user --scope`; a functioning user bus and lingering session are required. Configure that explicitly if needed (`loginctl enable-linger scdeploy` and the user runtime environment). Missing resource enforcement causes build failure, never silent unlimited execution.

For a manager update, stop the timer and ensure no running deployment/build first; rerun the installer from the reviewed new source. To restore the previous manager independently of Python:

```bash
sudo bash /opt/scdeploy/rollback-manager.sh --approve
sudo -u scdeploy scdeploy history PROJECT --json
sudo -u scdeploy scdeploy recover PROJECT --approve
```

This restores executable code, not database schema downgrades. Version 0.1.0 has no destructive schema migrations; back up state.db and its WAL consistently before future upgrades. For removal, `sudo bash scripts/uninstall.sh --approve` unregisters CLI/timer only. Applications, releases, profiles, history and recovery files remain.
