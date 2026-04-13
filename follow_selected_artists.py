"""Подписка на артистов из selected_potential_artists.json через Spotify API."""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

import requests
import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth
from spotify_auth import get_spotify_settings, require_spotify_settings


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = SCRIPT_DIR
sys.path.insert(0, PROJECT_DIR)

from shared.utils import normalize_text, split_artists, transliterate  # noqa: E402


_orig_session_init = requests.Session.__init__


def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False


requests.Session.__init__ = _patched_session_init


SETTINGS = get_spotify_settings(SCRIPT_DIR)
DEFAULT_CACHE_PATH = SETTINGS["cache_path"]


def parse_args():
    parser = argparse.ArgumentParser(description="Follow selected Spotify artists from dashboard selection")
    parser.add_argument(
        "--selected",
        default=os.path.join(PROJECT_DIR, "selected_potential_artists.json"),
        help="JSON со списком выбранных артистов",
    )
    parser.add_argument(
        "--report",
        default=os.path.join(SCRIPT_DIR, "spotify_library_audit_report.json"),
        help="Audit report JSON",
    )
    parser.add_argument(
        "--resolution-report",
        default=os.path.join(SCRIPT_DIR, "selected_artists_follow_resolution.json"),
        help="Куда сохранить отчёт по резолву и follow",
    )
    parser.add_argument("--cache-path", default=DEFAULT_CACHE_PATH, help="Путь к spotipy cache")
    parser.add_argument(
        "--use-track-lookup",
        action="store_true",
        help="Дополнительно резолвить артистов через track IDs. Без флага этот шаг пропускается.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Реально выполнить follow. Без флага будет только dry-run.",
    )
    return parser.parse_args()


def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


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


def name_forms(name):
    norm = normalize_text(name)
    tr = normalize_text(transliterate(name))
    return {v for v in {norm, tr, norm.replace(" ", ""), tr.replace(" ", "")} if v}


