$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "First run: creating the Python environment..."
    python3 -m venv (Join-Path $ProjectRoot ".venv")
    & $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
    & $Python -m playwright install chromium
}

$env:PYTHONIOENCODING = "utf-8"
Set-Location $ProjectRoot
Write-Host "PVP Reward: http://127.0.0.1:8765"
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8765

