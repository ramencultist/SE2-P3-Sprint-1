# Module Name: push_video.ps1
# Description: Converts a dashcam clip to H.264 720p MP4 and pushes it to the emulator for the detector's video mode
#
# Inputs: A source clip, output name and optional length
# Outputs: test.mp4 on the emulator
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script loading pre-recorded test video onto the emulator
# How you validated and revised the AI output: Proofreading, and Stage B runs using the pushed video
# The challenges or limitations you faced while using AI: Android needs H.264 MP4, so the clips are converted with ffmpeg first
# Authors: Blake Pennel
# Creation Date: 11/5/2026

<#
.SYNOPSIS
  Convert a dashcam clip to H.264 720p30 mp4 and push it to the emulator for the detector's
  video mode (/sdcard/Android/data/com.wazeomatic.detector/files/test.mp4 and /sdcard/Download/).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\emulator\push_video.ps1
  powershell -ExecutionPolicy Bypass -File tools\emulator\push_video.ps1 -InputFile F:\dev\datasets\dashcam\dashcam_daylight_720p.ogv -Seconds 60
#>
param(
    [string]$InputFile = 'F:\dev\datasets\dashcam\dashcam_urban_1080p.ogv',
    [string]$Name = 'test.mp4',
    [int]$Seconds = 0,     # 0 = whole clip
    [int]$Port = 5554,
    [switch]$Reencode      # convert again even if the mp4 is newer than the source
)
. "$PSScriptRoot\_env.ps1"
$serial = Get-Serial $Port
if (-not (Test-Path $InputFile)) { throw "Input not found: $InputFile" }

$base = [IO.Path]::GetFileNameWithoutExtension($InputFile)
$suffix = if ($Seconds -gt 0) { "_${Seconds}s" } else { '' }
$out = Join-Path $RunsDir "${base}_720p30${suffix}.mp4"
if ($Reencode -or -not (Test-Path $out) -or ((Get-Item $out).LastWriteTime -lt (Get-Item $InputFile).LastWriteTime)) {
    $ffArgs = @('-hide_banner', '-loglevel', 'error', '-y', '-i', $InputFile)
    if ($Seconds -gt 0) { $ffArgs += @('-t', "$Seconds") }
    $ffArgs += @('-vf', 'scale=-2:720,fps=30', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
        '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-an', '-movflags', '+faststart', $out)
    Write-Host "ffmpeg -> $out"
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $Ffmpeg @ffArgs 2>&1 | ForEach-Object { "$_" } | Write-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { throw "ffmpeg failed ($code)" }
} else {
    Write-Host "Reusing $out"
}
Write-Host ("{0:N1} MB" -f ((Get-Item $out).Length / 1MB))

if (-not (Test-DeviceOnline $serial)) { throw "$serial is not online" }
$detDir = "/sdcard/Android/data/$DetectorPkg/files"
Invoke-Adb $serial shell mkdir -p $detDir | Out-Null
foreach ($dest in @("$detDir/$Name", "/sdcard/Download/$Name")) {
    $r = Invoke-Adb $serial push $out $dest
    $r | Write-Host
    if ($script:AdbExit -ne 0) { throw "adb push to $dest failed" }
}
# let the gallery / file pickers see the Download copy
Invoke-Adb $serial shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "file:///sdcard/Download/$Name" | Out-Null
Write-Host "Pushed to $detDir/$Name and /sdcard/Download/$Name"
