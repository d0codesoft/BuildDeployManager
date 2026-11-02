#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 && ${1:-} == --approve ]] || { echo 'Usage: sudo bash uninstall.sh --approve'; exit 2; }
systemctl disable --now scdeploy-check.timer || true
systemctl stop scdeploy-check.service || true
rm -f /etc/systemd/system/scdeploy-check.timer /etc/systemd/system/scdeploy-check.service
rm -f /usr/local/bin/scdeploy
systemctl daemon-reload
echo 'CLI and scheduler removed. Applications, releases, manager versions, audit, secrets, users and sudoers are retained for reviewed recovery.'
