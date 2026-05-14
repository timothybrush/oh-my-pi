#!/usr/bin/env bash
# Stage a build-context-shaped slice of $PI_ROOT under .pi-context/.
#
# We can't feed the whole pi checkout to docker — `target/` alone is >100 GB on
# a developer machine. Use rsync to pull only the files the pi-natives build
# (and the omp-rpc wheel build) actually touch.
set -euo pipefail

PI_ROOT=${PI_ROOT:-/work/pi}
STAGE=${1:-.pi-context}

if [ ! -d "$PI_ROOT" ]; then
  echo "stage-pi: PI_ROOT=$PI_ROOT does not exist" >&2
  exit 2
fi

mkdir -p "$STAGE"

# `--delete` keeps the stage faithful when pi files are removed upstream.
rsync -a --delete --info=stats0 \
  --exclude='target/' \
  --exclude='runs/' \
  --exclude='node_modules/' \
  --exclude='.fallow/' \
  --exclude='.worktrees/' \
  --exclude='dist/' \
  --exclude='.git/' \
  --exclude='*.log' \
  --exclude='CPU.*.cpuprofile' \
  --exclude='packages/natives/native/.build/' \
  --exclude='packages/natives/native/pi_natives.darwin-*.node' \
  --exclude='packages/natives/native/pi_natives.dev.node' \
  --exclude='**/__pycache__/' \
  --exclude='**/*.tsbuildinfo' \
  "$PI_ROOT/" "$STAGE/"

du -sh "$STAGE" | awk '{print "stage-pi: prepared "$0}'
