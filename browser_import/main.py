"""
Spotify Browser Automation — добавление треков через UI (без API лимитов).

Использует Playwright для автоматизации open.spotify.com:
1. Первый запуск — открывает браузер для ручного логина, сохраняет сессию
2. Далее — автоматически ищет треки и добавляет в лайки

Источник: MY FULL DISCOGRAPHY (liked tracks).json
Прогресс: browser_import/progress.json

Запуск:
  python main.py              — основной режим (продолжает с последнего)
  python main.py --chrome     — через Google Chrome (уже залогинен, не нужен --login)
  python main.py --login      — только логин (сохранить сессию Playwright)
  python main.py --start 691  — начать с конкретного индекса
  python main.py --not-found  — обработать только not_found треки
"""

import json
import os
import re
import sys
import argparse
from time import sleep
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Добавляем корень проекта в sys.path для импорта shared
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from shared.utils import (
    transliterate, has_cyrillic, clean_for_search,
    build_search_query, fuzzy_match,
)

# ============================================================
# ПУТИ
# ============================================================
DISCOGRAPHY_FILE = os.path.join(PROJECT_DIR, 'MY FULL DISCOGRAPHY (liked tracks).json')
NOT_FOUND_FILE = os.path.join(PROJECT_DIR, 'spotify_likes_not_found.json')

PROGRESS_FILE = os.path.join(SCRIPT_DIR, 'progress.json')
SESSION_DIR = os.path.join(SCRIPT_DIR, 'browser_session')
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f'automation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
NOT_FINDED_LOG = os.path.join(LOG_DIR, f'notFinded_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

# Таймауты (секунды)
SEARCH_DELAY = 2.5        # пауза после ввода поиска (ждём результаты)
BETWEEN_TRACKS = 2.0      # пауза между треками
LIKE_DELAY = 1.0           # пауза после нажатия лайка
PAGE_LOAD_TIMEOUT = 15000  # мс, таймаут загрузки страницы


# ============================================================
# ПРОГРЕСС
# ============================================================
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'current_idx': 0, 'found': 0, 'not_found': 0, 'skipped': 0, 'liked': 0}

def save_progress(data):
    tmp = PROGRESS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
def log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def log_not_found(vk_artist, vk_title, found_artist=None, found_title=None):
    """Пишет в notFinded лог: что искали → что нашёл Spotify (или ничего)."""
    with open(NOT_FINDED_LOG, 'a', encoding='utf-8') as f:
        if found_artist:
            f.write(f"{vk_artist} — {vk_title}  >>>  {found_artist} — {found_title}\n")
        else:
            f.write(f"{vk_artist} — {vk_title}  >>>  НЕ НАЙДЕН\n")


