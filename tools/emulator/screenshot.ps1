# Module Name: screenshot.ps1
# Description: Saves an emulator screenshot to runs/emulator/
#
# Inputs: Name, output folder and port
# Outputs: A PNG screenshot
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script screenshots for test evidence
# How you validated and revised the AI output: Proofreading, and taking screenshots during emulator runs
# The challenges or limitations you faced while using AI: Piping binary data through PowerShell 5.1 corrupts PNGs, so the image is saved on the device and pulled with adb
# Authors: Blake Pennel
# Creation Date: 11/5/2026

<#
.SYNOPSIS
  Save an emulator screenshot to runs\emulator\ (screencap on the device + adb pull, because
  piping binary through PowerShell 5.1 corrupts PNGs).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\emulator\screenshot.ps1 -Name report_sheet
#>
param(
    [string]$Name = '',
    [string]$OutDir = '',
    [int]$Port = 5554
)
. "$PSScriptRoot\_env.ps1"
$serial = Get-Serial $Port
if (-not $Name) { $Name = 'shot_' + (Get-Date -Format 'yyyyMMdd_HHmmss') }
if (-not $OutDir) { $OutDir = $RunsDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$remote = "/sdcard/$Name.png"
Invoke-Adb $serial shell screencap -p $remote | Out-Null
$local = Join-Path $OutDir "$Name.png"
Invoke-Adb $serial pull $remote $local | Out-Null
Invoke-Adb $serial shell rm $remote | Out-Null
if (-not (Test-Path $local)) { throw 'screenshot failed' }
Write-Host $local
