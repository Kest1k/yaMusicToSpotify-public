"""Быстрая подписка на уже зарезолвленные artist IDs из selected_artists_follow_resolution.json."""

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


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS = get_spotify_settings(SCRIPT_DIR)
DEFAULT_CACHE_PATH = SETTINGS["cache_path"]


_orig_session_init = requests.Session.__init__


def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False


requests.Session.__init__ = _patched_session_init


def parse_args():
    parser = argparse.ArgumentParser(description="Follow already resolved Spotify artist IDs")
    parser.add_argument(
        "--resolution-report",
        default=os.path.join(SCRIPT_DIR, "selected_artists_follow_resolution.json"),
        help="JSON с уже зарезолвленными artist IDs",
    )
    parser.add_argument(
        "--result",
        default=os.path.join(SCRIPT_DIR, "follow_resolved_result.json"),
        help="Куда сохранить итог follow-only запуска",
    )
    parser.add_argument("--cache-path", default=DEFAULT_CACHE_PATH, help="Путь к spotipy cache")
    return parser.parse_args()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def build_spotify_client(cache_path):
    settings = require_spotify_settings(SCRIPT_DIR)
    auth = SpotifyOAuth(
        client_id=settings["client_id"],
        client_secret=settings["client_secret"],
        redirect_uri=settings["redirect_uri"],
        scope="user-follow-modify",
        username=settings["username"],
        open_browser=False,
        cache_path=cache_path,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=30)


def get_access_token(sp):
    return sp.auth_manager.get_access_token(as_dict=False)


def save_artist_uris_to_library(access_token, uris):
    response = requests.put(
        "https://api.spotify.com/v1/me/library",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        params={"uris": ",".join(uris)},
        timeout=30,
    )
    return response


def follow_artist_ids(sp, artist_ids):
    if not artist_ids:
        return
    artist_uris = [f"spotify:artist:{artist_id}" for artist_id in artist_ids]
    access_token = get_access_token(sp)

    def _send(uris):
        response = save_artist_uris_to_library(access_token, uris)
        if response.ok:
            return

        if response.status_code == 400 and "Too many uris requested" in response.text and len(uris) > 1:
            midpoint = max(1, len(uris) // 2)
            _send(uris[:midpoint])
            _send(uris[midpoint:])
            return

        raise RuntimeError(
            f"Spotify save-to-library failed: {response.status_code} {response.text}"
        )

    _send(artist_uris)


def main():
    args = parse_args()
    report = load_json(args.resolution_report)
    resolved = [item for item in report.get("artists", []) if item.get("spotify_artist_id")]
    unique_ids = list(dict.fromkeys(item["spotify_artist_id"] for item in resolved))

    if not unique_ids:
        print("В resolution report нет зарезолвленных artist IDs.")
        return

    sp = build_spotify_client(args.cache_path)
    me = sp.current_user()
    print(f"Авторизован как: {me.get('display_name') or me['id']} ({me['id']})")

    followed_count = 0
    total = len(unique_ids)
    for start in range(0, total, 50):
        batch = unique_ids[start:start + 50]
        follow_artist_ids(sp, batch)
        followed_count += len(batch)
        print(f"Followed batch: {followed_count}/{total}")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "spotify_user": {
            "id": me["id"],
            "display_name": me.get("display_name"),
        },
        "source_resolution_report": args.resolution_report,
        "resolved_count": len(unique_ids),
        "followed_count": followed_count,
        "artist_ids": unique_ids,
    }
    save_json(args.result, payload)

    print(f"Done. Followed via API: {followed_count}")
    print(f"Result file: {args.result}")


if __name__ == "__main__":
    main()
