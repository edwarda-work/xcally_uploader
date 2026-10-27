$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($env:OS -ne "Windows_NT") { throw "Build this executable on Windows." }
py -3.13 -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.13 (64-bit) with the Python launcher first." }
$python = Join-Path $PSScriptRoot ".venv-build\Scripts\python.exe"
& $python -m pip install -r requirements.txt "pyinstaller==6.22.0"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $python -m PyInstaller --clean --noconfirm packaging/windows.spec
if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }
Write-Host "Ready: dist\XcallyUploads.exe"
