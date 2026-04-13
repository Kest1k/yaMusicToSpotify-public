"""Единый аудит Spotify: лайки, исполнители, сравнение с дискографией, potential artists."""

import argparse
import json
import os
import re
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
PROJECT_DIR = SCRIPT_DIR
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from shared.utils import normalize_text, split_artists, transliterate  # noqa: E402
from compare_spotify_likes import (  # noqa: E402
    build_actual_tracks,
    build_expected_record,
    find_match,
    load_json,
    safe_int_percent,
)


_orig_session_init = requests.Session.__init__


def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False


requests.Session.__init__ = _patched_session_init


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


SETTINGS = get_spotify_settings(SCRIPT_DIR)
DEFAULT_CACHE_PATH = SETTINGS["cache_path"]

DEFAULT_DISCOGRAPHY_FILE = os.path.join(SCRIPT_DIR, "MY FULL DISCOGRAPHY (liked tracks).json")
DEFAULT_LIKES_OUTPUT = os.path.join(SCRIPT_DIR, "ACTUAL SPOTIFY LIKES.json")
DEFAULT_ARTISTS_OUTPUT = os.path.join(SCRIPT_DIR, "ACTUAL SPOTIFY ARTISTS.json")
DEFAULT_REPORT_OUTPUT = os.path.join(SCRIPT_DIR, "spotify_library_audit_report.json")
DEFAULT_NOT_FOUND_OUTPUT = os.path.join(SCRIPT_DIR, "spotify_likes_not_found.json")
DEFAULT_ARTIST_STATS_OUTPUT = os.path.join(SCRIPT_DIR, "discography_artist_stats.json")
DEFAULT_POTENTIAL_OUTPUT = os.path.join(SCRIPT_DIR, "potential_artists_for_add.json")

ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,|&|\band\b|\bи\b|\bvs\.?\b|\bfeat\.?\b|\bft\.?\b|\bфит\b)\s*",
    flags=re.IGNORECASE,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Spotify audit: export likes + followed artists + compare with full discography"
    )
    parser.add_argument("--discography", default=DEFAULT_DISCOGRAPHY_FILE, help="Путь к FULL DISCOGRAPHY JSON")
    parser.add_argument(
        "--likes-input",
        default=DEFAULT_LIKES_OUTPUT,
        help="Какой JSON с лайками использовать для сравнения, если лайки не нужно обновлять",
    )
    parser.add_argument("--likes-output", default=DEFAULT_LIKES_OUTPUT, help="Куда сохранить свежий экспорт liked songs")
    parser.add_argument("--artists-output", default=DEFAULT_ARTISTS_OUTPUT, help="Куда сохранить followed artists")
    parser.add_argument("--report-output", default=DEFAULT_REPORT_OUTPUT, help="Куда сохранить полный отчёт")
    parser.add_argument("--not-found-output", default=DEFAULT_NOT_FOUND_OUTPUT, help="Куда сохранить not_found")
    parser.add_argument(
        "--artist-stats-output",
        default=DEFAULT_ARTIST_STATS_OUTPUT,
        help="Куда сохранить статистику по артистам дискографии",
    )
    parser.add_argument(
        "--potential-output",
        default=DEFAULT_POTENTIAL_OUTPUT,
        help="Куда сохранить potential artists for add",
    )
    parser.add_argument(
        "--artist-threshold",
        type=int,
        default=3,
        help="Порог added tracks. В potential попадают артисты со значением строго больше порога.",
    )
    parser.add_argument(
        "--skip-likes-export",
        action="store_true",
        help="Не обновлять лайки через API, а использовать уже существующий likes-input JSON",
    )
    parser.add_argument("--limit", type=int, default=50, help="Размер батча Spotify API")
    parser.add_argument("--sample", type=int, default=15, help="Сколько примеров показать в консоли")
    parser.add_argument("--cache-path", default=DEFAULT_CACHE_PATH, help="Путь к spotipy cache-файлу")
    return parser.parse_args()


def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def build_spotify_client(cache_path):
    settings = require_spotify_settings(SCRIPT_DIR)
    auth = SpotifyOAuth(
        client_id=settings["client_id"],
        client_secret=settings["client_secret"],
        redirect_uri=settings["redirect_uri"],
        scope="user-library-read user-follow-read",
        username=settings["username"],
        open_browser=False,
        cache_path=cache_path,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=30)


