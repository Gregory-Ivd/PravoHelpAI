# PravoHelpAI

Telegram-бот — генератор юридичних шаблонів для типових ситуацій в Україні.

> ⚠️ **Це генератор шаблонів, а не юридична консультація.** Перед поданням документів у держоргани або суд — рекомендуємо перевірку у фахового юриста.

## Поточний статус

🚧 **Фаза 1 — скелет проєкту.** Базовий бот з `/start` і дисклеймером працює. Сценарії ще не підключено.

## Документи проєкту

- [`ANALYSIS.md`](./ANALYSIS.md) — глибокий критичний аналіз: що, чому, які ризики.
- [`PLAN.md`](./PLAN.md) — пофазний план реалізації.
- [`DECISIONS.md`](./DECISIONS.md) — лог архітектурних рішень.
- [`MESSAGE_FOR_DMITRO.md`](./MESSAGE_FOR_DMITRO.md) — повідомлення для юриста-партнера.

## Сценарії

| # | Сценарій | Статус |
|---|---|---|
| 1 | Невиплата зарплати | 🚧 У розробці (Фаза 2) |
| 2 | Повістка / мобілізація | ⏳ Очікує шаблонів від юриста |
| 3 | Оскарження штрафу ПДР | 📅 Після MVP |

## Запуск локально

### 1. Встановлення

```bash
# Створити віртуальне середовище (рекомендовано)
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/Mac

# Встановити пакет з dev-залежностями
pip install -e ".[dev]"
```

### 2. Налаштування

```bash
# Скопіювати шаблон і заповнити токен
cp .env.example .env
# Відкрити .env у редакторі і вставити TELEGRAM_BOT_TOKEN від @BotFather
```

### 3. Запуск

```bash
python -m pravohelp.bot
```

### 4. Тести

```bash
pytest
```

## Структура

```
src/pravohelp/
├── bot.py              # entry point
├── config.py           # завантаження .env
├── handlers/           # telegram-обробники
├── storage/            # SQLAlchemy моделі і БД
├── document/           # генератор DOCX (Фаза 2)
└── utils/              # валідатори, утиліти

templates/              # DOCX-шаблони документів
data/pravohelp.db       # SQLite (створюється автоматично, в .gitignore)
tests/                  # pytest
```

## Стек

- Python 3.11+
- `python-telegram-bot[job-queue]>=21.6`
- `docxtpl` — генерація DOCX з шаблонів
- `SQLAlchemy 2` + SQLite
- `structlog` — JSON-логи
- `pytest` + `pytest-asyncio` — тести

## Автозапуск на Windows (локально)

Якщо хочеш, щоб бот стартував автоматично при вході в Windows і працював поки ноут увімкнений:

```powershell
# Один раз — реєструємо задачу
powershell -ExecutionPolicy Bypass -File "C:\Users\Gregory Ivd\Desktop\PravoHelpAI\scripts\install_autostart.ps1"
```

Що це робить:
- Створює задачу **PravoHelpAI Bot** у Планувальнику Windows.
- Тригер: при вході користувача в систему.
- Запускає бот у прихованому вікні, логи пише в `data\bot.log`.
- Auto-restart при збої (3 спроби з інтервалом 1 хв).
- Sleep / hibernate ноута → бот спить разом з ним; вмикає → бот сам стартує знову.

Керування після інсталяції:
```powershell
# Зупинити вручну
Stop-ScheduledTask -TaskName "PravoHelpAI Bot"

# Запустити вручну
Start-ScheduledTask -TaskName "PravoHelpAI Bot"

# Видалити автозапуск
powershell -ExecutionPolicy Bypass -File "scripts\uninstall_autostart.ps1"

# Подивитись лог у реальному часі
Get-Content data\bot.log -Wait -Tail 50
```

Або через GUI: **Win+R** → `taskschd.msc` → знайти **PravoHelpAI Bot**.

## Деплой через Docker

Готовий `Dockerfile` + `docker-compose.yml` дозволяють запустити бота одною командою на будь-якому Linux-сервері з Docker.

### Локальний запуск у контейнері

```bash
# Заповни .env (TELEGRAM_BOT_TOKEN, опціонально LAWYER_*, ADMIN_TELEGRAM_IDS)
docker compose up --build
```

База і згенеровані DOCX лежать у `./data/` на хост-системі — переживають перезапуск.

### Деплой на VPS

Передумова: на сервері встановлено Docker + Docker Compose plugin.

```bash
git clone https://github.com/Gregory-Ivd/PravoHelpAI.git
cd PravoHelpAI
cp .env.example .env
nano .env                              # вписати токен і інші змінні
docker compose up -d --build           # фоновий запуск
docker compose logs -f bot             # дивитись логи
```

### Оновлення після нового коміту

```bash
git pull
docker compose up -d --build
```

### Бекап БД

`./data/pravohelp.db` — звичайний SQLite-файл. Достатньо `cp data/pravohelp.db backups/`.
