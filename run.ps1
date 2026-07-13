# One-command local run for preCaution (Windows / PowerShell).
#
# Creates a virtual environment, installs dependencies, makes sure a .env
# exists, then launches the web app. Safe to run repeatedly: it only does the
# setup work that is actually missing, and works offline once set up.
#
#   .\run.ps1
#
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. Find a Python interpreter (3.10+ works; 3.13 is what this was built on).
$py = $null
foreach ($cand in @("python", "python3", "py")) {
  if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) {
  Write-Error "No Python interpreter found. Install Python 3.10+ (3.13 recommended) from https://www.python.org/downloads/"
  exit 1
}

# 2. Create the virtual environment on first run.
if (-not (Test-Path ".venv")) {
  Write-Host "Creating virtual environment (.venv)..."
  & $py -m venv .venv
}
$venvPy = ".venv\Scripts\python.exe"

# 3. Install dependencies only when they are missing (first run, or a fresh
#    machine). Skipping this on later runs keeps startup fast and offline-safe.
& $venvPy -c "import uvicorn, fastapi, anthropic" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing dependencies..."
  & $venvPy -m pip install -r requirements.txt
}

# 4. Make sure a .env exists so the API key can be read.
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example."
}

# 5. Warn (do not block) if the key is still blank. The app boots either way;
#    only reading a protocol needs the key.
$hasKey = Select-String -Path ".env" -Pattern '^ANTHROPIC_API_KEY=.+' -Quiet
if (-not $hasKey) {
  Write-Host ""
  Write-Host "  WARNING: ANTHROPIC_API_KEY is not set in .env." -ForegroundColor Yellow
  Write-Host "  The app will start, but reading a protocol needs a key."
  Write-Host "  Get one at https://console.anthropic.com/ and add it to .env."
  Write-Host ""
}

# 6. Launch.
Write-Host ""
Write-Host "  preCaution is starting at  http://127.0.0.1:8000"
Write-Host "  Press Ctrl+C to stop."
Write-Host ""
& $venvPy -m uvicorn app.main:app --host 127.0.0.1 --port 8000