def normalize_liked_track(item, position):
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


def normalize_followed_artist(item, position):
    followers = item.get("followers") or {}
    return {
        "position": position,
        "spotify_id": item.get("id"),
        "name": item.get("name"),
        "genres": item.get("genres") or [],
        "popularity": item.get("popularity"),
        "followers_total": followers.get("total"),
        "uri": item.get("uri"),
        "external_url": (item.get("external_urls") or {}).get("spotify"),
    }


def export_liked_tracks(sp, batch_limit):
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
                print(f"Spotify сообщает total liked tracks: {total_expected}")

        if not items:
            break

        for item in items:
            tracks.append(normalize_liked_track(item, position))
            position += 1

        offset += len(items)
        print(f"  liked tracks получено: {offset}")

        if not batch.get("next"):
            break

    return total_expected, tracks


def export_followed_artists(sp, batch_limit):
    print("Выгружаю followed artists из Spotify...")
    after = None
    position = 1
    artists = []

    while True:
        batch = sp.current_user_followed_artists(limit=batch_limit, after=after)
        artists_block = (batch.get("artists") or {})
        items = artists_block.get("items") or []

        if not items:
            break

        for item in items:
            artists.append(normalize_followed_artist(item, position))
            position += 1

        after = items[-1].get("id")
        print(f"  followed artists получено: {len(artists)}")

        if not artists_block.get("next"):
            break

    return artists


def split_artist_names_preserve_case(artist_str):
    parts = [part.strip() for part in ARTIST_SPLIT_RE.split(artist_str) if part.strip()]
    return parts or [artist_str.strip()]


def build_followed_lookup(followed_artists):
    records = []
    by_norm = {}
    by_translit = {}
    by_nospace = {}
    by_translit_nospace = {}

    for artist in followed_artists:
        name = artist.get("name") or ""
        name_norm = normalize_text(name)
        name_tr = normalize_text(transliterate(name))
        name_ns = name_norm.replace(" ", "")
        name_tr_ns = name_tr.replace(" ", "")

        record = {
            "name": name,
            "spotify_id": artist.get("spotify_id"),
            "normalized": name_norm,
            "transliterated": name_tr,
            "nospace": name_ns,
            "transliterated_nospace": name_tr_ns,
        }
        records.append(record)

        if name_norm:
            by_norm[name_norm] = record
        if name_tr:
            by_translit[name_tr] = record
        if name_ns:
            by_nospace[name_ns] = record
        if name_tr_ns:
            by_translit_nospace[name_tr_ns] = record

    return {
        "records": records,
        "by_norm": by_norm,
        "by_translit": by_translit,
        "by_nospace": by_nospace,
        "by_translit_nospace": by_translit_nospace,
    }


def find_followed_artist_match(name, lookup):
    name_norm = normalize_text(name)
    name_tr = normalize_text(transliterate(name))
    name_ns = name_norm.replace(" ", "")
    name_tr_ns = name_tr.replace(" ", "")

    for key, mapping in [
        (name_norm, lookup["by_norm"]),
        (name_norm, lookup["by_translit"]),
        (name_tr, lookup["by_norm"]),
        (name_tr, lookup["by_translit"]),
        (name_ns, lookup["by_nospace"]),
        (name_ns, lookup["by_translit_nospace"]),
        (name_tr_ns, lookup["by_nospace"]),
        (name_tr_ns, lookup["by_translit_nospace"]),
    ]:
        if key and key in mapping:
            return mapping[key]

    return None


