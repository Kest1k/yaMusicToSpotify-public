"""
Spotify Browser Automation — OPTIMIZED (search dropdown).

Вместо навигации на /search/... для каждого трека:
1. Открываем Spotify один раз
2. Кликаем в поле поиска
3. Вводим запрос → ждём dropdown с результатами
4. Жмём ⊕ (добавить) прямо в dropdown
5. Очищаем поле → вводим следующий запрос

Это в 3-5x быстрее оригинала (нет перезагрузки страницы).

Запуск:
  python main_optimized.py              — основной режим
  python main_optimized.py --chrome     — через Google Chrome
  python main_optimized.py --login      — только логин
  python main_optimized.py --start 691  — начать с конкретного индекса
  python main_optimized.py --not-found  — обработать только not_found треки
"""

import json
import os
import re
import sys
import argparse
import threading
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
LOG_FILE = os.path.join(LOG_DIR, f'optimized_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
NOT_FINDED_LOG = os.path.join(LOG_DIR, f'notFinded_opt_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

# ============================================================
# ПАУЗА (Ctrl+P / Enter в консоли)
# ============================================================
_pause_event = threading.Event()  # set = на паузе
_pause_event.clear()

def _pause_listener():
    """Фоновый поток: слушает Enter в консоли для toggle паузы."""
    while True:
        try:
            line = input()
            if _pause_event.is_set():
                _pause_event.clear()
                log("▶️  Продолжаю работу...")
            else:
                _pause_event.set()
                log("⏸️  ПАУЗА! Нажми Enter чтобы продолжить...")
        except EOFError:
            break

def start_pause_listener():
    """Запускает фоновый поток для паузы."""
    t = threading.Thread(target=_pause_listener, daemon=True)
    t.start()
    log("💡 Нажми Enter в консоли чтобы поставить на паузу/продолжить")

def check_pause():
    """Проверяет паузу. Если на паузе — блокирует до снятия."""
    while _pause_event.is_set():
        sleep(0.3)


# Таймауты (секунды)
SEARCH_DELAY = 1.5         # пауза после ввода (ждём dropdown)
BETWEEN_TRACKS = 0.8       # пауза между треками (быстрее — нет навигации)
LIKE_DELAY = 0.5            # пауза после нажатия лайка
DROPDOWN_TIMEOUT = 5000    # мс, таймаут ожидания dropdown
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
# ОСНОВНАЯ АВТОМАТИЗАЦИЯ (OPTIMIZED — DROPDOWN)
# ============================================================
class SpotifyAutomation:
    def __init__(self, headless=False, use_chrome=False):
        self.headless = headless
        self.use_chrome = use_chrome
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._search_ready = False  # флаг: мы уже на странице поиска

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

        log("  ⚠️ Chrome executable не найден, пробуем channel='chrome'")
        return None

    def start(self):
        """Запускает браузер с сохранённой сессией."""
        self.playwright = sync_playwright().start()

        if self.use_chrome:
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

        if self._is_logged_in():
            log("✅ Логин успешен! Сессия сохранена.")
            return True
        else:
            log("❌ Похоже, логин не удался. Попробуй ещё раз.")
            return False

    def _is_logged_in(self):
        """Проверяет залогинен ли пользователь. Сразу открывает /search чтобы не делать двойную навигацию."""
        try:
            self.page.goto('https://open.spotify.com/search', timeout=PAGE_LOAD_TIMEOUT)
            # Ждём полной загрузки DOM + сети
            try:
                self.page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                pass  # не критично, продолжаем
            sleep(2)
            login_btn = self.page.query_selector('[data-testid="login-button"]')
            if login_btn:
                return False
            # Ждём появления поля поиска (до 10 сек)
            search_input = None
            for attempt in range(5):
                search_input = self._get_search_input()
                if search_input:
                    break
                sleep(2)
            if search_input:
                search_input.click()
                sleep(0.3)
                self._search_ready = True
            return True
        except Exception:
            return False

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
        self._search_ready = False  # нужно заново перейти на поиск

    # ============================================================
    # SEARCH DROPDOWN FLOW (КЛЮЧЕВАЯ ОПТИМИЗАЦИЯ)
    # ============================================================
    def _ensure_search_page(self):
        """Убеждаемся что мы на странице поиска с активным полем ввода."""
        if self._search_ready:
            # Даже если флаг True — проверяем что поле поиска реально есть
            search_input = self._get_search_input()
            if search_input:
                return True
            # Поле пропало — сбрасываем и переходим заново
            log("  ⚠️ Поле поиска пропало, переоткрываю страницу поиска...")
            self._search_ready = False

        try:
            # Переходим на страницу поиска
            self.page.goto('https://open.spotify.com/search', timeout=PAGE_LOAD_TIMEOUT)
            sleep(2)

            # Ждём появления поля поиска (до 10 сек)
            search_input = None
            for attempt in range(5):
                search_input = self._get_search_input()
                if search_input:
                    break
                log(f"  ⚠️ Не нашёл поле поиска (попытка {attempt + 1}/5)...")
                sleep(2)

            if search_input:
                search_input.click()
                sleep(0.3)
                self._search_ready = True
                return True

            log("  ⚠️ Не нашёл поле поиска после 5 попыток!")
            return False
        except Exception as e:
            log(f"  ❌ Ошибка при переходе на поиск: {e}")
            return False

    def _get_search_input(self):
        """Находит поле ввода поиска."""
        selectors = [
            'input[data-testid="search-input"]',
            'input[role="searchbox"]',
            'input[placeholder*="Что хочешь"]',
            'input[placeholder*="What do you"]',
            'input[placeholder*="Поиск"]',
            'input[placeholder*="Search"]',
            'form[role="search"] input',
            '[data-testid="search-input"]',
        ]
        for selector in selectors:
            try:
                el = self.page.query_selector(selector)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        return None

    def _clear_search_input(self):
        """Очищает поле поиска."""
        search_input = self._get_search_input()
        if not search_input:
            return False

        try:
            search_input.click()
            sleep(0.2)
            # Ctrl+A → Delete для надёжной очистки
            self.page.keyboard.press('Control+a')
            sleep(0.1)
            self.page.keyboard.press('Backspace')
            sleep(0.3)
            return True
        except Exception as e:
            log(f"  ⚠️ Ошибка очистки поиска: {e}")
            return False

    def _type_search_query(self, query):
        """Вводит поисковый запрос и ждёт dropdown."""
        search_input = self._get_search_input()
        if not search_input:
            self._search_ready = False
            return False

        try:
            search_input.click()
            sleep(0.1)
            # Очищаем
            self.page.keyboard.press('Control+a')
            sleep(0.1)
            # Вводим новый запрос
            search_input.fill(query)
            sleep(SEARCH_DELAY)  # ждём dropdown
            return True
        except Exception as e:
            log(f"  ⚠️ Ошибка ввода запроса: {e}")
            self._search_ready = False
            return False

    def _find_dropdown_tracks(self, max_results=5):
        """
        Находит строки (треки/альбомы) в dropdown результатах поиска.
        Использует JS для нахождения dropdown-контейнера рядом с search input,
        чтобы не хватать ссылки из рекомендаций/недавних на странице.
        Возвращает список элементов (до max_results штук).
        """
        try:
            # JS: находим dropdown-контейнер и помечаем его data-атрибутом
            has_results = self.page.evaluate('''(maxResults) => {
                // Убираем старую метку
                const old = document.querySelector('[data-dropdown-container="true"]');
                if (old) old.removeAttribute('data-dropdown-container');

                // Находим search input
                const searchInput = document.querySelector('input[data-testid="search-input"]') ||
                                    document.querySelector('input[role="searchbox"]');
                if (!searchInput) return false;

                // Поднимаемся от search input, ищем контейнер с role="grid"/"list" и row-результатами
                let node = searchInput;
                for (let i = 0; i < 20; i++) {
                    if (!node.parentElement) break;
                    node = node.parentElement;

                    const grids = node.querySelectorAll('[role="grid"], [role="list"], [role="listbox"], [role="presentation"]');
                    for (const grid of grids) {
                        const rows = grid.querySelectorAll('[role="row"], [role="listitem"], [role="option"]');
                        let validCount = 0;
                        for (const row of rows) {
                            const rect = row.getBoundingClientRect();
                            if (rect.height === 0) continue;
                            const html = row.innerHTML;
                            if (html.includes('/track/') || html.includes('/album/')) {
                                validCount++;
                            }
                        }
                        if (validCount > 0) {
                            grid.setAttribute('data-dropdown-container', 'true');
                            return true;
                        }
                    }
                }
                return false;
            }''', max_results)

            if not has_results:
                return []

            # Берём row'ы только из помеченного контейнера
            container = self.page.query_selector('[data-dropdown-container="true"]')
            if not container:
                return []

            rows = container.query_selector_all('[role="row"], [role="listitem"], [role="option"]')
            result = []
            for el in rows:
                try:
                    if not el.is_visible():
                        continue
                    inner = el.inner_html() or ''
                    if '/track/' in inner or '/album/' in inner:
                        result.append(el)
                        if len(result) >= max_results:
                            break
                except Exception:
                    continue

            return result

        except Exception as e:
            log(f"  ⚠️ Ошибка поиска dropdown: {e}")
            return []

    def _extract_dropdown_track_info(self, element):
        """
        Извлекает artist и title из элемента dropdown (row).
        Работает ТОЛЬКО внутри переданного элемента, не поднимаясь по DOM.
        Возвращает (artist, title) или (None, None).
        """
        try:
            info = self.page.evaluate('''(el) => {
                // el — это row-элемент. Ищем данные ТОЛЬКО внутри него.
                const links = el.querySelectorAll('a');
                let trackName = '';
                let artistNames = [];
                
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    const text = link.textContent.trim();
                    if (!text) continue;
                    
                    if ((href.includes('/track/') || href.includes('/album/')) && !trackName) {
                        trackName = text;
                    }
                    if (href.includes('/artist/') && text) {
                        artistNames.push(text);
                    }
                }
                
                let artistName = artistNames.join(', ');

                // Fallback: ищем по subtitle/description внутри row
                if (!artistName) {
                    const subtitleCells = el.querySelectorAll('[id*="-subtitle"], [id*="-description"]');
                    for (const cell of subtitleCells) {
                        const text = cell.textContent.trim();
                        if (text && !['Трек', 'Song', 'E'].includes(text)) {
                            // Убираем префиксы типа "Трек • " или "Альбом • "
                            artistName = text.replace(/^.{0,20}[•·]\s*/, '').trim();
                            break;
                        }
                    }
                }

                // Fallback 2: если нет ссылок, берём из span-текстов внутри row
                if (!trackName || !artistName) {
                    const spans = el.querySelectorAll('span');
                    const texts = [];
                    for (const span of spans) {
                        const t = span.textContent.trim();
                        if (t && t.length > 1 && 
                            !['Трек', 'Song', 'E', 'Исполнитель', 'Artist', '•', '·',
                              'Альбом', 'Album', 'Подкаст', 'Podcast', 'Подписаться'].includes(t)) {
                            if (!texts.includes(t)) texts.push(t);
                        }
                    }
                    if (texts.length >= 2) {
                        if (!trackName) trackName = texts[0];
                        if (!artistName) artistName = texts[1];
                    } else if (texts.length === 1 && !trackName) {
                        trackName = texts[0];
                    }
                }

                return {
                    artist: artistName, 
                    title: trackName,
                };
            }''', element)

            if info and info.get('artist') and info.get('title'):
                return info['artist'], info['title']
            return None, None
        except Exception as e:
            log(f"      ⚠️ Ошибка извлечения инфо: {e}")
            return None, None

    def _click_dropdown_add_button(self, element):
        """
        Нажимает кнопку ⊕ (добавить) в dropdown элементе.
        Возвращает True если успешно.
        """
        try:
            # Наводим мышь на элемент чтобы появилась кнопка
            element.hover()
            sleep(0.3)

            # Ищем кнопку добавления в контексте элемента
            added = self.page.evaluate('''(el) => {
                // el — уже row-элемент, не нужно подниматься
                let root = el;

                // Ищем кнопку добавления
                const selectors = [
                    'button[data-testid="add-button"]',
                    'button[aria-label*="Add to"]',
                    'button[aria-label*="Добавить в"]',
                    'button[aria-label*="Save to"]',
                    'button[aria-label*="Сохранить в"]',
                    'button[aria-label*="Add to Liked"]',
                    'button[aria-label*="Добавить в любимые"]',
                ];

                for (const sel of selectors) {
                    const btn = root.querySelector(sel);
                    if (btn) {
                        // Проверяем что это не "уже добавлено" (checked)
                        const checked = btn.getAttribute('aria-checked');
                        if (checked === 'true') {
                            return 'already_liked';
                        }
                        btn.click();
                        return 'clicked';
                    }
                }

                // Fallback: ищем любую кнопку с иконкой + внутри
                const buttons = root.querySelectorAll('button');
                for (const btn of buttons) {
                    const label = btn.getAttribute('aria-label') || '';
                    const svg = btn.querySelector('svg');
                    if (svg && (label.toLowerCase().includes('add') ||
                                label.toLowerCase().includes('добавить') ||
                                label.toLowerCase().includes('save') ||
                                label.toLowerCase().includes('сохранить'))) {
                        btn.click();
                        return 'clicked';
                    }
                }

                return 'not_found';
            }''', element)

            if added == 'clicked':
                sleep(LIKE_DELAY)
                return 'liked'
            elif added == 'already_liked':
                return 'already_liked'
            else:
                return 'not_found'

        except Exception as e:
            log(f"    ⚠️ Ошибка при клике на ⊕: {e}")
            return 'error'

    def search_and_add(self, artist, title, chrono_idx):
        """
        ОПТИМИЗИРОВАННЫЙ поиск: через dropdown без навигации.
        Возвращает: 'liked', 'not_found', 'already_liked', 'error'
        """
        query = build_search_query(artist, title)
        log(f"  🔍 Поиск: '{query}'")

        try:
            # Убеждаемся что мы на странице поиска
            if not self._ensure_search_page():
                # Fallback: пробуем перезагрузить
                self._search_ready = False
                if not self._ensure_search_page():
                    return 'error'

            # Проверяем не разлогинило ли
            if self._check_logged_out():
                log("🔐 Сессия истекла! Жду ре-логин...")
                self._wait_for_relogin()

            # Вводим запрос
            if not self._type_search_query(query):
                log(f"  ❌ Не удалось ввести запрос")
                self._search_ready = False
                return 'error'

            # Ищем треки/альбомы в dropdown (до 5 результатов)
            track_elements = self._find_dropdown_tracks(max_results=5)
            if not track_elements:
                log(f"  ❌ Нет результатов для: {artist} — {title}")
                log_not_found(artist, title)
                return 'not_found'

            log(f"  📋 Найдено {len(track_elements)} результатов в dropdown")

            # Перебираем результаты — ищем совпадение
            best_el = None
            best_found_artist = None
            best_found_title = None
            last_found_artist = None
            last_found_title = None

            for idx, track_el in enumerate(track_elements):
                found_artist_i, found_title_i = self._extract_dropdown_track_info(track_el)
                if found_artist_i and found_title_i:
                    log(f"    [{idx + 1}] {found_artist_i} — {found_title_i}")
                    last_found_artist = found_artist_i
                    last_found_title = found_title_i

                    is_valid = fuzzy_match(artist, title, found_artist_i, found_title_i)
                    if is_valid:
                        best_el = track_el
                        best_found_artist = found_artist_i
                        best_found_title = found_title_i
                        log(f"    ✓ Совпадение найдено на позиции [{idx + 1}]!")
                        break
                else:
                    log(f"    [{idx + 1}] (не удалось извлечь инфо)")

            if not best_el:
                log(f"  ⚠️ Не совпадает! Ожидали: {artist} — {title}")
                log_not_found(artist, title, last_found_artist, last_found_title)
                return 'not_found'

            # Жмём ⊕ (добавить) на найденном совпадении
            result = self._click_dropdown_add_button(best_el)

            if result == 'liked':
                log(f"  ✅ Добавлен в лайки: {best_found_artist} — {best_found_title}")
                return 'liked'
            elif result == 'already_liked':
                log(f"  💚 Уже в лайках: {best_found_artist} — {best_found_title}")
                return 'already_liked'
            else:
                log(f"  ⚠️ Не удалось нажать ⊕")
                return 'error'

        except PlaywrightTimeout:
            log(f"  ⏰ Таймаут при поиске: {artist} — {title}")
            self._search_ready = False
            return 'error'
        except Exception as e:
            log(f"  ❌ Ошибка: {e}")
            self._search_ready = False
            return 'error'


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
    if not os.path.exists(DISCOGRAPHY_FILE):
        log(f"❌ Файл не найден: {DISCOGRAPHY_FILE}")
        return

    with open(DISCOGRAPHY_FILE, 'r', encoding='utf-8-sig') as f:
        tracks = json.load(f)

    log(f"📂 Загружено {len(tracks)} треков из дискографии")
    log(f"⚡ OPTIMIZED MODE — search dropdown (без навигации)")

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
        if auto.use_chrome:
            log("🌐 Chrome профиль — пропускаем проверку логина")
        else:
            if not auto._is_logged_in():
                log("🔐 Не залогинен! Запусти с --login сначала.")
                return
            log("✅ Залогинен в Spotify")

        start_pause_listener()

        for i in range(current_idx, len(tracks)):
            check_pause()  # проверяем паузу перед каждым треком

            track = tracks[i]
            chrono_idx = track.get('chronological_index', i + 1)
            artist = track['artist']
            title = track['title']
            source = track.get('source', '?')

            log(f"\n[{i + 1}/{len(tracks)}] #{chrono_idx} ({source}) {artist} — {title}")

            result = auto.search_and_add(artist, title, chrono_idx)

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
        log(f"\n⏸️ Остановлено пользователем (Ctrl+C) на позиции {progress.get('current_idx', 0)}")
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
    log(f"⚡ OPTIMIZED MODE — search dropdown")

    nf_progress_file = os.path.join(SCRIPT_DIR, 'progress_not_found_opt.json')
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

        start_pause_listener()
        still_not_found = []

        for i in range(current_idx, len(tracks)):
            check_pause()  # проверяем паузу перед каждым треком

            track = tracks[i]
            artist = track['artist']
            title = track['title']
            chrono_idx = track.get('chronological_index', '?')

            log(f"\n[{i + 1}/{len(tracks)}] #{chrono_idx} {artist} — {title}")

            result = auto.search_and_add(artist, title, chrono_idx)

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

        if still_not_found:
            still_nf_file = os.path.join(SCRIPT_DIR, 'still_not_found_opt.json')
            with open(still_nf_file, 'w', encoding='utf-8') as f:
                json.dump(still_not_found, f, ensure_ascii=False, indent=2)
            log(f"\n📝 {len(still_not_found)} треков всё ещё не найдены → still_not_found_opt.json")

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
    parser = argparse.ArgumentParser(description='Spotify Browser Automation — OPTIMIZED (dropdown)')
    parser.add_argument('--login', action='store_true', help='Только логин (сохранить сессию)')
    parser.add_argument('--start', type=int, default=None, help='Начать с конкретного индекса (0-based)')
    parser.add_argument('--not-found', action='store_true', help='Обработать только not_found треки')
    parser.add_argument('--headless', action='store_true', help='Запуск без GUI (не рекомендуется)')
    parser.add_argument('--chrome', action='store_true', help='Использовать профиль Google Chrome')
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
