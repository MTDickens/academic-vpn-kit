#!/usr/bin/env bash
set -euo pipefail
umask 077
KIT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "setup" || "${1:-}" == "deps" ]]; then
  if [[ "$EUID" != 0 ]]; then
    echo 'setup/deps 需要 root；其他生成命令不需要。' >&2
    exit 1
  fi
  if ! /usr/bin/python3 -c 'import cryptography, qrcode, PIL' 2>/dev/null || ! command -v curl >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y python3 python3-cryptography python3-qrcode python3-pil ca-certificates curl
  fi
  if [[ "$1" == "deps" ]]; then exit 0; fi
fi
exec /usr/bin/python3 "$KIT_ROOT/scripts/avpn.py" "$@"
