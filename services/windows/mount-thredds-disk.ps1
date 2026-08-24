$ErrorActionPreference = "Stop"
$logFile = "C:\Scripts\mount-thredds-disk.log"

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Out-File -FilePath $logFile -Append
}

try {
    # At boot, the task fires ~19s after Windows starts, which can be before
    # the USB SSD has finished enumerating (observed 2026-08-19: Get-Disk
    # found nothing at 19s post-boot even though the disk was healthy and
    # present moments later). Retry for up to ~2 minutes before giving up.
    $disk = $null
    for ($i = 0; $i -lt 24; $i++) {
        $disk = Get-Disk | Where-Object { $_.SerialNumber -match '<DISK_SERIAL_NUMBER>' }
        if ($disk) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $disk) {
        Log "ERROR: disk with serial <DISK_SERIAL_NUMBER> not found after retrying."
        exit 1
    }
    Log "Found disk number $($disk.Number) (serial $($disk.SerialNumber)), mounting into WSL2..."
    & wsl.exe --mount "\\.\PHYSICALDRIVE$($disk.Number)" --bare 2>&1 | ForEach-Object { Log $_ }
    Log "wsl --mount exit code: $LASTEXITCODE"

    # Run mount -a (and the check below) inside PID 1's namespaces via nsenter,
    # not just a bare `wsl.exe -u root -- ...`. A plain wsl.exe interop call
    # can land in a private/transient mount namespace that gets torn down
    # when the process exits, so it never becomes visible to the
    # systemd services (tomcat/xcube) -- the mount looked successful in this
    # script's own check but /srv/mosaic-data stayed empty for everyone
    # else (found 2026-08-19). nsenter -t 1 -m pins the mount to init's
    # namespace, which is the one every long-running service actually uses.
    Log "Mounting filesystem inside WSL2 (fstab entry only auto-triggers at boot, not on later device attach)..."
    & wsl.exe -u root -- nsenter -t 1 -m -- mount -a 2>&1 | ForEach-Object { Log $_ }
    & wsl.exe -u root -- nsenter -t 1 -m -- mountpoint -q /srv/mosaic-data
    $mounted = ($LASTEXITCODE -eq 0)
    Log "post mount-a check, /srv/mosaic-data mounted: $mounted"

    if ($mounted) {
        # tomcat/xcube each scan this directory once at their own process
        # startup and cache the result -- systemd starts them immediately
        # at WSL2 boot, well before this task's disk-attach + mount-a
        # above has necessarily finished, so a cold boot can leave
        # them serving zero datasets even after the mount itself is fine.
        # This was previously a manual-workaround-only gap -- closing
        # it here by always restarting them once the mount is confirmed,
        # rather than relying on someone remembering to do it by hand.
        # geosat-mosaic-api deliberately excluded: it reads files
        # per-request with no startup-time cache, doesn't need this.
        Log "Restarting tomcat/xcube so they pick up the now-mounted data..."
        & wsl.exe -u root -- systemctl restart tomcat xcube 2>&1 | ForEach-Object { Log $_ }
        Log "restart command exit code: $LASTEXITCODE"
    } else {
        Log "Mount not confirmed -- skipping tomcat/xcube restart (nothing new for them to pick up)."
    }
}
catch {
    Log "EXCEPTION: $_"
    exit 1
}
