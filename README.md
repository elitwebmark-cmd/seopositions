# ⚡ Rank Tracker

Онлайн рангтрекер на базі **serper.dev**. FastAPI + SQLite + APScheduler + Chart.js.

## Можливості
- Мультипроєкти (сайт, домен, gl, hl, location, device)
- Адмінка: список запитів (bulk-додавання через textarea), налаштування, видалення
- Автозйом позицій по cron-розкладу + кнопка «▶ Зняти зараз»
- Дешборд: KPI (ТОП 3/10/20/50/100, середня позиція), графік динаміки, таблиця з фільтрами
- Порівняння двох зрізів A ↔ B (було / стало / Δ)
- Графік руху позицій по кожному запиту
- Експорт CSV
- Captcha-free: використовує офіційний Serper API

## Деплой на Railway

1. Створи новий GitHub-репозиторій і залий туди всі файли з цієї папки.
2. На [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. Після першого деплою додай **Volume** (Settings → Volumes → New Volume):
   - **Mount path:** `/data`
   - **Size:** 1 GB (достатньо)
4. Variables (Settings → Variables), додай:

   | Змінна | Значення |
   |---|---|
   | `SERPER_API_KEY` | твій ключ з serper.dev |
   | `ADMIN_PASSWORD` | придумай надійний |
   | `SECRET_KEY` | будь-який довгий рандомний рядок (`openssl rand -hex 32`) |
   | `TZ` | `Europe/Kiev` |

5. Railway авторедеплоїть → відкрий публічний URL → `/login` → введи ADMIN_PASSWORD.

## Локальний запуск

```bash
python -m venv .venv && source .venv/bin/activate  # на Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # відредагуй значення
export $(cat .env | xargs)  # або: $env:SERPER_API_KEY="..." в PowerShell
mkdir -p data && export DB_PATH=./data/ranktracker.db
uvicorn app:app --reload
```

Відкрий http://localhost:8000/login

## Як налаштувати перший проєкт

1. Залогінься → `/admin` → заповни форму нового проєкту:
   - **Домен:** `elit-web.ua` (без https://)
   - **gl:** `ua` · **hl:** `uk`
   - **Локація:** `Ukraine` (загалом) або конкретне місто у форматі Google Ads Geo, наприклад:
     - `Kyiv, Kyiv city, Ukraine`
     - `Lviv, Lviv Oblast, Ukraine`
     - `Odesa, Odesa Oblast, Ukraine`
     - `Dnipro, Dnipropetrovsk Oblast, Ukraine`
     - `Kharkiv, Kharkiv Oblast, Ukraine`
   - **Cron:** `0 6 * * *` — щодня о 06:00 (Europe/Kiev)
2. Увійди в проєкт → додай запити (кожен з нового рядка)
3. Натисни **▶ Зняти позиції зараз** для першого зрізу
4. Відкрий дешборд → вибирай зрізи A та B для порівняння

## Структура cron (приклади)

| Вираз | Коли |
|---|---|
| `0 6 * * *` | щодня о 06:00 |
| `0 6 * * 1` | щопонеділка о 06:00 |
| `0 */4 * * *` | кожні 4 години |
| `0 6 1,15 * *` | 1-го і 15-го числа о 06:00 |

## Витрати serper.dev

Один запит = 1 кредит. 100 запитів × 1 проєкт × 1 раз на день = 3000/міс. На starter-плані ($50/міс = 50k credits) вистачить з запасом.

## Увага
- Код не має 2FA — захист лише паролем ADMIN_PASSWORD. Обов'язково використовуй сильний пароль.
- SQLite живе у volume на Railway (`/data`). Бекап можна робити через Railway CLI.
