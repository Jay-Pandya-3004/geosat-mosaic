#!/usr/bin/env bash
# Proactively restarts xcube before it thrashes into unresponsiveness --
# complements xcube-nightly-restart.timer (a fixed 03:00 restart) by
# catching memory growth that happens faster than a day. xcube's
# still-undiagnosed long-uptime memory leak reached 5.3G/3.7G swap and
# full unresponsiveness over just 17h in the 2026-08-16 incident -- a
# once-nightly restart alone wouldn't have caught that in time.
#
# Threshold is 80% of xcube.service's own MemoryHigh=5G cgroup guardrail
# (4GiB), not the swap figure itself -- MemoryHigh is the point at which
# the kernel starts aggressively reclaiming/throttling the cgroup, which
# is what actually *causes* the swap thrashing, so restarting on the way
# up to that point (not at/after it) is what makes this "proactive"
# rather than just a second copy of the guardrail's own kill behavior.

set -euo pipefail

THRESHOLD_BYTES=$((4 * 1024 * 1024 * 1024))  # 4GiB, 80% of xcube.service's MemoryHigh=5G
LOG_FILE=/opt/xcube-deploy/memory-watchdog.log

log() {
    echo "$(date -Iseconds) $1" >> "$LOG_FILE"
}

current=$(systemctl show xcube.service -p MemoryCurrent --value)

# "[not set]" (or empty) means the unit isn't running / cgroup accounting
# isn't available -- nothing to watch, not a failure of this check.
if [[ -z "$current" || "$current" == "[not set]" ]]; then
    exit 0
fi

if (( current >= THRESHOLD_BYTES )); then
    current_gib=$(awk "BEGIN { printf \"%.2f\", $current / 1024 / 1024 / 1024 }")
    log "xcube memory ${current_gib}GiB >= 4.00GiB threshold -- restarting proactively"
    systemctl restart xcube.service
    log "restart issued"
fi
# Healthy case: no log line -- avoids unbounded log growth from a check
# that runs every 5 minutes indefinitely; only actual restarts are logged.