# ============================================================
# ОСНОВНАЯ АВТОМАТИЗАЦИЯ
# ============================================================
class SpotifyAutomation:
    def __init__(self, headless=False, use_chrome=False):
        self.headless = headless
        self.use_chrome = use_chrome
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _find_chrome_user_data_dir(self):
        """Находит путь к профилю Google Chrome."""
        if sys.platform == 'win32':
            local_app = os.environ.get('LOCALAPPDATA', '')
            path = os.path.join(local_app, 'Google', 'Chrome', 'User Data')
            if os.path.exists(path):
                return path
        elif sys.platform == 'darwin':
            path = os.path.expanduser('~/Library/Application Support/Google/Chrome')
            if os.path.exists(path):
                return path
        else:
            path = os.path.expanduser('~/.config/google-chrome')
            if os.path.exists(path):
                return path
        return None

    def _find_chrome_executable(self):
        """Находит путь к исполняемому файлу Google Chrome."""
        if sys.platform == 'win32':
            candidates = [
                os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
                os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
            ]
        elif sys.platform == 'darwin':
            candidates = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
        else:
            candidates = ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium-browser']

        for path in candidates:
            if path and os.path.exists(path):
                log(f"  🔧 Chrome: {path}")
                return path

        # Fallback — пусть Playwright сам найдёт через channel
        log("  ⚠️ Chrome executable не найден, пробуем channel='chrome'")
        return None

    def _copy_chrome_session(self):
        """Копирует куки и localStorage из Chrome профиля в сессию Playwright."""
        import shutil
        import sqlite3

        chrome_data = self._find_chrome_user_data_dir()
        if not chrome_data:
            log("❌ Не найден профиль Google Chrome!")
            sys.exit(1)

        chrome_default = os.path.join(chrome_data, 'Default')
        pw_default = os.path.join(SESSION_DIR, 'Default')
        os.makedirs(pw_default, exist_ok=True)

        # Копируем файлы с куками и сессией
        files_to_copy = [
            'Cookies',
            'Login Data',
            'Web Data',
            'Local State',
        ]
        for fname in files_to_copy:
            src = os.path.join(chrome_default, fname)
            if os.path.exists(src):
                dst = os.path.join(pw_default, fname)
                try:
                    shutil.copy2(src, dst)
                    log(f"  📋 Скопирован: {fname}")
                except Exception as e:
                    log(f"  ⚠️ Не удалось скопировать {fname}: {e}")

        # Копируем Local Storage
        ls_src = os.path.join(chrome_default, 'Local Storage')
        ls_dst = os.path.join(pw_default, 'Local Storage')
        if os.path.exists(ls_src):
            try:
                if os.path.exists(ls_dst):
                    shutil.rmtree(ls_dst)
                shutil.copytree(ls_src, ls_dst)
                log("  📋 Скопирован: Local Storage")
            except Exception as e:
                log(f"  ⚠️ Не удалось скопировать Local Storage: {e}")

        # Копируем Local State из корня User Data
        local_state_src = os.path.join(chrome_data, 'Local State')
        local_state_dst = os.path.join(SESSION_DIR, 'Local State')
        if os.path.exists(local_state_src):
            try:
                shutil.copy2(local_state_src, local_state_dst)
                log("  📋 Скопирован: Local State (root)")
            except Exception as e:
                log(f"  ⚠️ Не удалось скопировать Local State: {e}")

        log("✅ Сессия Chrome скопирована")

    def start(self):
        """Запускает браузер с сохранённой сессией."""
        self.playwright = sync_playwright().start()

        if self.use_chrome:
            # Запускаем настоящий Google Chrome с его родным профилем.
            # Chrome должен быть ЗАКРЫТ перед запуском!
            chrome_data = self._find_chrome_user_data_dir()
            if not chrome_data:
                log("❌ Не найден профиль Google Chrome!")
                sys.exit(1)

            log("🌐 Запускаем Google Chrome с родным профилем...")
            log("⚠️  Chrome должен быть ЗАКРЫТ!")

            chrome_exe = self._find_chrome_executable()
            launch_kwargs = dict(
                user_data_dir=chrome_data,
                headless=self.headless,
                viewport={'width': 1280, 'height': 900},
                locale='ru-RU',
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--profile-directory=Default',
                ],
            )
            if chrome_exe:
                launch_kwargs['executable_path'] = chrome_exe
            else:
                launch_kwargs['channel'] = 'chrome'

            self.context = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        else:
            # Используем встроенный Playwright Chromium с отдельной сессией
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=SESSION_DIR,
                headless=self.headless,
                viewport={'width': 1280, 'height': 900},
                locale='ru-RU',
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                ],
            )

        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = self.context.new_page()

    def stop(self):
        """Закрывает браузер."""
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()

    def login(self):
        """Открывает Spotify для ручного логина."""
        log("🔐 Открываю Spotify для логина...")
        self.page.goto('https://open.spotify.com/', timeout=PAGE_LOAD_TIMEOUT)
        log("⏳ Залогинься вручную в открывшемся браузере.")
        log("   После логина нажми Enter в консоли для продолжения...")
        input("   >>> Нажми Enter когда залогинишься: ")

        # Проверяем что залогинились
        if self._is_logged_in():
            log("✅ Логин успешен! Сессия сохранена.")
            return True
        else:
            log("❌ Похоже, логин не удался. Попробуй ещё раз.")
            return False

    def _is_logged_in(self):
        """Проверяет залогинен ли пользователь."""
        try:
            self.page.goto('https://open.spotify.com/', timeout=PAGE_LOAD_TIMEOUT)
            sleep(3)
            # Если есть кнопка логина — не залогинен
            login_btn = self.page.query_selector('[data-testid="login-button"]')
            if login_btn:
                return False
            # Проверяем наличие элементов залогиненного юзера
            return True
        except Exception:
            return False

    def search_and_like(self, artist, title, chrono_idx):
        """
        Ищет трек и добавляет в лайки.
        Возвращает: 'liked', 'not_found', 'already_liked', 'error'
        """
        query = build_search_query(artist, title)
        log(f"  🔍 Поиск: '{query}'")

        try:
            # Переходим на страницу поиска
            search_url = f"https://open.spotify.com/search/{quote(query)}"
            self.page.goto(search_url, timeout=PAGE_LOAD_TIMEOUT)
            sleep(SEARCH_DELAY)

            # Проверяем не разлогинило ли
            if self._check_logged_out():
                log("🔐 Сессия истекла! Жду ре-логин...")
                self._wait_for_relogin()
                # Повторяем поиск после логина
                self.page.goto(search_url, timeout=PAGE_LOAD_TIMEOUT)
                sleep(SEARCH_DELAY)

            # Ищем первый результат трека
            track_row = self._find_first_track_row()
            if not track_row:
                log(f"  ❌ Нет результатов для: {artist} — {title}")
                log_not_found(artist, title)
                return 'not_found'

            # Валидируем что найденный трек соответствует искомому
            is_valid, found_artist, found_title = self._validate_match(track_row, artist, title)
            found_info = f"{found_artist} — {found_title}" if found_artist else None
            if found_info:
                log(f"  📋 Найден: {found_info}")

            if not is_valid:
                log_not_found(artist, title, found_artist, found_title)
                return 'not_found'

            # Проверяем, уже лайкнут ли трек
            if self._is_track_liked(track_row):
                log(f"  💚 Уже в лайках!")
                return 'already_liked'

            # Наводим на трек и жмём лайк
            liked = self._like_track(track_row)
            if liked:
                log(f"  ✅ Добавлен в лайки!")
                return 'liked'
            else:
                log(f"  ⚠️ Не удалось нажать лайк")
                return 'error'

        except PlaywrightTimeout:
            log(f"  ⏰ Таймаут при поиске: {artist} — {title}")
            return 'error'
        except Exception as e:
            log(f"  ❌ Ошибка: {e}")
            return 'error'

    def _validate_match(self, track_row, expected_artist, expected_title):
        """
        Проверяет что найденный трек соответствует искомому.
        Умная fuzzy-логика: обрабатывает ё/е, пунктуацию, перестановку слов,
        скиты, ремиксы, feat, скобки и т.д.
        Возвращает (is_valid, found_artist, found_title).
        """
        try:
            info = self.page.evaluate('''(el) => {
                let root = el;
                for (let i = 0; i < 10; i++) {
                    if (root.getAttribute && (root.getAttribute('role') === 'row' ||
                        root.getAttribute('data-testid')?.includes('row'))) break;
                    if (root.parentElement) root = root.parentElement;
                    else break;
                }
                const links = root.querySelectorAll('a');
                let trackName = '';
                let artistName = '';
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('/track/') && !trackName) {
                        trackName = link.textContent.trim();
                    }
                    if (href.includes('/artist/') && !artistName) {
                        artistName = link.textContent.trim();
                    }
                }
                return {artist: artistName, title: trackName};
            }''', track_row)

            if not info or not info.get('artist'):
                return True, '', ''  # не смогли извлечь — пропускаем валидацию

            found_artist = info['artist']
            found_title = info['title']

            is_valid = fuzzy_match(expected_artist, expected_title, found_artist, found_title)

            if not is_valid:
                log(f"  ⚠️ Не совпадает! Ожидали: {expected_artist} — {expected_title}")
                log(f"     Нашли: {found_artist} — {found_title}")

            return is_valid, found_artist, found_title

        except Exception as e:
            log(f"  ⚠️ Ошибка валидации: {e}")
            return True, '', ''  # при ошибке — пропускаем валидацию

    def _check_logged_out(self):
        """Проверяет разлогинило ли нас."""
        try:
            login_btn = self.page.query_selector('[data-testid="login-button"]')
            return login_btn is not None and login_btn.is_visible()
        except Exception:
            return False

    def _wait_for_relogin(self):
        """Ждёт пока пользователь залогинится снова."""
        log("⚠️  Открываю главную страницу...")
        self.page.goto('https://open.spotify.com/', timeout=PAGE_LOAD_TIMEOUT)
        log("   Залогинься в браузере, потом нажми Enter в консоли")
        input("   >>> Enter после логина: ")

    def _click_songs_filter(self):
        """Кликает на фильтр 'Треки'/'Songs' в результатах поиска."""
        try:
            # Ищем кнопку фильтра "Треки" или "Songs"
            for selector in [
                'button:has-text("Треки")',
                'button:has-text("Songs")',
                'a:has-text("Треки")',
                'a:has-text("Songs")',
                '[data-testid="search-category-card-songs"]',
                'span:has-text("Треки")',
                'span:has-text("Songs")',
            ]:
                btn = self.page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    return True
        except Exception:
            pass
        return False

    def _find_first_track_row(self):
        """Находит первую строку трека в результатах поиска."""
        # Spotify использует разные селекторы, пробуем несколько
        selectors = [
            '[data-testid="tracklist-row"]',
            '[data-testid="track-row"]',
            'div[role="row"][aria-rowindex="1"]',
            'div[data-testid="tracklist-row"]:first-child',
            '.contentSpacing section div[role="row"]',
            'div[role="gridcell"] a[href*="/track/"]',
        ]

        for selector in selectors:
            try:
                el = self.page.wait_for_selector(selector, timeout=5000)
                if el:
                    return el
            except PlaywrightTimeout:
                continue

        # Fallback: ищем любую ссылку на трек
        try:
            track_link = self.page.query_selector('a[href*="/track/"]')
            if track_link:
                # Поднимаемся к родительской строке
                row = self.page.evaluate('''(el) => {
                    let parent = el;
                    for (let i = 0; i < 10; i++) {
                        parent = parent.parentElement;
                        if (!parent) return null;
                        if (parent.getAttribute('role') === 'row' ||
                            parent.getAttribute('data-testid')?.includes('row')) {
                            return true;
                        }
                    }
                    return null;
                }''', track_link)
                if row:
                    return track_link
        except Exception:
            pass

        return None

    def _get_track_info(self, track_row):
        """Извлекает информацию о треке из строки результата."""
        try:
            info = self.page.evaluate('''(el) => {
                // Ищем в контексте строки или рядом
                let root = el;
                for (let i = 0; i < 10; i++) {
                    if (root.getAttribute && (root.getAttribute('role') === 'row' ||
                        root.getAttribute('data-testid')?.includes('row'))) break;
                    if (root.parentElement) root = root.parentElement;
                    else break;
                }
                const links = root.querySelectorAll('a');
                let trackName = '';
                let artistName = '';
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('/track/') && !trackName) {
                        trackName = link.textContent.trim();
                    }
                    if (href.includes('/artist/') && !artistName) {
                        artistName = link.textContent.trim();
                    }
                }
                return trackName && artistName ? `${artistName} — ${trackName}` : null;
            }''', track_row)
            return info
        except Exception:
            return None

    def _is_track_liked(self, track_row):
        """Проверяет, лайкнут ли трек."""
        try:
            result = self.page.evaluate('''(el) => {
                let root = el;
                for (let i = 0; i < 10; i++) {
                    if (root.getAttribute && (root.getAttribute('role') === 'row' ||
                        root.getAttribute('data-testid')?.includes('row'))) break;
                    if (root.parentElement) root = root.parentElement;
                    else break;
                }
                // Ищем кнопку лайка с aria-checked="true" или заполненное сердечко
                const likeBtn = root.querySelector('[data-testid="add-button"][aria-checked="true"]') ||
                                root.querySelector('button[aria-label*="Remove from"] ') ||
                                root.querySelector('button[aria-label*="Удалить из"]');
                return !!likeBtn;
            }''', track_row)
            return result
        except Exception:
            return False

    def _like_track(self, track_row):
        """Наводит на трек и нажимает кнопку лайка."""
        try:
            # Наводим мышь на строку трека
            track_row.hover()
            sleep(0.5)

            # Ищем кнопку лайка (сердечко / +)
            like_selectors = [
                '[data-testid="add-button"]',
                'button[aria-label*="Save to"]',
                'button[aria-label*="Сохранить в"]',
                'button[aria-label*="Add to Liked"]',
                'button[aria-label*="Добавить в"]',
                'button[aria-label*="Like"]',
            ]

            # Сначала ищем в контексте строки
            for selector in like_selectors:
                try:
                    # Ищем кнопку внутри строки
                    btn = self.page.evaluate(f'''(el) => {{
                        let root = el;
                        for (let i = 0; i < 10; i++) {{
                            if (root.getAttribute && (root.getAttribute('role') === 'row' ||
                                root.getAttribute('data-testid')?.includes('row'))) break;
                            if (root.parentElement) root = root.parentElement;
                            else break;
                        }}
                        const btn = root.querySelector('{selector}');
                        if (btn) {{ btn.click(); return true; }}
                        return false;
                    }}''', track_row)
                    if btn:
                        sleep(LIKE_DELAY)
                        return True
                except Exception:
                    continue

            # Fallback: правый клик → "Сохранить в медиатеку"
            track_row.click(button='right')
            sleep(0.5)

            context_menu_items = [
                'text="Save to your Liked Songs"',
                'text="Сохранить в медиатеку"',
                'text="Add to Liked Songs"',
                'text="Добавить в плейлист \"Любимые песни\""',
            ]
            for item_selector in context_menu_items:
                try:
                    menu_item = self.page.wait_for_selector(item_selector, timeout=2000)
                    if menu_item:
                        menu_item.click()
                        sleep(LIKE_DELAY)
                        return True
                except PlaywrightTimeout:
                    continue

            # Закрываем контекстное меню если ничего не нашли
            self.page.keyboard.press('Escape')
            return False

        except Exception as e:
            log(f"    ⚠️ Ошибка при лайке: {e}")
            return False


