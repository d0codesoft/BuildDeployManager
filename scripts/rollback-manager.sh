#!/usr/bin/env bash
# Independent of the installed Python interpreter and package.
set -euo pipefail
[[ ${EUID} -eq 0 && ${1:-} == --approve ]] || { echo 'Usage: sudo bash rollback-manager.sh --approve'; exit 2; }
previous=$(readlink -f /opt/scdeploy/previous)
case "$previous" in /opt/scdeploy/releases/*) ;; *) echo 'Invalid previous manager release'; exit 2;; esac
[[ -x $previous/venv/bin/scdeploy ]] || { echo 'Previous CLI missing'; exit 2; }
systemctl stop scdeploy-check.timer scdeploy-check.service
ln -s "$previous" /opt/scdeploy/.restore-$$
mv -Tf /opt/scdeploy/.restore-$$ /opt/scdeploy/current
echo 'Previous manager restored. Inspect history and recover pending app operations before restarting the timer.'
