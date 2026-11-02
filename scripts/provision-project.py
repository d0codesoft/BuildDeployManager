#!/usr/bin/env python3
"""Administrator-only creation of a dedicated project root and exact sudo rules."""

import argparse
import os
import pwd
import subprocess
import tempfile
from pathlib import Path

from scdeploy.config import load
from scdeploy.errors import PolicyError
from scdeploy.security import no_symlink_parents, trusted_configs


def main() -> None:
    """Provision reviewed directories without changing application units or Nginx."""
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.approve:
        raise PolicyError("Run as root with --approve after reviewing project YAML")
    trusted_configs(args.config.parent)
    config = load(args.config)
    root = Path(config.deploy.release_directory).parent
    if root != Path("/opt") / config.project.name:
        raise PolicyError("Provisioner supports only /opt/<project-id>; provision custom roots manually")
    no_symlink_parents(root)
    user = pwd.getpwnam("scdeploy")
    for path in [root, root / "releases", root / "shared"]:
        if path.exists():
            # Existing production layout must be imported, never taken over.
            raise PolicyError("Project directory already exists; use the migration procedure")
    root.mkdir(mode=0o755)
    for path in [root, root / "releases", root / "shared"]:
        path.mkdir(mode=0o755, exist_ok=True)
        os.chown(path, user.pw_uid, user.pw_gid)
    lines = ["# Exact service privileges for " + config.project.name]
    for service in config.deploy.services:
        for action in ["start", "stop", "restart"]:
            lines.append(f"scdeploy ALL=(root) NOPASSWD: /usr/bin/systemctl {action} {service}")
    target = Path("/etc/sudoers.d") / ("scdeploy-" + config.project.name)
    if target.exists():
        raise PolicyError("Existing sudoers policy must be reviewed manually")
    with tempfile.NamedTemporaryFile(mode="w", dir="/etc/sudoers.d", prefix=".scdeploy-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write("\n".join(lines) + "\n")
    try:
        temporary.chmod(0o440)
        subprocess.run(["/usr/sbin/visudo", "-cf", str(temporary)], check=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print("Project directory and exact service policy created; units were not changed")


if __name__ == "__main__":
    main()
