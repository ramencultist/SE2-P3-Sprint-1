# Module Name: screenrecord.ps1
# Description: Records the emulator screen for a number of seconds to runs/emulator/
#
# Inputs: Duration, name, output folder, port and size
# Outputs: An MP4 screen recording
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script screen recordings of macro runs
# How you validated and revised the AI output: Proofreading, and recording macro runs on the emulator
# The challenges or limitations you faced while using AI: Android limits screen recordings to 180 seconds
# Authors: Blake Pennel
# Creation Date: 11/5/2026

<#
.SYNOPSIS
  Record the emulator screen for N seconds (max 180, Android limit) into runs\emulator\.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\emulator\screenrecord.ps1 -Seconds 20 -Name macro_run
#>
param(
    [int]$Seconds = 20,
    [string]$Name = '',
    [string]$OutDir = '',
    [int]$Port = 5554,
    [string]$Size = '720x1606'   # half of 1280x2856 keeps files small and the encoder happy
)
. "$PSScriptRoot\_env.ps1"
$serial = Get-Serial $Port
if (-not $Name) { $Name = 'rec_' + (Get-Date -Format 'yyyyMMdd_HHmmss') }
if (-not $OutDir) { $OutDir = $RunsDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Seconds = [Math]::Min([Math]::Max($Seconds, 1), 180)
$remote = "/sdcard/$Name.mp4"
Write-Host "Recording $Seconds s..."
Invoke-Adb $serial shell screenrecord --time-limit $Seconds --size $Size $remote | Write-Host
Start-Sleep -Seconds 1
$local = Join-Path $OutDir "$Name.mp4"
Invoke-Adb $serial pull $remote $local | Out-Null
Invoke-Adb $serial shell rm $remote | Out-Null
Write-Host $local
