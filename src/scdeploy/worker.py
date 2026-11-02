"""Unprivileged build subprocess entry point; contains no manager credentials."""

import os
import sys


def main() -> None:
    """Use group-writable outputs so the manager can package and clean the workspace."""
    if os.name != "posix" or os.geteuid() == 0 or len(sys.argv) < 2:
        raise SystemExit("Build worker requires a non-root Unix identity and a command")
    os.umask(0o007)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    main()
