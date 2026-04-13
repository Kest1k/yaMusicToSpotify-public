"""Экспорт всех лайкнутых треков Spotify через Web API."""

import argparse
import json
import os
import sys
from datetime import datetime

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotify_auth import get_spotify_settings, require_spotify_settings


_orig_session_init = requests.Session.__init__


def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False


requests.Session.__init__ = _patched_session_init


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS = get_spotify_settings(SCRIPT_DIR)
DEFAULT_CACHE_PATH = SETTINGS["cache_path"]


def parse_args():
    parser = argparse.ArgumentParser(description="Экспорт Spotify Liked Songs в JSON")
    parser.add_argument(
        "--output",
        default=None,
        help="Путь к итоговому JSON. По умолчанию создаётся файл рядом со скриптом.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Размер батча Spotify API. Обычно оставляем 50.",
    )
    parser.add_argument(
        "--cache-path",
        default=DEFAULT_CACHE_PATH,
        help="Путь к spotipy cache-файлу.",
    )
    return parser.parse_args()


def build_output_path(user_id, explicit_output=None):
    if explicit_output:
        return os.path.abspath(explicit_output)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"spotify_liked_export_{user_id}_{timestamp}.json"
    return os.path.join(SCRIPT_DIR, filename)


def build_spotify_client(cache_path):
    settings = require_spotify_settings(SCRIPT_DIR)
    auth = SpotifyOAuth(
        client_id=settings["client_id"],
        client_secret=settings["client_secret"],
        redirect_uri=settings["redirect_uri"],
        scope="user-library-read",
        username=settings["username"],
        open_browser=False,
        cache_path=cache_path,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=30)


def normalize_track(item, position):
    track = item.get("track") or {}
    album = track.get("album") or {}
    artists = track.get("artists") or []

    return {
        "position": position,
        "saved_at": item.get("added_at"),
        "spotify_id": track.get("id"),
        "name": track.get("name"),
        "artist": ", ".join(artist.get("name", "") for artist in artists if artist.get("name")),
        "artists": [artist.get("name") for artist in artists if artist.get("name")],
        "album": album.get("name"),
        "album_id": album.get("id"),
        "duration_ms": track.get("duration_ms"),
        "explicit": track.get("explicit"),
        "popularity": track.get("popularity"),
        "track_number": track.get("track_number"),
        "disc_number": track.get("disc_number"),
        "is_local": track.get("is_local"),
        "uri": track.get("uri"),
        "external_url": (track.get("external_urls") or {}).get("spotify"),
        "album_release_date": album.get("release_date"),
    }


def export_liked_tracks(sp, batch_limit):
    me = sp.current_user()
    user_id = me["id"]

    print(f"Авторизован как: {me.get('display_name') or user_id} ({user_id})")
    print("Выгружаю лайкнутые треки из Spotify...")

    offset = 0
    position = 1
    tracks = []
    total_expected = None

    while True:
        batch = sp.current_user_saved_tracks(limit=batch_limit, offset=offset)
        items = batch.get("items", [])

        if total_expected is None:
            total_expected = batch.get("total")
            if total_expected is not None:
                print(f"Spotify сообщает total: {total_expected}")

        if not items:
            break

        for item in items:
            tracks.append(normalize_track(item, position))
            position += 1

        offset += len(items)
        print(f"  Получено: {offset}")

        if not batch.get("next"):
            break

    return me, total_expected, tracks


def main():
    args = parse_args()

    try:
        sp = build_spotify_client(args.cache_path)
        me, total_expected, tracks = export_liked_tracks(sp, args.limit)
    except Exception as exc:
        print(f"Ошибка экспорта: {exc}", file=sys.stderr)
        print(
            "Если это первый запуск на этом ПК, авторизуйся через любой spotipy-скрипт проекта "
            "или удали устаревший cache-файл и запусти снова.",
            file=sys.stderr,
        )
        sys.exit(1)

    output_path = build_output_path(me["id"], args.output)
    payload = {
        "exported_at": datetime.now().isoformat(),
        "spotify_user": {
            "id": me["id"],
            "display_name": me.get("display_name"),
        },
        "total_reported_by_api": total_expected,
        "exported_count": len(tracks),
        "tracks": tracks,
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print()
    print(f"Готово. Экспортировано треков: {len(tracks)}")
    print(f"Файл: {output_path}")


if __name__ == "__main__":
    main()
