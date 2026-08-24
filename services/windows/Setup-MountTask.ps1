$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Scripts\mount-thredds-disk.ps1"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "<windows-username>" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Unregister-ScheduledTask -TaskName "Mount-THREDDS-Disk" -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName "Mount-THREDDS-Disk" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Auto-attach the mosaic data disk to WSL2 on startup"

Write-Host "Task registered. Verifying:"
Get-ScheduledTask -TaskName "Mount-THREDDS-Disk" | Select-Object TaskName, State
