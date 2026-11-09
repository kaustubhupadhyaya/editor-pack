# editor-pack — one-shot setup for all tools the skills need (pip + external).
#
#   pwsh -File setup.ps1
#
# Installs: ffmpeg (via winget) and the pip packages in requirements.txt, then
# prints a verification report. Blender / DaVinci Resolve are heavy GUI apps —
# this script only CHECKS for them (install them yourself if you need grade /
# stabilize / timeline features). Descript needs no install (MCP connector).

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
function Have($c){ [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function VerLine($name, $val){ "{0,-14} {1}" -f $name, $val }

Write-Host "== editor-pack setup ==`n" -ForegroundColor Cyan

# 1) ffmpeg + ffprobe (external, via winget) --------------------------------
if (Have ffmpeg) {
  Write-Host "ffmpeg: already present"
} else {
  Write-Host "ffmpeg: installing via winget (Gyan.FFmpeg)..." -ForegroundColor Yellow
  winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
  Write-Host "  (open a new terminal afterwards so ffmpeg is on PATH)"
}

# 2) Python pip packages ----------------------------------------------------
$py = if (Have python) { "python" } elseif (Have py) { "py" } else { $null }
if (-not $py) { Write-Host "`nPython not found. Install Python 3.11+ and re-run." -ForegroundColor Red; exit 1 }
Write-Host "`nInstalling pip packages (pulls torch CPU — sizeable, be patient)..." -ForegroundColor Yellow
& $py -m pip install --user --upgrade -r (Join-Path $root 'requirements.txt')
Write-Host "Installing Playwright's Chromium browser (used by ig-saved-collections' session refresh)..." -ForegroundColor Yellow
& $py -m playwright install chromium

# 3) Verification report ----------------------------------------------------
Write-Host "`n== verification ==" -ForegroundColor Cyan
VerLine "python"  (& $py --version 2>&1)
$ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
$ffmpeg  = (Get-Command ffmpeg  -ErrorAction SilentlyContinue).Source
VerLine "ffmpeg"  ($(if($ffmpeg){ (& ffmpeg -version 2>&1 | Select-Object -First 1) } else { "NOT ON PATH (winget adds it — reopen terminal)" }))
VerLine "libass"  ($(if($ffmpeg){ if((& ffmpeg -version 2>&1 | Select-String 'libass')){"yes (captions OK)"}else{"MISSING - captions need a libass build"} } else { "?" }))
foreach($p in 'yt-dlp','stable-ts','moviepy','auto-editor','numpy','pillow','torch','openai-whisper','gallery-dl','instagrapi','python-dotenv','playwright','pyyaml','google-api-python-client','youtube-transcript-api'){
  $v = (& $py -m pip show $p 2>&1 | Select-String '^Version:').ToString() -replace '^Version:\s*',''
  VerLine $p ($(if($v){$v}else{"not installed"}))
}

# 4) Optional external GUI apps (checked, not installed) --------------------
Write-Host "`n== optional external apps (install manually if needed) ==" -ForegroundColor Cyan
$blender = Get-ChildItem "C:\Program Files\Blender Foundation" -Recurse -Filter blender.exe -ErrorAction SilentlyContinue | Select-Object -First 1
VerLine "Blender"  ($(if($blender){"found: $($blender.FullName)"}else{"not found (only needed for Blender-scripted tasks)"}))
$resolve = Test-Path "C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
VerLine "DaVinci Resolve" ($(if($resolve){"found (use py -3.11 for its API)"}else{"not found (only needed for grade/stabilize/timeline)"}))
VerLine "Descript" "MCP connector - no install"

Write-Host "`nDone. If ffmpeg was just installed, open a new terminal to pick it up." -ForegroundColor Green
