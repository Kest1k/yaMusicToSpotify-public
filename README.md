# yaMusicToSpotify

Windows-first toolkit for moving a large music library into Spotify, auditing the migration result, and exploring the final dataset in a local dashboard.

Language:
- [English](./README.md)
- [Русская версия](./README.ru.md)

This repository combines two practical workflows:

- browser automation for adding tracks through the Spotify web interface with Playwright
- Spotify Web API utilities for exporting likes, auditing coverage, and following artists in bulk

It was built around a real migration from VK, YouTube Music, and Yandex Music, but the repository itself is prepared for public use:

- Spotify credentials are loaded from environment variables or a local `.env`
- personal exports are ignored by Git
- the `site/` folder contains a safe demo snapshot for the dashboard

## What the project does

### 1. Browser-based import

The scripts in [`browser_import/`](./browser_import) automate Spotify search and "Like" actions in the web player. This is useful when API-based migration is not enough or when you want to preserve a manual, UI-level import flow.

The optimized variant is:

- [`browser_import/main_optimized.py`](./browser_import/main_optimized.py)

There is also a fallback version:

- [`browser_import/main.py`](./browser_import/main.py)

### 2. Spotify library audit

The API utilities export your current Spotify likes, fetch followed artists, compare them against a source discography, and build an audit report:

- [`export_spotify_liked.py`](./export_spotify_liked.py)
- [`compare_spotify_likes.py`](./compare_spotify_likes.py)
- [`spotify_library_audit.py`](./spotify_library_audit.py)
- [`refresh_dashboard_data.py`](./refresh_dashboard_data.py)

### 3. Artist follow workflow

The dashboard can store a shortlist of artists you want to follow. Then the CLI utilities resolve artist IDs and follow them through the Spotify API:

- [`follow_selected_artists.py`](./follow_selected_artists.py)
- [`follow_resolved_artists.py`](./follow_resolved_artists.py)

### 4. Local dashboard

The project ships with a local dashboard for exploring:

- coverage of the migration
- source breakdown
- followed artists
- priority gaps
- candidate artists to follow
- missing tracks

Files:

- [`dashboard/`](./dashboard)
- [`dashboard_server.py`](./dashboard_server.py)

The [`site/`](./site) directory contains a GitHub-safe demo snapshot so the UI still works even without personal data files.

## Repository layout

```text
.
|-- browser_import/                  # Playwright-based Spotify web automation
|-- dashboard/                       # Main dashboard frontend
|-- shared/                          # Text normalization and fuzzy matching helpers
|-- site/                            # Public demo snapshot of the dashboard
|-- MY FULL DISCOGRAPHY (liked tracks).example.json
|-- export_spotify_liked.py
|-- compare_spotify_likes.py
|-- spotify_library_audit.py
|-- refresh_dashboard_data.py
|-- follow_selected_artists.py
|-- follow_resolved_artists.py
|-- spotify_auth.py                  # Shared environment/config loader for Spotify auth
`-- requirements.txt
```

## Requirements

- Windows PowerShell
- Python 3.11+
- A Spotify application created in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- A Spotify Premium account is recommended for the browser-based workflow

## Setup

### 1. Clone the repository

```powershell
git clone <your-repo-url>
cd yaMusicToSpotify
```

### 2. Create local environment

```powershell
.\setup_release.ps1
```

This script:

- creates a local `.venv` if needed
- installs Python dependencies
- installs the Playwright Chromium browser

### 3. Configure Spotify credentials

Copy [`.env.example`](./.env.example) to `.env` and fill in your values:

```powershell
Copy-Item .env.example .env
```

Required variables:

```env
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_USERNAME=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_CACHE_PATH=.cache-spotify
```

Make sure the same redirect URI is allowed in your Spotify app settings.

## Typical workflows

### Launch the dashboard

```powershell
.\run_dashboard.ps1
```

If your local audit files do not exist yet, the root dashboard will automatically fall back to the demo snapshot from [`site/`](./site).

### Refresh local Spotify data

```powershell
.\run_refresh_data.ps1
```

This updates local private files such as:

- `ACTUAL SPOTIFY LIKES.json`
- `ACTUAL SPOTIFY ARTISTS.json`
- `spotify_library_audit_report.json`

By default it does **not** overwrite the public demo snapshot in `site/`.

If you explicitly want to refresh that snapshot locally, run:

```powershell
.\run_refresh_data.ps1 -SyncSiteSnapshot
```

### First browser login

```powershell
.\run_browser_import.ps1 -Login
```

### Main optimized browser import

```powershell
.\run_browser_import.ps1
```

### Fallback browser import

```powershell
.\run_browser_import.ps1 -Main
```

### Export Spotify liked songs manually

```powershell
.\.venv\Scripts\python.exe .\export_spotify_liked.py
```

### Run the full audit manually

```powershell
.\.venv\Scripts\python.exe .\spotify_library_audit.py
```

## Notes about data files

The repository intentionally keeps a strict line between code and personal exports.

Ignored by Git:

- live Spotify export files
- generated audit reports
- browser session and cache files
- local progress files
- selection and follow-resolution artifacts

Tracked in Git:

- code
- documentation
- demo dashboard snapshot in [`site/`](./site)

## Source discography file

The repository ships with a tiny example source file:

- [`MY FULL DISCOGRAPHY (liked tracks).example.json`](./MY%20FULL%20DISCOGRAPHY%20%28liked%20tracks%29.example.json)

For real use, place your own source dataset at:

- `MY FULL DISCOGRAPHY (liked tracks).json`

That live file is ignored by Git on purpose.

## Why there are two dashboards

- [`dashboard/`](./dashboard) is the main working dashboard that prefers local live data
- [`site/`](./site) is a safe, publishable demo snapshot that can be committed to GitHub

## License

MIT. See [`LICENSE`](./LICENSE).
