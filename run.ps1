param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& $Python -m pip install -r requirements.txt
& $Python main.py
