#!/usr/bin/env bash
# نصب یک‌خطی:
#   curl -fsSL https://raw.githubusercontent.com/AssA7778/Android-emulator/main/bootstrap.sh | sudo bash
#   ... | sudo bash -s -- --domain droid.example.com
#   ... | sudo bash -s -- --check
set -euo pipefail

REPO=https://github.com/AssA7778/Android-emulator.git
SRC=/opt/android-farm-src

[[ $EUID -eq 0 ]] || { echo "با sudo اجرا کن" >&2; exit 1; }

if ! command -v git >/dev/null; then
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null
fi

if [[ -d $SRC/.git ]]; then
  git -C "$SRC" fetch -q --depth 1 origin main
  git -C "$SRC" reset -q --hard origin/main
else
  rm -rf "$SRC"
  git clone -q --depth 1 "$REPO" "$SRC"
fi

exec bash "$SRC/install.sh" "$@"