# ============================================================
# РЕЖИМЫ РАБОТЫ
# ============================================================
def run_login(auto):
    """Только логин."""
    auto.start()
    try:
        auto.login()
    finally:
        auto.stop()


def run_main(auto, start_idx=None):
    """Основной режим — обработка дискографии."""
    # Загружаем треки
    if not os.path.exists(DISCOGRAPHY_FILE):
        log(f"❌ Файл не найден: {DISCOGRAPHY_FILE}")
        return

    with open(DISCOGRAPHY_FILE, 'r', encoding='utf-8-sig') as f:
        tracks = json.load(f)

    log(f"📂 Загружено {len(tracks)} треков из дискографии")

    # Загружаем прогресс
    progress = load_progress()
    if start_idx is not None:
        progress['current_idx'] = start_idx
    current_idx = progress.get('current_idx', 0)

    if current_idx >= len(tracks):
        log(f"✅ Все {len(tracks)} треков уже обработаны!")
        return

    log(f"▶️ Продолжаю с позиции {current_idx + 1}/{len(tracks)}")
    log(f"   Статистика: found={progress.get('found', 0)}, "
        f"not_found={progress.get('not_found', 0)}, "
        f"liked={progress.get('liked', 0)}, "
        f"skipped={progress.get('skipped', 0)}")

    auto.start()
    try:
        # Проверяем логин (пропускаем для Chrome — уже залогинен)
        if auto.use_chrome:
            log("🌐 Chrome профиль — пропускаем проверку логина")
        else:
            if not auto._is_logged_in():
                log("🔐 Не залогинен! Запусти с --login сначала.")
                return
            log("✅ Залогинен в Spotify")


        for i in range(current_idx, len(tracks)):
            track = tracks[i]
            chrono_idx = track.get('chronological_index', i + 1)
            artist = track['artist']
            title = track['title']
            source = track.get('source', '?')

            log(f"\n[{i + 1}/{len(tracks)}] #{chrono_idx} ({source}) {artist} — {title}")

            result = auto.search_and_like(artist, title, chrono_idx)

            if result == 'liked':
                progress['liked'] = progress.get('liked', 0) + 1
                progress['found'] = progress.get('found', 0) + 1
            elif result == 'already_liked':
                progress['skipped'] = progress.get('skipped', 0) + 1
                progress['found'] = progress.get('found', 0) + 1
            elif result == 'not_found':
                progress['not_found'] = progress.get('not_found', 0) + 1
            elif result == 'error':
                pass  # ошибки логируются в лог-файл

            progress['current_idx'] = i + 1
            progress['last_updated'] = datetime.now().isoformat()
            save_progress(progress)

            sleep(BETWEEN_TRACKS)

        log(f"\n🎉 Все треки обработаны!")

    except KeyboardInterrupt:
        log(f"\n⏸️ Остановлено пользователем на позиции {progress.get('current_idx', 0)}")
        save_progress(progress)
    finally:
        auto.stop()


