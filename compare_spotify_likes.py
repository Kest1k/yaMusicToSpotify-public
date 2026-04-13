"""Сравнение полной дискографии с актуальными лайками Spotify."""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = SCRIPT_DIR
sys.path.insert(0, PROJECT_DIR)

from shared.utils import fuzzy_match, normalize_text, split_artists, transliterate  # noqa: E402


DEFAULT_LIKES_FILE = os.path.join(SCRIPT_DIR, "ACTUAL SPOTIFY LIKES.json")
DEFAULT_DISCOGRAPHY_FILE = os.path.join(SCRIPT_DIR, "MY FULL DISCOGRAPHY (liked tracks).json")


def parse_args():
    parser = argparse.ArgumentParser(description="Сравнить актуальные лайки Spotify с полной дискографией")
    parser.add_argument("--likes", default=DEFAULT_LIKES_FILE, help="JSON с экспортом текущих Spotify Liked Songs")
    parser.add_argument("--discography", default=DEFAULT_DISCOGRAPHY_FILE, help="JSON с полной дискографией")
    parser.add_argument(
        "--report",
        default=os.path.join(SCRIPT_DIR, "spotify_likes_compare_report.json"),
        help="Куда сохранить полный отчёт",
    )
    parser.add_argument(
        "--not-found",
        dest="not_found",
        default=os.path.join(SCRIPT_DIR, "spotify_likes_not_found.json"),
        help="Куда сохранить список ненайденных треков",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=15,
        help="Сколько первых not_found показать в консоли",
    )
    return parser.parse_args()


def load_json(path, encoding):
    with open(path, "r", encoding=encoding) as fh:
        return json.load(fh)


def safe_int_percent(part, total):
    if not total:
        return 0.0
    return round((part / total) * 100, 2)


def normalize_pair(artist, title):
    return normalize_text(artist), normalize_text(title)


def translit_pair(artist, title):
    return normalize_text(transliterate(artist)), normalize_text(transliterate(title))


def build_actual_tracks(likes_payload):
    if isinstance(likes_payload, dict):
        tracks = likes_payload.get("tracks", [])
    elif isinstance(likes_payload, list):
        tracks = likes_payload
    else:
        raise ValueError("Неподдерживаемый формат файла лайков Spotify")

    actual_tracks = []
    exact_index = defaultdict(list)
    artist_index = defaultdict(list)
    title_word_index = defaultdict(list)

    for idx, track in enumerate(tracks):
        artist = track.get("artist") or ", ".join(track.get("artists", []))
        title = track.get("name") or track.get("title") or ""

        record = {
            "idx": idx,
            "spotify_id": track.get("spotify_id"),
            "saved_at": track.get("saved_at") or track.get("added_at"),
            "artist": artist,
            "title": title,
            "raw": track,
        }

        artist_norm, title_norm = normalize_pair(artist, title)
        artist_tr, title_tr = translit_pair(artist, title)
        record["artist_norm"] = artist_norm
        record["title_norm"] = title_norm
        record["artist_tr"] = artist_tr
        record["title_tr"] = title_tr

        actual_tracks.append(record)

        exact_index[(artist_norm, title_norm)].append(record)
        exact_index[(artist_tr, title_tr)].append(record)

        artist_forms = set(split_artists(artist))
        if artist_norm:
            artist_forms.add(artist_norm)
        if artist_tr:
            artist_forms.add(artist_tr)
        for form in artist_forms:
            if form:
                artist_index[form].append(record)

        title_words = set(title_norm.split()) | set(title_tr.split())
        for word in title_words:
            if len(word) >= 4 or word.isdigit():
                title_word_index[word].append(record)

    return actual_tracks, exact_index, artist_index, title_word_index


def build_expected_record(track, idx):
    artist = track.get("artist", "")
    title = track.get("title", "")
    artist_norm, title_norm = normalize_pair(artist, title)
    artist_tr, title_tr = translit_pair(artist, title)
    return {
        "idx": idx,
        "artist": artist,
        "title": title,
        "source": track.get("source"),
        "chronological_index": track.get("chronological_index", idx + 1),
        "raw": track,
        "artist_norm": artist_norm,
        "title_norm": title_norm,
        "artist_tr": artist_tr,
        "title_tr": title_tr,
    }


