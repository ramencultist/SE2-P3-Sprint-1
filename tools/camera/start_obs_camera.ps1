# Module Name: start_obs_camera.ps1
# Description: Turns a dashcam clip into a webcam with OBS Studio's virtual camera so the live camera path can be tested
#
# Inputs: A dashcam clip, or a stop switch
# Outputs: An OBS virtual camera playing the clip
# External Sources: Claude Fable 5.1
# Attributions:
#
# Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
# A description of how and why AI was used: Claude Fable 5.1 used to script a fake live camera for testing without a real camera
# How you validated and revised the AI output: Proofreading, and running the desktop pipeline against the virtual camera
# The challenges or limitations you faced while using AI: Configuring OBS profiles and scenes from a script
# Authors: Blake Pennel
# Creation Date: 11/6/2026

<#
.SYNOPSIS
  Turn a dashcam clip into a webcam with OBS Studio's virtual camera, so the live-camera path
  can be tested without a real camera: the desktop pipeline opens it as camera index 0 and the
  Android emulator as "-camera-back webcam0" (see tools/emulator/start_emulator.ps1 -Webcam).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\camera\start_obs_camera.ps1
  powershell -ExecutionPolicy Bypass -File tools\camera\start_obs_camera.ps1 -Clip assets\videos\dashcam_daylight_720p.mp4
  powershell -ExecutionPolicy Bypass -File tools\camera\start_obs_camera.ps1 -Stop
.NOTES
  Writes an OBS profile and scene collection both named "wazeomatic" into %APPDATA%\obs-studio
  (1280x720 at 30 fps, one looping media source) and starts OBS minimised with the virtual
  camera on. Needs OBS Studio 26 or newer (tested with 30.2). Nothing else in your OBS setup
  is touched; pick your own profile/collection again in OBS afterwards if you use it for
  something else.
#>
param(
    [string]$Clip = 'assets\videos\dashcam_urban_1080p.mp4',
    [string]$ObsExe = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe',
    [switch]$Stop
)
$ErrorActionPreference = 'Stop'

if ($Stop) {
    Get-Process obs64 -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host 'OBS stopped.'
    return
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$clipPath = if ([System.IO.Path]::IsPathRooted($Clip)) { $Clip } else { Join-Path $repo $Clip }
if (-not (Test-Path $clipPath)) { throw "clip not found: $clipPath" }
if (-not (Test-Path $ObsExe)) { throw "OBS not found at $ObsExe" }
$clipFwd = (Resolve-Path $clipPath).Path -replace '\\', '/'

$obsDir = Join-Path $env:APPDATA 'obs-studio'
$profileDir = Join-Path $obsDir 'basic\profiles\wazeomatic'
$scenesDir = Join-Path $obsDir 'basic\scenes'
New-Item -ItemType Directory -Force $profileDir, $scenesDir | Out-Null

# Profile: 1280x720 canvas and output at 30 fps, like the phone camera frames we analyse.
@"
[General]
Name=wazeomatic

[Video]
BaseCX=1280
BaseCY=720
OutputCX=1280
OutputCY=720
FPSType=0
FPSCommon=30
ScaleType=bicubic

[Output]
Mode=Simple

[Audio]
SampleRate=48000
ChannelSetup=Stereo
"@ | Set-Content -Encoding ASCII (Join-Path $profileDir 'basic.ini')

$sceneUuid = [guid]::NewGuid().ToString()
$clipUuid = [guid]::NewGuid().ToString()
$collection = [ordered]@{
    name = 'wazeomatic'
    current_scene = 'Dashcam'
    current_program_scene = 'Dashcam'
    current_transition = 'Cut'
    transition_duration = 300
    preview_locked = $false
    scaling_enabled = $false
    scaling_level = 0
    scaling_off_x = 0
    scaling_off_y = 0
    scene_order = @(@{ name = 'Dashcam' })
    groups = @()
    quick_transitions = @()
    saved_projectors = @()
    transitions = @()
    modules = @{}
    sources = @(
        [ordered]@{
            id = 'scene'; versioned_id = 'scene'; name = 'Dashcam'; uuid = $sceneUuid
            enabled = $true; muted = $false; volume = 1.0; balance = 0.5; sync = 0; flags = 0
            mixers = 0; monitoring_type = 0; 'push-to-mute' = $false; 'push-to-talk' = $false
            hotkeys = @{}; private_settings = @{}; filters = @()
            settings = [ordered]@{
                custom_size = $false; id_counter = 1
                items = @(
                    [ordered]@{
                        name = 'Clip'; source_uuid = $clipUuid; id = 1; visible = $true; locked = $false
                        rot = 0.0; pos = @{ x = 0.0; y = 0.0 }; scale = @{ x = 1.0; y = 1.0 }
                        align = 5; bounds_type = 2; bounds_align = 0; bounds = @{ x = 1280.0; y = 720.0 }
                        crop_left = 0; crop_top = 0; crop_right = 0; crop_bottom = 0
                        scale_filter = 'disable'; blend_method = 'default'; blend_type = 'normal'
                        show_transition = @{ duration = 0 }; hide_transition = @{ duration = 0 }
                        group_item_backup = $false; private_settings = @{}
                    }
                )
            }
        },
        [ordered]@{
            id = 'ffmpeg_source'; versioned_id = 'ffmpeg_source'; name = 'Clip'; uuid = $clipUuid
            enabled = $true; muted = $true; volume = 0.0; balance = 0.5; sync = 0; flags = 0
            mixers = 0; monitoring_type = 0; 'push-to-mute' = $false; 'push-to-talk' = $false
            hotkeys = @{}; private_settings = @{}; filters = @()
            settings = [ordered]@{
                is_local_file = $true; local_file = $clipFwd; looping = $true
                restart_on_activate = $true; close_when_inactive = $false; hw_decode = $false
                clear_on_media_end = $false; speed_percent = 100
            }
        }
    )
}
$collection | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 (Join-Path $scenesDir 'wazeomatic.json')

Get-Process obs64 -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800
$args = @('--profile', 'wazeomatic', '--collection', 'wazeomatic', '--scene', 'Dashcam',
          '--startvirtualcam', '--minimize-to-tray', '--disable-shutdown-check', '--disable-updater')
Write-Host "Starting OBS virtual camera with $clipFwd"
Start-Process -FilePath $ObsExe -WorkingDirectory (Split-Path $ObsExe) -ArgumentList $args | Out-Null
Write-Host 'OBS started. The clip now appears as a webcam ("OBS Virtual Camera").'
Write-Host 'Desktop:  .venv\Scripts\python -m wazeomatic.run_video 0 --camera --show'
Write-Host 'Emulator: powershell -File tools\emulator\start_emulator.ps1 -Webcam   (back camera = webcam0)'
Write-Host 'Stop:     powershell -File tools\camera\start_obs_camera.ps1 -Stop'