def run_not_found(auto, start_idx=None):
    """Обработка только not_found треков."""
    if not os.path.exists(NOT_FOUND_FILE):
        log(f"❌ Файл не найден: {NOT_FOUND_FILE}")
        return

    with open(NOT_FOUND_FILE, 'r', encoding='utf-8') as f:
        tracks = json.load(f)

    log(f"📂 Загружено {len(tracks)} not_found треков")

    # Отдельный прогресс для not_found
    nf_progress_file = os.path.join(SCRIPT_DIR, 'progress_not_found.json')
    if os.path.exists(nf_progress_file):
        with open(nf_progress_file, 'r', encoding='utf-8') as f:
            nf_progress = json.load(f)
    else:
        nf_progress = {'current_idx': 0, 'found': 0, 'still_not_found': 0}

    if start_idx is not None:
        nf_progress['current_idx'] = start_idx

    current_idx = nf_progress.get('current_idx', 0)
    if current_idx >= len(tracks):
        log(f"✅ Все not_found треки уже обработаны!")
        return

    log(f"▶️ Продолжаю с позиции {current_idx + 1}/{len(tracks)}")

    auto.start()
    try:
        if auto.use_chrome:
            log("🌐 Chrome профиль — пропускаем проверку логина")
        else:
            if not auto._is_logged_in():
                log("🔐 Не залогинен! Запусти с --login сначала.")
                return
            log("✅ Залогинен в Spotify")

        still_not_found = []

        for i in range(current_idx, len(tracks)):
            track = tracks[i]
            artist = track['artist']
            title = track['title']
            chrono_idx = track.get('chronological_index', '?')

            log(f"\n[{i + 1}/{len(tracks)}] #{chrono_idx} {artist} — {title}")

            result = auto.search_and_like(artist, title, chrono_idx)

            if result == 'liked':
                nf_progress['found'] = nf_progress.get('found', 0) + 1
            elif result == 'already_liked':
                nf_progress['found'] = nf_progress.get('found', 0) + 1
            elif result in ('not_found', 'error'):
                nf_progress['still_not_found'] = nf_progress.get('still_not_found', 0) + 1
                still_not_found.append(track)

            nf_progress['current_idx'] = i + 1
            nf_progress['last_updated'] = datetime.now().isoformat()

            with open(nf_progress_file, 'w', encoding='utf-8') as f:
                json.dump(nf_progress, f, ensure_ascii=False, indent=2)

            sleep(BETWEEN_TRACKS)

        # Сохраняем оставшиеся not_found
        if still_not_found:
            still_nf_file = os.path.join(SCRIPT_DIR, 'still_not_found.json')
            with open(still_nf_file, 'w', encoding='utf-8') as f:
                json.dump(still_not_found, f, ensure_ascii=False, indent=2)
            log(f"\n📝 {len(still_not_found)} треков всё ещё не найдены → still_not_found.json")

        log(f"\n🎉 Обработка not_found завершена!")
        log(f"   Найдено: {nf_progress.get('found', 0)}")
        log(f"   Не найдено: {nf_progress.get('still_not_found', 0)}")

    except KeyboardInterrupt:
        log(f"\n⏸️ Остановлено пользователем")
    finally:
        auto.stop()



# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Spotify Browser Automation')
    parser.add_argument('--login', action='store_true', help='Только логин (сохранить сессию)')
    parser.add_argument('--start', type=int, default=None, help='Начать с конкретного индекса (0-based)')
    parser.add_argument('--not-found', action='store_true', help='Обработать только not_found треки')
    parser.add_argument('--headless', action='store_true', help='Запуск без GUI (не рекомендуется)')
    parser.add_argument('--chrome', action='store_true', help='Использовать профиль Google Chrome (не нужен --login)')
    args = parser.parse_args()

    auto = SpotifyAutomation(headless=args.headless, use_chrome=args.chrome)

    if args.login:
        run_login(auto)
    elif args.not_found:
        run_not_found(auto, start_idx=args.start)
    else:
        run_main(auto, start_idx=args.start)


if __name__ == '__main__':
    main()