def clean_artist_name(name):
    value = str(name or "").strip()
    value = value.replace("►", " ").replace("▸", " ").replace("▶", " ").replace("•", " ")
    value = value.replace("в–є", " ").replace("в—Џ", " ")
    value = re.sub(r"^[\s\.\-–—:;,_~`'\"«»•►▸▶\|\\/]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_selected_items(selected_payload, selected_path):
    selected = selected_payload.get("selected", [])
    changed = False
    normalized = []

    for item in selected:
        row = dict(item)
        original = row.get("artist", "")
        cleaned = clean_artist_name(original)
        if cleaned and cleaned != original:
            row["artist_original"] = original
            row["artist"] = cleaned
            changed = True
        normalized.append(row)

    if changed:
        selected_payload = dict(selected_payload)
        selected_payload["selected"] = normalized
        selected_payload["selected_count"] = len(normalized)
        selected_payload["normalized_at"] = datetime.now().isoformat()
        save_json(selected_path, selected_payload)

    return normalized, changed


def artist_name_matches(expected_name, candidate_name):
    expected_forms = name_forms(expected_name)
    candidate_forms = name_forms(candidate_name)
    if expected_forms & candidate_forms:
        return True

    for exp in split_artists(expected_name):
        if name_forms(exp) & candidate_forms:
            return True

    for cand in split_artists(candidate_name):
        if expected_forms & name_forms(cand):
            return True

    return False


def collect_track_ids_by_artist(report):
    result = defaultdict(list)
    for item in report.get("found", []):
        artist = clean_artist_name(item.get("artist"))
        track_id = item.get("spotify_id")
        if artist and track_id:
            result[artist].append(track_id)
    return result


def collect_spotify_artist_names_by_artist(report):
    result = defaultdict(list)
    for item in report.get("found", []):
        artist = clean_artist_name(item.get("artist"))
        spotify_artist = item.get("spotify_artist")
        if artist and spotify_artist:
            result[artist].append(spotify_artist)
    return result


def fetch_tracks(sp, ids):
    tracks = []
    ids = [track_id for track_id in ids if track_id]
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        try:
            resp = sp.tracks(batch)
            tracks.extend(resp.get("tracks") or [])
        except SpotifyException as exc:
            if exc.http_status != 403:
                raise
            for track_id in batch:
                try:
                    resp = sp.tracks([track_id])
                    tracks.extend(resp.get("tracks") or [])
                except SpotifyException as inner_exc:
                    if inner_exc.http_status != 403:
                        raise
                except Exception:
                    continue
        except Exception:
            continue
    return tracks


def resolve_from_report_artist_names(sp, artist_name, spotify_artist_names):
    seen = set()
    for candidate_group in spotify_artist_names:
        for candidate_name in [part.strip() for part in candidate_group.split(",") if part.strip()]:
            if candidate_name in seen:
                continue
            seen.add(candidate_name)
            resolution = resolve_via_search(sp, candidate_name)
            if resolution and artist_name_matches(artist_name, resolution.get("spotify_artist_name", "")):
                resolution["method"] = "from_report_artist_names"
                return resolution
    return None


def resolve_from_tracks(sp, artist_name, track_ids):
    tracks = fetch_tracks(sp, track_ids)
    counts = Counter()
    names = {}

    for track in tracks:
        for artist in track.get("artists") or []:
            cand_name = artist.get("name")
            cand_id = artist.get("id")
            if cand_name and cand_id and artist_name_matches(artist_name, cand_name):
                counts[cand_id] += 1
                names[cand_id] = cand_name

    if not counts:
        return None

    best_id, hit_count = counts.most_common(1)[0]
    return {
        "spotify_artist_id": best_id,
        "spotify_artist_name": names[best_id],
        "method": "from_found_tracks",
        "confidence_hits": hit_count,
    }


def resolve_via_search(sp, artist_name):
    search = sp.search(q=f"artist:{artist_name}", type="artist", limit=10)
    items = (((search or {}).get("artists") or {}).get("items")) or []
    for item in items:
        if artist_name_matches(artist_name, item.get("name", "")):
            return {
                "spotify_artist_id": item.get("id"),
                "spotify_artist_name": item.get("name"),
                "method": "search",
                "confidence_hits": None,
            }
    return None


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
    selected_payload = load_json(args.selected)
    report = load_json(args.report)
    selected, normalized_changed = normalize_selected_items(selected_payload, args.selected)

    if not selected:
        print("В selected_potential_artists.json нет выбранных артистов.")
        return

    sp = build_spotify_client(args.cache_path)
    me = sp.current_user()
    print(f"Авторизован как: {me.get('display_name') or me['id']} ({me['id']})")
    if normalized_changed:
        print(f"Список нормализован и перезаписан: {args.selected}")

    track_ids_by_artist = collect_track_ids_by_artist(report)
    spotify_artist_names_by_artist = collect_spotify_artist_names_by_artist(report)
    resolution_rows = []
    resolved_ids = []
    total = len(selected)

    for index, item in enumerate(selected, start=1):
        artist_name = clean_artist_name(item.get("artist"))
        if not artist_name:
            continue

        print(f"[{index}/{total}] {artist_name}")

        resolution = resolve_from_report_artist_names(
            sp,
            artist_name,
            spotify_artist_names_by_artist.get(artist_name, []),
        )
        if not resolution:
            resolution = resolve_via_search(sp, artist_name)
        if not resolution and args.use_track_lookup:
            try:
                resolution = resolve_from_tracks(sp, artist_name, track_ids_by_artist.get(artist_name, []))
            except Exception:
                resolution = None

        row = {
            "artist": artist_name,
            "artist_cleaned": clean_artist_name(artist_name),
            "added_track_count": item.get("added_track_count"),
            "discography_track_count": item.get("discography_track_count"),
            "missing_track_count": item.get("missing_track_count"),
            "resolved": bool(resolution),
            **(resolution or {}),
        }
        resolution_rows.append(row)
        if resolution and resolution.get("spotify_artist_id"):
            resolved_ids.append(resolution["spotify_artist_id"])
            print(f"  -> resolved: {resolution.get('spotify_artist_name')} [{resolution.get('method')}]")
        else:
            print("  -> not resolved")

    unique_ids = list(dict.fromkeys(resolved_ids))
    followed_count = 0

    if args.execute and unique_ids:
        for i in range(0, len(unique_ids), 50):
            batch = unique_ids[i:i + 50]
            follow_artist_ids(sp, batch)
            followed_count += len(batch)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "spotify_user": {
            "id": me["id"],
            "display_name": me.get("display_name"),
        },
        "execute": args.execute,
        "normalized_selected_file": normalized_changed,
        "selected_count": len(selected),
        "resolved_count": len(unique_ids),
        "followed_count": followed_count,
        "artists": resolution_rows,
    }

    save_json(args.resolution_report, payload)

    print(f"Selected: {len(selected)}")
    print(f"Resolved artist IDs: {len(unique_ids)}")
    if args.execute:
        print(f"Followed via API: {followed_count}")
    else:
        print("Dry-run only. Добавь --execute чтобы реально подписаться.")
    print(f"Resolution report: {args.resolution_report}")


if __name__ == "__main__":
    main()
