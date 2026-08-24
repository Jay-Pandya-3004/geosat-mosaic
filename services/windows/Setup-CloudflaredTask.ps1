# Registers a scheduled task to run the Cloudflare Tunnel connector as
# the deploying user at startup, instead of cloudflared's native Windows
# service (which crash-loops under LocalSystem in this environment -- 32+
# failures observed, empty --logfile output even on failure, root cause
# not found; running the identical command as a real logged-on user works
# perfectly). Mirrors the already-proven Mount-THREDDS-Disk task pattern
# in this same project: AtStartup trigger, S4U logon (no stored
# password, no interactive session needed, survives a cold boot).
#
# The action runs run-cloudflared-tunnel.ps1 rather than cloudflared.exe
# directly -- that wrapper waits for network/DNS readiness before
# starting cloudflared, and restarts it itself if it exits, since the
# RestartCount/RestartInterval settings below turned out not to actually
# retry after a boot-time DNS failure (2026-08-22).

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument '-ExecutionPolicy Bypass -File "C:\Scripts\run-cloudflared-tunnel.ps1"'

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal -UserId '<windows-username>' -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName "Cloudflared-Tunnel" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force

Write-Output "Registered scheduled task 'Cloudflared-Tunnel'."
