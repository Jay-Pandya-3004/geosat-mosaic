#!/usr/bin/env bash
# Self-healing for the netCDF4/HDF5 non-threadsafe eviction-close race
# (see dataset_cache_patch.py's docstring; found 2026-08-17). Once a
# close() races a concurrent
# read even once, xcube's process-wide HDF5 state gets corrupted such that
# one specific (otherwise-healthy) file then fails deterministically with
# "NetCDF: HDF error" on every subsequent request -- indefinitely, until
# the process restarts. The dataset_cache_patch.py delay reduces how often
# the initial collision happens but can't guarantee zero, and the existing
# memory watchdog only restarts on memory pressure, which may not occur for
# hours after a corruption -- leaving requests failing the whole time.
# This restarts on the corruption signal directly instead of waiting for it
# to coincide with a memory-pressure restart.

set -euo pipefail

LOG_FILE=/opt/xcube-deploy/hdf-error-watchdog.log
THRESHOLD=1  # any HDF error in the lookback window means the process is
             # already in the "permanently poisoned for one file" state --
             # this doesn't self-heal, so don't wait for a
             # count, restart on the first sighting.
LOOKBACK_MINUTES=5  # matches this watchdog's own timer interval, so every
                     # window of log is checked exactly once

log() {
    echo "$(date -Iseconds) $1" >> "$LOG_FILE"
}

count=$(journalctl -u xcube --no-pager -S "-${LOOKBACK_MINUTES}min" 2>/dev/null | grep -c "RuntimeError: NetCDF: HDF error" || true)

if (( count >= THRESHOLD )); then
    log "xcube: ${count} HDF error(s) in last ${LOOKBACK_MINUTES}min -- restarting to clear poisoned HDF5 state"
    systemctl restart xcube.service
    log "restart issued"
fi
