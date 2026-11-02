#!/usr/bin/env bash
# Explicit optional tool installation; no infrastructure reconfiguration.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run as root'; exit 2; }
case ${1:-} in
  git) apt-get install -y git ca-certificates ;;
  dotnet8) apt-get install -y dotnet-sdk-8.0 ;;
  dotnet9) apt-get install -y dotnet-sdk-9.0 ;;
  dotnet10) apt-get install -y dotnet-sdk-10.0 ;;
  node) apt-get install -y nodejs npm ;;
  python) apt-get install -y python3 python3-venv python3-pip ;;
  *) echo 'Choose git, dotnet8, dotnet9, dotnet10, node or python. Configure trusted package repositories separately.'; exit 2 ;;
esac