def compare_discography_with_likes(discography, likes_payload):
    actual_tracks, exact_index, artist_index, title_word_index = build_actual_tracks(likes_payload)

    found = []
    not_found = []
    found_idx_set = set()
    exact_found = 0
    fuzzy_found = 0

    for idx, track in enumerate(discography):
        expected = build_expected_record(track, idx)
        match_type, match = find_match(expected, exact_index, artist_index, title_word_index)

        if match:
            found_idx_set.add(idx)
            if match_type == "exact":
                exact_found += 1
            else:
                fuzzy_found += 1

            found.append(
                {
                    "idx": expected["idx"],
                    "chronological_index": expected["chronological_index"],
                    "source": expected["source"],
                    "artist": expected["artist"],
                    "title": expected["title"],
                    "match_type": match_type,
                    "spotify_artist": match["artist"],
                    "spotify_title": match["title"],
                    "spotify_id": match.get("spotify_id"),
                    "saved_at": match.get("saved_at"),
                }
            )
        else:
            not_found.append(
                {
                    "idx": expected["idx"],
                    "chronological_index": expected["chronological_index"],
                    "source": expected["source"],
                    "artist": expected["artist"],
                    "title": expected["title"],
                }
            )

    summary = {
        "actual_spotify_likes_count": len(actual_tracks),
        "discography_count": len(discography),
        "found_total": len(found),
        "found_exact": exact_found,
        "found_fuzzy": fuzzy_found,
        "not_found_total": len(not_found),
        "found_percent": safe_int_percent(len(found), len(discography)),
        "not_found_percent": safe_int_percent(len(not_found), len(discography)),
    }

    return summary, found, not_found, found_idx_set


def analyze_discography_artists(discography, found_idx_set, followed_lookup, threshold):
    stats = {}

    for idx, track in enumerate(discography):
        artist_names = split_artist_names_preserve_case(track.get("artist", ""))
        is_found = idx in found_idx_set
        title = track.get("title")
        source = track.get("source")
        chrono = track.get("chronological_index", idx + 1)

        for artist_name in artist_names:
            artist_key = normalize_text(artist_name)
            if not artist_key:
                continue

            item = stats.setdefault(
                artist_key,
                {
                    "artist": artist_name,
                    "normalized_artist": artist_key,
                    "discography_track_count": 0,
                    "added_track_count": 0,
                    "missing_track_count": 0,
                    "followed_on_spotify": False,
                    "spotify_followed_artist_name": None,
                    "example_added_tracks": [],
                    "example_missing_tracks": [],
                },
            )

            item["discography_track_count"] += 1

            example_entry = {
                "chronological_index": chrono,
                "source": source,
                "artist": track.get("artist"),
                "title": title,
            }

            if is_found:
                item["added_track_count"] += 1
                if len(item["example_added_tracks"]) < 5:
                    item["example_added_tracks"].append(example_entry)
            else:
                item["missing_track_count"] += 1
                if len(item["example_missing_tracks"]) < 5:
                    item["example_missing_tracks"].append(example_entry)

    artist_stats = []
    potential_artists = []

    for item in stats.values():
        followed_match = find_followed_artist_match(item["artist"], followed_lookup)
        item["followed_on_spotify"] = followed_match is not None
        item["spotify_followed_artist_name"] = followed_match["name"] if followed_match else None
        item["potential_for_add"] = item["added_track_count"] > threshold and not item["followed_on_spotify"]
        artist_stats.append(item)
        if item["potential_for_add"]:
            potential_artists.append(item)

    artist_stats.sort(
        key=lambda x: (-x["added_track_count"], -x["discography_track_count"], x["artist"].lower())
    )
    potential_artists.sort(
        key=lambda x: (-x["added_track_count"], -x["discography_track_count"], x["artist"].lower())
    )

    return artist_stats, potential_artists


