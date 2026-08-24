Start-ScheduledTask -TaskName "Mount-THREDDS-Disk"
Start-Sleep -Seconds 5
Get-Content C:\Scripts\mount-thredds-disk.log -Tail 5
