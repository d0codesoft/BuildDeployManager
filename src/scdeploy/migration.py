"""Read-only import of legacy layouts; no automatic production cutover."""

import hashlib
from pathlib import Path

from scdeploy.execution import Runner


def inspect_scloud(runner: Runner, nginx_directory: Path = Path("/etc/nginx/sites-enabled")) -> dict:
    """Discover actual service paths and preserve configuration fingerprints."""
    services = {}
    for name in ["scloud.service", "kestrel-scloud.service", "next-scloud-client.service"]:
        result = runner.run(["/usr/bin/systemctl", "show", name,
                             "--property=LoadState,ActiveState,FragmentPath,WorkingDirectory,ExecStart,User"], check=False)
        services[name] = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    nginx = []
    for path in sorted(nginx_directory.glob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in ("scloud", "example.com", "5770")):
            nginx.append({"path": str(path), "resolved": str(path.resolve()),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "has_wiki": "/wiki" in text, "has_tls": "ssl_certificate" in text})
    paths = {}
    for value in ["/opt/scloud", "/var/www/scloud", "/var/www/scloud-client"]:
        path = Path(value)
        paths[value] = {"exists": path.exists(), "symlink": path.is_symlink(), "resolved": str(path.resolve())}
    return {"mode": "observation", "services": services, "nginx": nginx, "paths": paths,
            "plan": ["Confirm the single authoritative backend unit; disable duplicate startup manually.",
                     "Back up service files, app data, database and active nginx configuration separately.",
                     "Copy the running application to an initial retained release and test recovery in staging.",
                     "Review WorkingDirectory/ExecStart against current/server and current/admin.",
                     "Preserve /wiki, TLS and other virtual hosts; nginx -t before a reviewed reload.",
                     "Enable deployment only after a maintenance-window cutover and health verification."]}
