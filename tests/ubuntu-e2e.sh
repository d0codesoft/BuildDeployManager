#!/usr/bin/env bash
# Opt-in destructive acceptance fixture, exclusively for disposable Ubuntu 24.04 VMs.
set -euo pipefail
[[ ${EUID} -eq 0 && ${1:-} == --isolated-vm && ${2:-} == --approve ]] || {
  echo 'Run only in a disposable VM: sudo bash tests/ubuntu-e2e.sh --isolated-vm --approve'; exit 2;
}
source /etc/os-release
[[ ${ID} == ubuntu && ${VERSION_ID} == 24.04 ]] || exit 2
[[ -d /run/systemd/system ]] || { echo 'A running systemd is required'; exit 2; }
for path in /opt/scdeploy-e2e /etc/scdeploy/projects/scdeploy-e2e.yaml /etc/systemd/system/scdeploy-e2e.service /var/lib/scdeploy/e2e-state /etc/sudoers.d/scdeploy-scdeploy-e2e; do
  [[ ! -e $path ]] || { echo "Refusing existing fixture path: $path"; exit 2; }
done
base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
bash "$base/scripts/install.sh"
tmp=$(mktemp -d /tmp/scdeploy-e2e.XXXXXX)
chmod 0755 "$tmp"
cleanup() {
  systemctl stop scdeploy-e2e.service || true
  rm -f /etc/systemd/system/scdeploy-e2e.service /etc/scdeploy/projects/scdeploy-e2e.yaml /etc/sudoers.d/scdeploy-scdeploy-e2e
  systemctl daemon-reload
  # All recursive deletion targets are fixed fixture paths checked absent above.
  rm -rf -- /opt/scdeploy-e2e /var/lib/scdeploy/e2e-state
  case "$tmp" in /tmp/scdeploy-e2e.*) rm -rf -- "$tmp";; esac
}
trap cleanup EXIT
id scdeploy-e2e >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin scdeploy-e2e
"/opt/scdeploy/current/venv/bin/python" - "$tmp" <<'PY'
import sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scdeploy.artifacts import ArtifactStore, create_manifest
root = Path(sys.argv[1])
key = Ed25519PrivateKey.generate()
private = root / 'signer.pem'
private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
private.chmod(0o600)
public = root / 'public.pem'
public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
for version, script in [('1.0.0', 'from http.server import HTTPServer,BaseHTTPRequestHandler\nclass Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200); self.end_headers(); self.wfile.write(b"healthy")\nHTTPServer(("127.0.0.1",19571),Handler).serve_forever()\n'), ('2.0.0', 'raise SystemExit(1)\n')]:
    payload = root / version
    payload.mkdir()
    (payload / 'app.py').write_text(script)
    m = create_manifest(payload, 'scdeploy-e2e', version, 'a'*40, 'linux-x64', 'Release', private)
    archive = ArtifactStore(root / 'artifacts').publish(payload,m)
    archive.chmod(0o644)
PY
cat >/etc/scdeploy/projects/scdeploy-e2e.yaml <<EOF
schema_version: 1
project:
  name: scdeploy-e2e
  environment: staging
source:
  type: git
  repository: https://example.invalid/repo
  public_key: $tmp/public.pem
deploy:
  release_directory: /opt/scdeploy-e2e/releases
  current_link: /opt/scdeploy-e2e/current
  observe_only: false
  services: [scdeploy-e2e.service]
  healthchecks:
    - type: http
      url: http://127.0.0.1:19571/health
      body_contains: healthy
      timeout_seconds: 5
      interval_seconds: 0.2
