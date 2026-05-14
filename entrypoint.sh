#!/usr/bin/env bash
# robomp container entrypoint. No per-boot pip installs — everything is baked
# into the image; we only sanity-check the runtime mount and create state dirs.
set -euo pipefail

: "${PI_ROOT:=/work/pi}"
if [ ! -d "$PI_ROOT/packages/coding-agent" ]; then
  echo "robomp: $PI_ROOT does not look like a pi checkout — bind-mount it at $PI_ROOT" >&2
  exit 2
fi

mkdir -p /data/workspaces /data/logs
exec "$@"
