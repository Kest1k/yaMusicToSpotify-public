"""Обновление всех JSON-данных для локального Spotify dashboard."""

import argparse
import json
import os
import shutil
import subprocess
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = sys.executable

DEFAULT_LIKES = os.path.join(SCRIPT_DIR, "ACTUAL SPOTIFY LIKES.json")
DEFAULT_ARTISTS = os.path.join(SCRIPT_DIR, "ACTUAL SPOTIFY ARTISTS.json")
DEFAULT_REPORT = os.path.join(SCRIPT_DIR, "spotify_library_audit_report.json")
DEFAULT_NOT_FOUND = os.path.join(SCRIPT_DIR, "spotify_likes_not_found.json")
DEFAULT_ARTIST_STATS = os.path.join(SCRIPT_DIR, "discography_artist_stats.json")
DEFAULT_POTENTIAL = os.path.join(SCRIPT_DIR, "potential_artists_for_add.json")
DEFAULT_DISCOGRAPHY = os.path.join(SCRIPT_DIR, "MY FULL DISCOGRAPHY (liked tracks).json")
SITE_DIR = os.path.join(SCRIPT_DIR, "site")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Обновить данные для dashboard: liked tracks, followed artists, audit report и производные JSON."
    )
    parser.add_argument(
        "--skip-likes-export",
        action="store_true",
        help="Не перевыгружать liked songs, а использовать уже существующий ACTUAL SPOTIFY LIKES.json",
    )
    parser.add_argument(
        "--likes-input",
        default=DEFAULT_LIKES,
        help="Какой файл лайков использовать, если включён --skip-likes-export",
    )
    parser.add_argument(
        "--artist-threshold",
        type=int,
        default=3,
        help="Порог для potential artists. В список попадут исполнители с added_track_count строго больше порога.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Размер батча Spotify API",
    )
    parser.add_argument(
        "--sync-site-snapshot",
        action="store_true",
        help="Перезаписать demo snapshot в site/ текущими локальными данными.",
    )
    return parser.parse_args()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_command(args):
    command = [
        PYTHON_EXE,
        os.path.join(SCRIPT_DIR, "spotify_library_audit.py"),
        "--discography",
        DEFAULT_DISCOGRAPHY,
        "--likes-input",
        os.path.abspath(args.likes_input),
        "--likes-output",
        DEFAULT_LIKES,
        "--artists-output",
        DEFAULT_ARTISTS,
        "--report-output",
        DEFAULT_REPORT,
        "--not-found-output",
        DEFAULT_NOT_FOUND,
        "--artist-stats-output",
        DEFAULT_ARTIST_STATS,
        "--potential-output",
        DEFAULT_POTENTIAL,
        "--artist-threshold",
        str(args.artist_threshold),
        "--limit",
        str(args.limit),
        "--sample",
        "8",
    ]
    if args.skip_likes_export:
        command.append("--skip-likes-export")
    return command


def print_summary(report_path):
    report = load_json(report_path)
    summary = report.get("summary", {})

    print()
    print("Данные для сайта обновлены.")
    print(f"Liked songs в Spotify: {summary.get('actual_spotify_likes_count', 0)}")
    print(f"Followed artists: {summary.get('followed_artists_count', 0)}")
    print(
        f"Найдено в полной дискографии: {summary.get('found_total', 0)} "
        f"из {summary.get('discography_count', 0)} "
        f"({summary.get('found_percent', 0)}%)"
    )
    print(f"Не найдено: {summary.get('not_found_total', 0)}")
    print(f"Potential artists: {summary.get('potential_artists_count', 0)}")
    print(f"Отчёт: {report_path}")


def sync_site_snapshot():
    if not os.path.isdir(SITE_DIR):
        return

    files_to_copy = [
        DEFAULT_ARTISTS,
        DEFAULT_REPORT,
        os.path.join(SCRIPT_DIR, "selected_potential_artists.json"),
    ]
    for source in files_to_copy:
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(SITE_DIR, os.path.basename(source)))

    print(f"Site snapshot sync: {SITE_DIR}")


def main():
    args = parse_args()
    command = build_command(args)

    print("Обновляю данные Spotify для dashboard...")
    print("Команда:")
    print(" ".join(f'"{part}"' if " " in part else part for part in command))
    print()

    subprocess.run(command, check=True, cwd=SCRIPT_DIR)
    if args.sync_site_snapshot:
        sync_site_snapshot()
    print_summary(DEFAULT_REPORT)


if __name__ == "__main__":
    main()