def main():
    args = parse_args()

    likes_input = os.path.abspath(args.likes_input)
    likes_output = os.path.abspath(args.likes_output)
    artists_output = os.path.abspath(args.artists_output)
    report_output = os.path.abspath(args.report_output)
    not_found_output = os.path.abspath(args.not_found_output)
    artist_stats_output = os.path.abspath(args.artist_stats_output)
    potential_output = os.path.abspath(args.potential_output)

    print("Проверка авторизации Spotify...")
    sp = build_spotify_client(args.cache_path)
    me = sp.current_user()
    print(f"Авторизован как: {me.get('display_name') or me['id']} ({me['id']})")
    print()

    if args.skip_likes_export:
        if not os.path.exists(likes_input):
            raise FileNotFoundError(
                f"Файл лайков не найден: {likes_input}. "
                "Либо убери --skip-likes-export, либо укажи корректный --likes-input."
            )
        print(f"Лайки не обновляю, использую существующий файл: {likes_input}")
        likes_payload = load_json(likes_input, "utf-8")
        liked_tracks = likes_payload.get("tracks", []) if isinstance(likes_payload, dict) else likes_payload
        liked_total_reported = (
            likes_payload.get("total_reported_by_api")
            if isinstance(likes_payload, dict)
            else len(liked_tracks)
        )
    else:
        liked_total_reported, liked_tracks = export_liked_tracks(sp, args.limit)
        likes_payload = {
            "exported_at": datetime.now().isoformat(),
            "spotify_user": {
                "id": me["id"],
                "display_name": me.get("display_name"),
            },
            "total_reported_by_api": liked_total_reported,
            "exported_count": len(liked_tracks),
            "tracks": liked_tracks,
        }
        save_json(likes_output, likes_payload)

    followed_artists = export_followed_artists(sp, args.limit)

    artists_payload = {
        "exported_at": datetime.now().isoformat(),
        "spotify_user": {
            "id": me["id"],
            "display_name": me.get("display_name"),
        },
        "exported_count": len(followed_artists),
        "artists": followed_artists,
    }

    save_json(artists_output, artists_payload)

    discography = load_json(args.discography, "utf-8-sig")
    if not isinstance(discography, list):
        raise ValueError("Файл полной дискографии должен быть списком треков")

    compare_summary, found, not_found, found_idx_set = compare_discography_with_likes(discography, likes_payload)
    followed_lookup = build_followed_lookup(followed_artists)
    artist_stats, potential_artists = analyze_discography_artists(
        discography,
        found_idx_set,
        followed_lookup,
        args.artist_threshold,
    )

    summary = {
        "generated_at": datetime.now().isoformat(),
        "spotify_user": {
            "id": me["id"],
            "display_name": me.get("display_name"),
        },
        "likes_input": likes_input if args.skip_likes_export else likes_output,
        "likes_output": likes_output if not args.skip_likes_export else None,
        "artists_output": artists_output,
        "discography_file": os.path.abspath(args.discography),
        "report_output": report_output,
        "not_found_output": not_found_output,
        "artist_stats_output": artist_stats_output,
        "potential_output": potential_output,
        "followed_artists_count": len(followed_artists),
        "artist_threshold": args.artist_threshold,
        "potential_artists_count": len(potential_artists),
        **compare_summary,
    }

    report_payload = {
        "summary": summary,
        "found": found,
        "not_found": not_found,
        "artist_stats": artist_stats,
        "potential_artists_for_add": potential_artists,
    }

    save_json(report_output, report_payload)
    save_json(not_found_output, not_found)
    save_json(artist_stats_output, artist_stats)
    save_json(potential_output, potential_artists)

    print()
    print("Аудит завершён.")
    print(f"Текущих лайков Spotify: {summary['actual_spotify_likes_count']}")
    print(f"Followed artists Spotify: {summary['followed_artists_count']}")
    print(f"Треков в полной дискографии: {summary['discography_count']}")
    print(
        f"Найдено: {summary['found_total']} "
        f"(exact={summary['found_exact']}, fuzzy={summary['found_fuzzy']}) "
        f"= {summary['found_percent']}%"
    )
    print(f"Не найдено: {summary['not_found_total']} = {summary['not_found_percent']}%")
    print(
        f"Potential artists for add (> {args.artist_threshold} added tracks и не followed): "
        f"{summary['potential_artists_count']}"
    )
    print(f"Лайки для анализа: {summary['likes_input']}")
    if summary["likes_output"]:
        print(f"Обновлённый экспорт лайков: {summary['likes_output']}")
    print(f"Исполнители: {artists_output}")
    print(f"Полный отчёт: {report_output}")
    print(f"Not found: {not_found_output}")
    print(f"Статы по артистам: {artist_stats_output}")
    print(f"Potential artists: {potential_output}")

    if not_found and args.sample > 0:
        print()
        print(f"Первые {min(args.sample, len(not_found))} ненайденных:")
        for item in not_found[: args.sample]:
            print(
                f"  #{item['chronological_index']} ({item.get('source') or '?'}) "
                f"{item['artist']} — {item['title']}"
            )

    if potential_artists and args.sample > 0:
        print()
        print(f"Первые {min(args.sample, len(potential_artists))} potential artists:")
        for item in potential_artists[: args.sample]:
            print(
                f"  {item['artist']}: added={item['added_track_count']}, "
                f"total={item['discography_track_count']}, missing={item['missing_track_count']}"
            )


if __name__ == "__main__":
    main()
