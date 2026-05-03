# Реєструє задачу в Планувальнику Windows: бот стартує при вході користувача в систему.
# Запускати з PowerShell — admin-права не обовʼязкові, бо задача в контексті поточного юзера.

$ErrorActionPreference = "Stop"

$taskName = "PravoHelpAI Bot"
$root = "C:\Users\Gregory Ivd\Desktop\PravoHelpAI"
$script = Join-Path $root "scripts\run_bot.ps1"

if (-not (Test-Path $script)) {
    throw "Не знайдено $script"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ('-ExecutionPolicy Bypass -WindowStyle Hidden -NoProfile -File "{0}"' -f $script)

# 1) При вході в систему — стартує одразу.
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# 2) Heartbeat кожні 5 хв — піднімає бот, якщо помер після sleep/hibernate/краша.
#    Завдяки -MultipleInstances IgnoreNew повторні запуски пропускаються, поки процес живий.
$triggerHeartbeat = New-ScheduledTaskTrigger `
    -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($triggerLogon, $triggerHeartbeat) `
    -Settings $settings `
    -Principal $principal `
    -Description "PravoHelpAI Telegram bot — автозапуск при вході в Windows + heartbeat кожні 5 хв" `
    -Force | Out-Null

Write-Host ""
Write-Host "[OK] Задачу '$taskName' зареєстровано." -ForegroundColor Green
Write-Host "    - Бот стартуватиме при кожному вході в Windows."
Write-Host "    - Логи: $root\data\bot.log"
Write-Host "    - Перегляд/керування: taskschd.msc → 'PravoHelpAI Bot'"
Write-Host ""
Write-Host "Запустити прямо зараз? (бот запуститься у фоні)"
$choice = Read-Host "[Y/n]"
if ($choice -ne 'n' -and $choice -ne 'N') {
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2
    Write-Host "[OK] Бот запущено." -ForegroundColor Green
}