def candidate_records(expected, exact_index, artist_index, title_word_index):
    candidates = []
    seen = set()

    exact_keys = [
        (expected["artist_norm"], expected["title_norm"]),
        (expected["artist_tr"], expected["title_tr"]),
    ]
    for key in exact_keys:
        for record in exact_index.get(key, []):
            if record["idx"] not in seen:
                seen.add(record["idx"])
                candidates.append(record)

    artist_forms = set(split_artists(expected["artist"]))
    if expected["artist_norm"]:
        artist_forms.add(expected["artist_norm"])
    if expected["artist_tr"]:
        artist_forms.add(expected["artist_tr"])
    for form in artist_forms:
        for record in artist_index.get(form, []):
            if record["idx"] not in seen:
                seen.add(record["idx"])
                candidates.append(record)

    expected_title_words = set(expected["title_norm"].split()) | set(expected["title_tr"].split())
    ranked_words = sorted(
        (word for word in expected_title_words if len(word) >= 4 or word.isdigit()),
        key=len,
        reverse=True,
    )
    for word in ranked_words[:3]:
        for record in title_word_index.get(word, []):
            if record["idx"] not in seen:
                seen.add(record["idx"])
                candidates.append(record)

    return candidates


def find_match(expected, exact_index, artist_index, title_word_index):
    for key in [
        (expected["artist_norm"], expected["title_norm"]),
        (expected["artist_tr"], expected["title_tr"]),
    ]:
        records = exact_index.get(key)
        if records:
            return "exact", records[0]

    candidates = candidate_records(expected, exact_index, artist_index, title_word_index)
    for record in candidates:
        if fuzzy_match(expected["artist"], expected["title"], record["artist"], record["title"]):
            return "fuzzy", record

    return None, None


def main():
    args = parse_args()

    likes_payload = load_json(args.likes, "utf-8")
    discography = load_json(args.discography, "utf-8-sig")

    if not isinstance(discography, list):
        raise ValueError("Файл полной дискографии должен быть списком треков")

    actual_tracks, exact_index, artist_index, title_word_index = build_actual_tracks(likes_payload)

    found = []
    not_found = []
    exact_found = 0
    fuzzy_found = 0

    for idx, track in enumerate(discography):
        expected = build_expected_record(track, idx)
        match_type, match = find_match(expected, exact_index, artist_index, title_word_index)

        if match:
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
        "generated_at": datetime.now().isoformat(),
        "likes_file": os.path.abspath(args.likes),
        "discography_file": os.path.abspath(args.discography),
        "actual_spotify_likes_count": len(actual_tracks),
        "discography_count": len(discography),
        "found_total": len(found),
        "found_exact": exact_found,
        "found_fuzzy": fuzzy_found,
        "not_found_total": len(not_found),
        "found_percent": safe_int_percent(len(found), len(discography)),
        "not_found_percent": safe_int_percent(len(not_found), len(discography)),
    }

    report = {
        "summary": summary,
        "found": found,
        "not_found": not_found,
    }

    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    with open(args.not_found, "w", encoding="utf-8") as fh:
        json.dump(not_found, fh, ensure_ascii=False, indent=2)

    print("Сравнение завершено.")
    print(f"Текущих лайков Spotify: {summary['actual_spotify_likes_count']}")
    print(f"Треков в полной дискографии: {summary['discography_count']}")
    print(
        f"Найдено: {summary['found_total']} "
        f"(exact={summary['found_exact']}, fuzzy={summary['found_fuzzy']}) "
        f"= {summary['found_percent']}%"
    )
    print(f"Не найдено: {summary['not_found_total']} = {summary['not_found_percent']}%")
    print(f"Отчёт: {os.path.abspath(args.report)}")
    print(f"Список not_found: {os.path.abspath(args.not_found)}")

    if not_found and args.sample > 0:
        print()
        print(f"Первые {min(args.sample, len(not_found))} ненайденных:")
        for item in not_found[: args.sample]:
            print(
                f"  #{item['chronological_index']} ({item.get('source') or '?'}) "
                f"{item['artist']} — {item['title']}"
            )


if __name__ == "__main__":
    main()
