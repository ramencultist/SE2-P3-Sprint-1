# Module Name: create_avd.ps1
# Description: Creates the pixel9pro_test emulator (Pixel 9 Pro look-alike) used for testing
#
# Inputs: Profile name, system image and camera options
# Outputs: A configured Android Virtual Device
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script the emulator setup so it is repeatable
# How you validated and revised the AI output: Proofreading, and creating the AVD and booting it
# The challenges or limitations you faced while using AI: Matching the Pixel 9 Pro screen size and density
# Authors: Blake Pennel
# Creation Date: 11/5/2026

<#
.SYNOPSIS
  Create the pixel9pro_test AVD (Pixel 9 Pro look-alike) on F:.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\emulator\create_avd.ps1
  powershell -ExecutionPolicy Bypass -File tools\emulator\create_avd.ps1 -Webcam -Force
  # second profile with the Play Store, for installing the real Waze app (sign in by hand):
  powershell -ExecutionPolicy Bypass -File tools\emulator\create_avd.ps1 -Name pixel9pro_play -Image "system-images;android-35;google_apis_playstore;x86_64"
#>
param(
    [string]$Name = 'pixel9pro_test',
    [string]$Image = 'system-images;android-35;google_apis;x86_64',
    [switch]$Webcam,   # back camera = webcam0 (e.g. OBS Virtual Camera) instead of virtualscene
    [switch]$Force     # delete and recreate if it exists
)
. "$PSScriptRoot\_env.ps1"

$avdDir = Join-Path $env:ANDROID_AVD_HOME "$Name.avd"
if ((Test-Path $avdDir) -and -not $Force) {
    Write-Host "AVD $Name already exists ($avdDir). Use -Force to recreate. Updating config.ini only."
} else {
    # Pick the device profile: pixel_9_pro if this cmdline-tools knows it, else the closest Pixel.
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $devices = & $AvdManager list device -c 2>&1 | ForEach-Object { "$_".Trim() }
    $ErrorActionPreference = $old
    $device = $null
    foreach ($d in @('pixel_9_pro', 'pixel_9_pro_xl', 'pixel_8_pro', 'pixel_9', 'pixel_7_pro')) {
        if ($devices -contains $d) { $device = $d; break }
    }
    if (-not $device) { throw 'No Pixel device profile found in avdmanager list device' }
    if ($device -ne 'pixel_9_pro') { Write-Host "pixel_9_pro profile missing, using $device and overriding hardware" }
    Write-Host "Creating AVD $Name from device '$device', image '$Image'"
    $ErrorActionPreference = 'Continue'
    'no' | & $AvdManager create avd -n $Name -k $Image -d $device --force 2>&1 | ForEach-Object { "$_" } | Write-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0 -or -not (Test-Path $avdDir)) { throw "avdmanager create avd failed (exit $code)" }
}

# Hardware overrides to match a Pixel 9 Pro: 1280x2856 at 495 dpi. RAM is the emulator's
# RAM (4 GB), not the phone's 16 GB.
$camera = if ($Webcam) { 'webcam0' } else { 'virtualscene' }
$overrides = [ordered]@{
    'hw.lcd.width'            = '1280'
    'hw.lcd.height'           = '2856'
    'hw.lcd.density'          = '495'
    'hw.ramSize'              = '4096'
    'vm.heapSize'             = '512'
    'hw.cpu.ncore'            = '6'
    'hw.camera.back'          = $camera
    'hw.camera.front'         = 'emulated'
    'hw.gpu.enabled'          = 'yes'
    'hw.gpu.mode'             = 'auto'
    'hw.keyboard'             = 'yes'
    'hw.mainKeys'             = 'no'
    'disk.dataPartition.size' = '8G'
    'showDeviceFrame'         = 'no'
    'skin.dynamic'            = 'yes'
    'skin.name'               = '1280x2856'
    'skin.path'               = '_no_skin'
    'fastboot.forceColdBoot'  = 'no'
}
# Google Play images (tag google_apis_playstore) need this flag or the Play Store app is missing.
$overrides['PlayStore.enabled'] = if ($Image -match 'playstore') { 'yes' } else { 'no' }

$cfg = Join-Path $avdDir 'config.ini'
$lines = @(Get-Content $cfg)
foreach ($k in $overrides.Keys) {
    $pattern = '^' + [regex]::Escape($k) + '\s*='
    $lines = @($lines | Where-Object { $_ -notmatch $pattern })
    $lines += "$k=$($overrides[$k])"
}
# config.ini must be plain ASCII/UTF-8 without BOM
[System.IO.File]::WriteAllLines($cfg, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "AVD ready: $avdDir"
Write-Host "  camera.back=$camera  lcd=1280x2856@495  ram=4096  cores=6"
