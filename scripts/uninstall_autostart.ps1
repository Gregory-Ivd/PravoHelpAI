# Видаляє задачу автозапуску бота.

$taskName = "PravoHelpAI Bot"

try {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[OK] Задачу '$taskName' видалено." -ForegroundColor Green
} catch {
    Write-Host "[!] Задачі '$taskName' не існує або не вдалося видалити: $_" -ForegroundColor Yellow
}
