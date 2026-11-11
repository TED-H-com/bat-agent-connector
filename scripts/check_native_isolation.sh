#!/usr/bin/env bash
# Test-owned bounded disk volume; no persistent host policy changes.
set -euo pipefail
mkdir -p "$HOME/agent-work/tmp"
isolation_run=$(mktemp -d "$HOME/agent-work/tmp/native-isolation.XXXXXXXX")
isolation_mounted=0
cleanup() {
  if [ "$isolation_mounted" = 1 ]; then
    sudo umount "$isolation_run/volume" || return
  fi
  rm -rf "$isolation_run"
}
trap cleanup EXIT
if [ -z "${BAC_ISOLATION_QUOTA_ROOT:-}" ]; then
  mkdir "$isolation_run/volume"
  truncate -s 64M "$isolation_run/quota.img"
  mkfs.ext4 -q -F "$isolation_run/quota.img"
  sudo mount -o loop,nodev,nosuid,noexec "$isolation_run/quota.img" "$isolation_run/volume"
  isolation_mounted=1
  sudo chown "$(id -u):$(id -g)" "$isolation_run/volume"
  export BAC_ISOLATION_QUOTA_ROOT="$isolation_run/volume"
fi
BAC_REQUIRE_BWRAP=1 \
  uv run pytest -q -rP tests/test_native_isolation.py --basetemp="$isolation_run/pytest"
