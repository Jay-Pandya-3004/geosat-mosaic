# Wrapper invoked by the Cloudflared-Tunnel scheduled task instead of
# launching cloudflared.exe directly. Two problems observed 2026-08-22:
#
# 1. The task fires ~40s after boot, before DNS/network is actually
#    usable -- cloudflared's own edge-discovery lookup for
#    argotunnel.com timed out and it exited immediately. Same class of
#    boot-timing race as mount-thredds-disk.ps1's disk-enumeration
#    retry; fixed the same way here (poll-and-wait instead of assuming
#    readiness).
# 2. The scheduled task's own RestartCount/RestartInterval settings
#    never actually retried after that failure -- LastTaskResult
#    stayed 1, service.log untouched for 15+ minutes, no cloudflared.exe
#    process. Don't rely on that mechanism -- restart cloudflared
#    ourselves if it ever exits.

$ErrorActionPreference = "Stop"
$logFile = "C:\Scripts\run-cloudflared-tunnel.log"
$cloudflaredExe = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$cloudflaredArgs = @(
    '--config', 'C:\Users\<windows-username>\.cloudflared\config.yml',
    '--logfile', 'C:\Users\<windows-username>\.cloudflared\service.log',
    'tunnel', 'run'
)

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Out-File -FilePath $logFile -Append
}

function Test-NetworkReady {
    try {
        $null = Resolve-DnsName -Name 'argotunnel.com' -Type A -DnsOnly -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

Log "=== run-cloudflared-tunnel.ps1 starting ==="

$ready = $false
for ($i = 0; $i -lt 24; $i++) {
    if (Test-NetworkReady) { $ready = $true; break }
    Log "Network/DNS not ready yet (attempt $($i + 1)/24), waiting 5s..."
    Start-Sleep -Seconds 5
}

if (-not $ready) {
    Log "ERROR: network/DNS never became ready after ~2 minutes, giving up."
    exit 1
}

Log "Network ready, starting cloudflared tunnel."

$attempt = 0
while ($attempt -lt 999) {
    $attempt++
    Log "Launching cloudflared (attempt $attempt)..."
    $proc = Start-Process -FilePath $cloudflaredExe -ArgumentList $cloudflaredArgs -NoNewWindow -PassThru -Wait
    Log "cloudflared exited with code $($proc.ExitCode). Restarting in 10s..."
    Start-Sleep -Seconds 10
}

Log "ERROR: exceeded 999 restart attempts, giving up."
exit 1
