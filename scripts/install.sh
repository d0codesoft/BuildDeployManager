#!/usr/bin/env bash
set -euo pipefail
umask 027
[[ ${EUID} -eq 0 ]] || { echo 'Run installer as root'; exit 2; }
source /etc/os-release
[[ ${ID} == ubuntu && ${VERSION_ID} == 24.04 ]] || {
  echo 'Only Ubuntu 24.04 is verified by the installer'; exit 2;
}
python3 -c 'import sys; assert sys.version_info >= (3,12)' || {
  echo 'Install python3, python3-venv and python3-pip first'; exit 2;
}
command -v sudo >/dev/null || { echo 'Install sudo first'; exit 2; }
command -v flock >/dev/null || { echo 'Install util-linux first'; exit 2; }
exec 9>/run/scdeploy-install.lock
flock -n 9 || { echo 'Installer already running'; exit 4; }
src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
for path in /opt /opt/scdeploy /opt/scdeploy/releases /etc/scdeploy /etc/scdeploy/projects /etc/scdeploy/secrets /var/lib/scdeploy /var/log/scdeploy /etc/sudoers.d/scdeploy-build /usr/local/bin/scdeploy; do
  [[ ! -L $path ]] || { echo 'Installation target must not be a symlink'; exit 2; }
done
id scdeploy >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/scdeploy --shell /usr/sbin/nologin scdeploy
id scdeploy-build >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/scdeploy-build --shell /usr/sbin/nologin scdeploy-build
usermod -a -G scdeploy-build scdeploy
printf '%s\n' 'scdeploy ALL=(scdeploy-build) NOPASSWD: ALL' >/etc/sudoers.d/scdeploy-build
chmod 0440 /etc/sudoers.d/scdeploy-build
visudo -cf /etc/sudoers.d/scdeploy-build
install -d -m 0755 /opt/scdeploy /opt/scdeploy/releases
install -d -m 0750 -o root -g scdeploy /etc/scdeploy /etc/scdeploy/projects
install -d -m 0700 -o scdeploy -g scdeploy /etc/scdeploy/secrets
install -d -m 0711 -o scdeploy -g scdeploy /var/lib/scdeploy
install -d -m 0700 -o scdeploy -g scdeploy /var/lib/scdeploy/artifacts /var/lib/scdeploy/locks
install -d -m 0750 -o scdeploy -g scdeploy /var/log/scdeploy
release="/opt/scdeploy/releases/$(date -u +%Y%m%dT%H%M%S)-$$"
install -d -m 0755 "$release"
# Both runtime identities must be able to read and execute the root-owned code.
# Keep the restrictive installer umask for state, logs and secret directories.
(
  umask 022
  python3 -m venv "$release/venv"
  "$release/venv/bin/pip" install --disable-pip-version-check "$src"
)
install -d -m 0755 "$release/scripts"
install -m 0755 "$src/scripts/rollback-manager.sh" "$release/scripts/rollback-manager.sh"
old=''
[[ ! -L /opt/scdeploy/current ]] || old=$(readlink -f /opt/scdeploy/current)
rollback_install() {
  if [[ -n $old ]]; then
    ln -s "$old" /opt/scdeploy/.restore-$$
    mv -Tf /opt/scdeploy/.restore-$$ /opt/scdeploy/current
  fi
  echo 'Manager update failed; previous installation retained' >&2
}
trap rollback_install ERR
sudo -u scdeploy "$release/venv/bin/scdeploy" doctor --json >/dev/null
if [[ -n $old ]]; then
  ln -s "$old" /opt/scdeploy/.previous-$$
  mv -Tf /opt/scdeploy/.previous-$$ /opt/scdeploy/previous
fi
ln -s "$release" /opt/scdeploy/.current-$$
mv -Tf /opt/scdeploy/.current-$$ /opt/scdeploy/current
cat >/usr/local/bin/scdeploy <<'EOF'
#!/bin/sh
exec /opt/scdeploy/current/venv/bin/scdeploy "$@"
EOF
chmod 0755 /usr/local/bin/scdeploy
install -m 0755 "$src/scripts/rollback-manager.sh" /opt/scdeploy/rollback-manager.sh
for file in "$src"/projects/examples/*.yaml; do
  target="/etc/scdeploy/projects/$(basename "$file").example"
  [[ -e $target ]] || install -m 0640 -o root -g scdeploy "$file" "$target"
done
install -m 0644 "$src/systemd/scdeploy-check.service" /etc/systemd/system/scdeploy-check.service
install -m 0644 "$src/systemd/scdeploy-check.timer" /etc/systemd/system/scdeploy-check.timer
systemctl daemon-reload
sudo -u scdeploy /usr/local/bin/scdeploy doctor --json
trap - ERR
echo 'Installed. Timer remains opt-in: systemctl enable --now scdeploy-check.timer'
