$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $pythonPath -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ['streamlit', 'pandas', 'mysql', 'pymongo', 'dotenv']) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $pythonPath -m streamlit run app.py --server.address 127.0.0.1
