#!/usr/bin/env bash
# Only reviewed full configurations can be applied; never generate a production replacement.
set -euo pipefail
[[ ${EUID} -eq 0 && $# -eq 3 && $3 == --approve ]] || {
  echo 'Usage: sudo bash apply-nginx.sh REVIEWED_CONFIG EXISTING_SITE --approve'; exit 2;
}
candidate=$(realpath -e -- "$1")
target=$(realpath -e -- "$2")
case "$target" in /etc/nginx/sites-available/*.conf) ;; *) echo 'Target must be an existing sites-available .conf'; exit 2;; esac
[[ -f $candidate && -f $target ]] || exit 2
backup="${target}.scdeploy-backup-$(date -u +%Y%m%dT%H%M%S)-$$"
cp -p -- "$target" "$backup"
restore() { cp -p -- "$backup" "$target"; nginx -t; systemctl reload nginx; }
trap restore ERR
install -o root -g root -m 0644 -- "$candidate" "${target}.scdeploy-new"
mv -T -- "${target}.scdeploy-new" "$target"
nginx -t
systemctl reload nginx
trap - ERR
echo "Applied reviewed Nginx configuration; backup: $backup"