EOF
chown root:scdeploy /etc/scdeploy/projects/scdeploy-e2e.yaml
chmod 0640 /etc/scdeploy/projects/scdeploy-e2e.yaml
/opt/scdeploy/current/venv/bin/python "$base/scripts/provision-project.py" /etc/scdeploy/projects/scdeploy-e2e.yaml --approve
cat >/etc/systemd/system/scdeploy-e2e.service <<'EOF'
[Unit]
Description=Disposable SCDEPLOY acceptance fixture
[Service]
Type=simple
User=scdeploy-e2e
WorkingDirectory=/opt/scdeploy-e2e/current
ExecStart=/usr/bin/python3 /opt/scdeploy-e2e/current/app.py
EOF
systemctl daemon-reload
run() { sudo -u scdeploy /usr/local/bin/scdeploy "$@" --data-dir /var/lib/scdeploy/e2e-state --no-notify; }
run deploy scdeploy-e2e --artifact "$tmp/artifacts/scdeploy-e2e/scdeploy-e2e-1.0.0-linux-x64.tar.gz" --approve
if run deploy scdeploy-e2e --artifact "$tmp/artifacts/scdeploy-e2e/scdeploy-e2e-2.0.0-linux-x64.tar.gz" --approve; then
  echo 'Unhealthy release was incorrectly accepted'; exit 1
fi
[[ $(readlink -f /opt/scdeploy-e2e/current) == /opt/scdeploy-e2e/releases/1.0.0 ]]
systemctl is-active --quiet scdeploy-e2e.service
run health scdeploy-e2e
run history scdeploy-e2e --json
# Exercise the primary source pipeline against a local owned Git upstream.
mkdir "$tmp/upstream"
cat >"$tmp/upstream/app.py" <<'PY'
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'healthy')
HTTPServer(('127.0.0.1', 19571), Handler).serve_forever()
PY
git -C "$tmp/upstream" init -b main
git -C "$tmp/upstream" -c user.name=E2E -c user.email=e2e@example.invalid add app.py
git -C "$tmp/upstream" -c user.name=E2E -c user.email=e2e@example.invalid commit -m good
chown -R scdeploy:scdeploy "$tmp/upstream"
cat >/etc/scdeploy/projects/scdeploy-e2e.yaml <<EOF
schema_version: 1
project:
  name: scdeploy-e2e
  environment: staging
source:
  type: git
  repository: file://$tmp/upstream
  branch: main
build:
  provider: command
  tests: false
  commands:
    - [python3, -c, "from pathlib import Path; import shutil; Path('dist').mkdir(); shutil.copy2('app.py', 'dist/app.py')"]
deploy:
  release_directory: /opt/scdeploy-e2e/releases
  current_link: /opt/scdeploy-e2e/current
  observe_only: false
  services: [scdeploy-e2e.service]
  healthchecks:
    - type: http
      url: http://127.0.0.1:19571/health
      body_contains: healthy
      timeout_seconds: 5
      interval_seconds: 0.2
EOF
# The signed baseline is no longer needed after its real recovery was checked.
# Retain its key while transitioning to local source builds so rollback can
# still authenticate it if the first source deployment fails.
/opt/scdeploy/current/venv/bin/python - /etc/scdeploy/projects/scdeploy-e2e.yaml "$tmp/public.pem" <<'PY'
import sys, yaml
from pathlib import Path
p = Path(sys.argv[1])
config = yaml.safe_load(p.read_text())
config['source']['public_key'] = sys.argv[2]
p.write_text(yaml.safe_dump(config))
PY
chown root:scdeploy /etc/scdeploy/projects/scdeploy-e2e.yaml
chmod 0640 /etc/scdeploy/projects/scdeploy-e2e.yaml
run deploy scdeploy-e2e --approve
installed=$(readlink -f /opt/scdeploy-e2e/current)
[[ $(cat "$installed/app.py") != 'raise SystemExit(1)' ]]
run deploy scdeploy-e2e --approve
[[ $(readlink -f /opt/scdeploy-e2e/current) == "$installed" ]]
printf 'raise SystemExit(1)\n' >"$tmp/upstream/app.py"
chown scdeploy:scdeploy "$tmp/upstream/app.py"
sudo -u scdeploy git -C "$tmp/upstream" -c user.name=E2E -c user.email=e2e@example.invalid commit -am bad
if run deploy scdeploy-e2e --approve; then
  echo 'Unhealthy source-built release was incorrectly accepted'; exit 1
fi
[[ $(readlink -f /opt/scdeploy-e2e/current) == "$installed" ]]
run health scdeploy-e2e
echo 'Signed artifact and local Git build/publish/release rollback passed against real systemd'
