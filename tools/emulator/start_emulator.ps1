# Module Name: start_emulator.ps1
# Description: Starts the test emulator in the background with the chosen camera and GPU options
#
# Inputs: Port, headless, GPU, camera and poster-video options
# Outputs: A running emulator on the given port
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script emulator start-up for the test suite
# How you validated and revised the AI output: Proofreading, and starting the emulator from run_e2e.py
# The challenges or limitations you faced while using AI: The emulator does not list the OBS virtual camera on Windows, so a poster video is used for the back camera
# Authors: Blake Pennel
# Creation Date: 11/5/2026

<#
.SYNOPSIS
  Start the pixel9pro_test emulator on port 5554 (serial emulator-5554) in the background.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\emulator\start_emulator.ps1
  powershell -ExecutionPolicy Bypass -File tools\emulator\start_emulator.ps1 -Headless
  powershell -ExecutionPolicy Bypass -File tools\emulator\start_emulator.ps1 -Webcam   # back cam = webcam0 (a real/UVC webcam)
  powershell -ExecutionPolicy Bypass -File tools\emulator\start_emulator.ps1 -Poster assets\videos\dashcam_urban_1080p.mp4
      # back cam = virtual scene whose wall poster plays the clip (works without any webcam; the
      # emulator does not enumerate the OBS virtual camera on Windows)
#>
param(
    [string]$Avd = 'pixel9pro_test',
    [int]$Port = 5554,
    [switch]$Headless,          # -no-window
    [string]$Gpu = 'auto',      # auto | host | swiftshader_indirect | ...
    [switch]$Webcam,            # -camera-back webcam0 for this run only
    [string]$Poster = '',       # image/mp4 shown on the virtual scene wall (back camera looks at it)
    [switch]$WipeData,
    [switch]$ColdBoot,          # ignore the quickboot snapshot
    [switch]$Wait               # also run wait_for_boot.ps1
)
. "$PSScriptRoot\_env.ps1"

# Never touch the other agent's emulator (wazeomatic_dev on 5560): we only ever use our own port.
$serial = Get-Serial $Port
if (Test-DeviceOnline $serial) {
    Write-Host "$serial is already running, not starting another one."
} else {
    # After a crash (or a force-kill) the next start blocks on a crash-report consent dialog
    # ("Showing crashdialog to get consent" in the log) and looks hung with 1 s of CPU. Drop the
    # pending crash data and skip metrics so unattended starts never wait on that dialog.
    Get-ChildItem (Join-Path $env:TEMP 'AndroidEmulator') -Filter 'emu-crash-*.db' -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    $emuArgs = @('-avd', $Avd, '-port', "$Port", '-gpu', $Gpu, '-no-boot-anim', '-no-audio', '-netdelay', 'none', '-netspeed', 'full', '-no-metrics')
    if ($Headless) { $emuArgs += '-no-window' }
    if ($Webcam) { $emuArgs += @('-camera-back', 'webcam0') }
    if ($Poster) {
        $posterPath = if ([System.IO.Path]::IsPathRooted($Poster)) { $Poster } else { Join-Path $RepoRoot $Poster }
        if (-not (Test-Path $posterPath)) { throw "poster not found: $posterPath" }
        $emuArgs += @('-camera-back', 'virtualscene', '-virtualscene-poster', "wall=$posterPath")
    }
    if ($WipeData) { $emuArgs += '-wipe-data' }
    if ($ColdBoot) { $emuArgs += '-no-snapshot-load' }
    $log = Join-Path $RunsDir "emulator_$Port.log"
    $errLog = Join-Path $RunsDir "emulator_$Port.err.log"
    Write-Host "Starting: emulator $($emuArgs -join ' ')"
    Write-Host "Emulator log: $log"
    $p = Start-Process -FilePath $EmulatorExe -ArgumentList $emuArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError $errLog
    Write-Host "emulator pid $($p.Id)"
}

if ($Wait) {
    & "$PSScriptRoot\wait_for_boot.ps1" -Port $Port
}
