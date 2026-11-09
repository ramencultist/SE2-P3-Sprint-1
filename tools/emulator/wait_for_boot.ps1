# Module Name: wait_for_boot.ps1
# Description: Waits for the emulator to finish booting, then unlocks it and keeps the screen on
#
# Inputs: Port and timeout
# Outputs: A booted, unlocked emulator
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script the boot wait
# How you validated and revised the AI output: Proofreading, and use before every emulator run
# The challenges or limitations you faced while using AI: Slow cold boots on a loaded host
# Authors: Blake Pennel
# Creation Date: 11/5/2026

<#
.SYNOPSIS
  Wait until the emulator reports sys.boot_completed=1, then unlock the screen and keep it on.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\emulator\wait_for_boot.ps1 -TimeoutSec 400
#>
param(
    [int]$Port = 5554,
    [int]$TimeoutSec = 400
)
. "$PSScriptRoot\_env.ps1"

$serial = Get-Serial $Port
$deadline = (Get-Date).AddSeconds($TimeoutSec)
Write-Host "Waiting for $serial to boot (timeout $TimeoutSec s)..."
$booted = $false
while ((Get-Date) -lt $deadline) {
    if (Test-DeviceOnline $serial) {
        $v = (Invoke-Adb $serial shell getprop sys.boot_completed | Select-Object -Last 1)
        if ("$v".Trim() -eq '1') { $booted = $true; break }
    }
    Start-Sleep -Seconds 3
}
if (-not $booted) {
    Write-Host "TIMEOUT: $serial did not finish booting in $TimeoutSec s"
    exit 1
}
# unlock, stay awake while plugged in, no screen timeout
Invoke-Adb $serial shell input keyevent 82 | Out-Null
Invoke-Adb $serial shell svc power stayon true | Out-Null
Invoke-Adb $serial shell settings put system screen_off_timeout 1800000 | Out-Null
$size = (Invoke-Adb $serial shell wm size) -join ' '
$dens = (Invoke-Adb $serial shell wm density) -join ' '
Write-Host "BOOTED $serial | $size | $dens"
exit 0
