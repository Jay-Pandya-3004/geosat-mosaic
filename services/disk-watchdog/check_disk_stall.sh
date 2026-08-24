#!/bin/bash
# Watches for the transient disk-I/O stall caught 2026-08-21 while
# checking geosat-mosaic-api's /point-probe: both uvicorn workers were
# in D-state (uninterruptible sleep, blocked on disk I/O) and a
# direct `dd` read of a data file timed out, then fully cleared on
# its own within ~1-2 minutes. No storvsc/SCSI errors were logged for
# it in dmesg, unlike the previously-fixed (2026-08-15) USB-selective-
# suspend fault -- so this is a different, not-yet-explained stall
# pattern. Only logs when it actually catches one (same "quiet unless
# something's wrong" convention as xcube's memory/hdf-error watchdogs),
# so an empty log after a few hours means it didn't recur, not that the
# check didn't run.

LOG_FILE="/opt/disk-watchdog/stall.log"
TEST_FILE="/srv/mosaic-data/tiera/tiera_<timestamp>.nc"  # substitute a real, currently-present file

log() {
    echo "$(date -Is)  $1" >> "$LOG_FILE"
}

d_state_count=$(ps -eo stat= | grep -c '^D')

dd_start=$(date +%s.%N)
timeout 5 dd if="$TEST_FILE" of=/dev/null bs=1M count=5 >/dev/null 2>&1
dd_exit=$?
dd_end=$(date +%s.%N)
dd_elapsed=$(echo "$dd_end - $dd_start" | bc)

if [ "$dd_exit" -ne 0 ] || [ "$d_state_count" -gt 0 ]; then
    log "STALL CAUGHT: dd_exit=$dd_exit dd_elapsed=${dd_elapsed}s d_state_processes=$d_state_count"
    log "  D-state processes: $(ps -eo pid,stat,etime,cmd | awk '$2 ~ /^D/')"
    log "  dmesg tail (storvsc/scsi/xfs/error): $(dmesg | tail -100 | grep -iE 'storvsc|status: scsi|sd 0:0:0|xfs|I/O error' | tail -10 | tr '\n' '|')"
    log "  free -h: $(free -h | tr '\n' '|')"
fi
