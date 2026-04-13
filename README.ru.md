# yaMusicToSpotify

Набор инструментов под Windows для переноса большой музыкальной библиотеки в Spotify, проверки результата миграции и просмотра итоговых данных в локальном дашборде.

Язык:
- [English](./README.md)
- [Русская версия](./README.ru.md)

Репозиторий объединяет два практических сценария:

- браузерную автоматизацию для добавления треков через веб-интерфейс Spotify на базе Playwright
- утилиты для Spotify Web API: экспорт лайков, аудит покрытия библиотеки и массовая подписка на артистов

Проект вырос из реальной миграции из VK, YouTube Music и Яндекс Музыки, но публичная версия подготовлена для безопасного использования:

- креды Spotify берутся из переменных окружения или локального `.env`
- личные выгрузки игнорируются Git
- в папке `site/` лежит безопасный демо-снимок дашборда

## Что делает проект

### 1. Импорт через браузер

Скрипты из [`browser_import/`](./browser_import) автоматизируют поиск треков в Spotify и нажатие кнопки "Like" в веб-плеере. Это полезно, когда API-миграции недостаточно или когда нужен управляемый импорт именно через интерфейс.

Основной вариант:

- [`browser_import/main_optimized.py`](./browser_import/main_optimized.py)

Резервный вариант:

- [`browser_import/main.py`](./browser_import/main.py)

### 2. Аудит библиотеки Spotify

Утилиты через API выгружают текущие лайкнутые треки, список отслеживаемых артистов, сравнивают это с исходной дискографией и собирают отчёт:

- [`export_spotify_liked.py`](./export_spotify_liked.py)
- [`compare_spotify_likes.py`](./compare_spotify_likes.py)
- [`spotify_library_audit.py`](./spotify_library_audit.py)
- [`refresh_dashboard_data.py`](./refresh_dashboard_data.py)

### 3. Работа с артистами

Дашборд умеет сохранять shortlist артистов, которых хочется добавить в подписки. Затем CLI-утилиты резолвят artist ID и подписываются на них через Spotify API:

- [`follow_selected_artists.py`](./follow_selected_artists.py)
- [`follow_resolved_artists.py`](./follow_resolved_artists.py)

### 4. Локальный дашборд

В комплекте идёт локальный дашборд для просмотра:

- покрытия миграции
- разбивки по источникам
- отслеживаемых артистов
- приоритетных пробелов
- артистов-кандидатов для подписки
- ненайденных треков

Файлы:

- [`dashboard/`](./dashboard)
- [`dashboard_server.py`](./dashboard_server.py)

Папка [`site/`](./site) содержит GitHub-safe демо-снимок, поэтому интерфейс можно показать публично даже без личных JSON.

## Структура репозитория

```text
.
|-- browser_import/                  # Автоматизация Spotify Web на Playwright
|-- dashboard/                       # Основной фронтенд дашборда
|-- shared/                          # Нормализация текста и fuzzy matching
|-- site/                            # Публичный демо-снимок дашборда
|-- MY FULL DISCOGRAPHY (liked tracks).example.json
|-- export_spotify_liked.py
|-- compare_spotify_likes.py
|-- spotify_library_audit.py
|-- refresh_dashboard_data.py
|-- follow_selected_artists.py
|-- follow_resolved_artists.py
|-- spotify_auth.py                  # Общий загрузчик конфигурации Spotify
`-- requirements.txt
```

## Требования

- Windows PowerShell
- Python 3.11+
- приложение Spotify, созданное в [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- для браузерного сценария желательно иметь Spotify Premium

## Установка

### 1. Клонируй репозиторий

```powershell
git clone <your-repo-url>
cd yaMusicToSpotify
```

### 2. Подготовь локальное окружение

```powershell
.\setup_release.ps1
```

Скрипт:

- создаст локальную `.venv`, если её ещё нет
- установит Python-зависимости
- установит Playwright Chromium

### 3. Настрой Spotify credentials

Скопируй [`.env.example`](./.env.example) в `.env` и подставь свои значения:

```powershell
Copy-Item .env.example .env
```

Нужные переменные:

```env
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_USERNAME=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_CACHE_PATH=.cache-spotify
```

Важно, чтобы тот же `redirect URI` был разрешён в настройках Spotify-приложения.

## Типовые сценарии

### Запуск дашборда

```powershell
.\run_dashboard.ps1
```

Если локальных audit-файлов ещё нет, основной дашборд автоматически переключится на демо-снимок из [`site/`](./site).

### Обновление локальных данных Spotify

```powershell
.\run_refresh_data.ps1
```

Будут обновлены локальные приватные файлы вроде:

- `ACTUAL SPOTIFY LIKES.json`
- `ACTUAL SPOTIFY ARTISTS.json`
- `spotify_library_audit_report.json`

По умолчанию публичный демо-снимок в `site/` не перезаписывается.

Если захочешь обновить и его локально, используй:

```powershell
.\run_refresh_data.ps1 -SyncSiteSnapshot
```

### Первый вход для браузерного импорта

```powershell
.\run_browser_import.ps1 -Login
```

### Основной оптимизированный импорт через браузер

```powershell
.\run_browser_import.ps1
```

### Резервный браузерный импорт

```powershell
.\run_browser_import.ps1 -Main
```

### Ручной экспорт Spotify likes

```powershell
.\.venv\Scripts\python.exe .\export_spotify_liked.py
```

### Ручной полный аудит

```powershell
.\.venv\Scripts\python.exe .\spotify_library_audit.py
```

## Что происходит с данными

В репозитории специально проведена чёткая граница между кодом и личными выгрузками.

Игнорируется Git:

- живые Spotify-выгрузки
- сгенерированные audit-отчёты
- файлы browser session и cache
- локальные progress-файлы
- артефакты выбора артистов и follow-resolution

Коммитится в Git:

- код
- документация
- демо-снимок дашборда в [`site/`](./site)

## Исходный файл дискографии

В репозиторий включён маленький пример исходного файла:

- [`MY FULL DISCOGRAPHY (liked tracks).example.json`](./MY%20FULL%20DISCOGRAPHY%20%28liked%20tracks%29.example.json)

Для реального использования положи свой датасет сюда:

- `MY FULL DISCOGRAPHY (liked tracks).json`

Этот живой файл специально игнорируется Git.

## Почему здесь два дашборда

- [`dashboard/`](./dashboard) — основной рабочий дашборд, который в первую очередь берёт локальные live-данные
- [`site/`](./site) — безопасный publishable demo snapshot, который можно коммитить на GitHub

## Лицензия

MIT. См. [`LICENSE`](./LICENSE).
