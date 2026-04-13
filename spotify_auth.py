"""Helpers for loading Spotify credentials from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_CACHE_FILENAME = ".cache-spotify"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_local_env(base_dir: str | Path) -> None:
    base_path = Path(base_dir).resolve()
    for candidate in [base_path / ".env", base_path.parent / ".env"]:
        _load_env_file(candidate)


def get_spotify_settings(base_dir: str | Path) -> dict[str, str | None]:
    load_local_env(base_dir)
    base_path = Path(base_dir).resolve()
    cache_path = os.environ.get("SPOTIFY_CACHE_PATH") or str(base_path / DEFAULT_CACHE_FILENAME)

    return {
        "client_id": os.environ.get("SPOTIFY_CLIENT_ID"),
        "client_secret": os.environ.get("SPOTIFY_CLIENT_SECRET"),
        "username": os.environ.get("SPOTIFY_USERNAME"),
        "redirect_uri": os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        "cache_path": cache_path,
    }


def require_spotify_settings(base_dir: str | Path) -> dict[str, str | None]:
    settings = get_spotify_settings(base_dir)
    missing = [name for name in ("client_id", "client_secret") if not settings.get(name)]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Missing required Spotify credentials: "
            f"{joined}. Create a .env file from .env.example or set the environment variables manually."
        )
    return settings
