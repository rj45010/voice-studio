# Voice Studio installer for Windows: irm <url>/install.ps1 | iex
$ErrorActionPreference = "Stop"

$Repo = "https://voice-studio.seemslegit.workers.dev"
$Site = "https://voice-studio.seemslegit.workers.dev"
$App = Join-Path $HOME ".voice-studio\app"

function Say($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "Installing uv (Python installer)"
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$HOME\.local\bin;$env:Path"
}

Say "Installing the voice engine (about 1 GB, more with an NVIDIA GPU)"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  uv tool install --python 3.12 omnivoice --index https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match
} else {
  uv tool install --python 3.12 omnivoice
}
$Py = Join-Path (uv tool dir) "omnivoice\Scripts\python.exe"
$Pyw = Join-Path (uv tool dir) "omnivoice\Scripts\pythonw.exe"

Say "Downloading Voice Studio"
New-Item -ItemType Directory -Force -Path $App | Out-Null
irm "$Repo/server.py" -OutFile (Join-Path $App "server.py")
irm "$Repo/index.html" -OutFile (Join-Path $App "index.html")

Say "Downloading the voice model (about 4.3 GB, the slow part)"
$env:HF_HUB_OFFLINE = "0"
& $Py -c "from huggingface_hub import snapshot_download as d; d('k2-fsa/OmniVoice'); d('openai/whisper-small', allow_patterns=['*.json', '*.txt', 'model.safetensors'])"

Say "Starting Voice Studio in the background, now and at every login"
$Startup = [Environment]::GetFolderPath("Startup")
$Link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $Startup "Voice Studio.lnk"))
$Link.TargetPath = $Pyw
$Link.Arguments = "`"$(Join-Path $App 'server.py')`""
$Link.Save()
Start-Process $Pyw -ArgumentList "`"$(Join-Path $App 'server.py')`"" -WindowStyle Hidden

Say "Done. Opening $Site"
Start-Sleep 3
Start-Process $Site
