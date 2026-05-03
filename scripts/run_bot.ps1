# Запуск PravoHelpAI у тихому режимі з логуванням у файл.
# Викликається з Task Scheduler або вручну.

$ErrorActionPreference = "Stop"

$root = "C:\Users\Gregory Ivd\Desktop\PravoHelpAI"
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "data"
$log = Join-Path $logDir "bot.log"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

$env:PYTHONUNBUFFERED = "1"
Set-Location $root

"---- bot start: $(Get-Date -Format 'o') ----" | Out-File -FilePath $log -Append -Encoding utf8

& $python -u -m pravohelp.bot *>> $log
