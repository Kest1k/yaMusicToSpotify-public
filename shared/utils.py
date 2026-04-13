"""
Общие утилиты для работы с треками: нормализация, транслитерация, fuzzy matching.

Используется во всех модулях проекта:
  - run_all.py (пайплайн VK → Spotify)
  - browser_import/main.py (Playwright автоматизация)
  - step*.py (отдельные шаги пайплайна)
  - importer.py, scout.py и др.
"""

import re
import unicodedata
from difflib import SequenceMatcher


# ============================================================
# ТРАНСЛИТЕРАЦИЯ
# ============================================================
TRANSLIT = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh',
    'з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o',
    'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
    'ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
}


def transliterate(text):
    """Транслитерирует кириллицу → латиницу."""
    result = []
    for ch in text:
        low = ch.lower()
        if low in TRANSLIT:
            tr = TRANSLIT[low]
            result.append(tr.upper() if ch.isupper() else tr)
        else:
            result.append(ch)
    return ''.join(result)


def has_cyrillic(text):
    """Проверяет наличие кириллицы в тексте."""
    return bool(re.search('[а-яА-ЯёЁ]', text))


# Ручной маппинг для символов, которые NFKD не раскладывает на base+combining
# (скандинавские ø/Ø, немецкие ß, польские ł и т.д.)
_SPECIAL_DIACRITICS = {
    'ø': 'o', 'Ø': 'O',
    'ß': 'ss',
    'ł': 'l', 'Ł': 'L',
    'đ': 'd', 'Đ': 'D',
    'æ': 'ae', 'Æ': 'AE',
    'œ': 'oe', 'Œ': 'OE',
    'þ': 'th', 'Þ': 'TH',
}


def strip_diacritics(text):
    """
    Убирает диакритику: ü→u, é→e, ø→o, ß→ss и т.д.
    Использует Unicode NFKD decomposition + ручной маппинг для особых случаев.
    НЕ трогает кириллицу (ё обрабатывается отдельно в normalize_text).
    """
    # Сначала ручной маппинг для символов, которые NFKD не раскладывает
    result = []
    for ch in text:
        if ch in _SPECIAL_DIACRITICS:
            result.append(_SPECIAL_DIACRITICS[ch])
        else:
            result.append(ch)
    text = ''.join(result)
    # NFKD decomposition: ü → u + combining diaeresis, é → e + combining acute
    nfkd = unicodedata.normalize('NFKD', text)
    # Убираем combining marks (категория 'M'), но НЕ трогаем кириллицу
    return ''.join(ch for ch in nfkd if unicodedata.category(ch) != 'Mn')


# ============================================================
# ОЧИСТКА И ПОИСКОВЫЕ ЗАПРОСЫ
# ============================================================
def clean_for_search(text):
    """Очищает текст для поискового запроса в Spotify."""
    s = text
    # Убираем номер трека в начале (01., 03., 12 -, 1. и т.д.)
    s = re.sub(r'^\s*\d{1,3}[\.\)\-]\s*', '', s)
    # Убираем типичный мусор из VK названий
    s = re.sub(r'\.mp3\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\[.*?\]', '', s)  # [HD Processing], [320] и т.д.
    # Убираем мусорные скобки: (version), (prod), (Original Mix), (Radio Edit) и т.д.
    # НО СОХРАНЯЕМ ремиксы с именами! (Akira Kiteshi Remix) — оставляем
    s = re.sub(r'\(.*?(?:version|prod).*?\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\(\s*original\s+mix\s*\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\(\s*radio\s+edit\s*\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\(\s*extended\s+mix\s*\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\(\s*club\s+mix\s*\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\(\s*bonus\s+track\s*\)', '', s, flags=re.IGNORECASE)
    # Убираем суффиксы через тире/дефис: "- Original Mix", "- Radio Edit", "- Extended Mix" и т.д.
    s = re.sub(r'\s*[-–—]\s*original\s+mix\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*[-–—]\s*radio\s+edit\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*[-–—]\s*extended\s+mix\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*[-–—]\s*club\s+mix\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*[-–—]\s*bonus\s+track\s*$', '', s, flags=re.IGNORECASE)
    # Оставляем ремиксы в названии, но убираем мусор
    s = re.sub(r'\bfeat(?:uring)?\.?\s+', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bft\.?\s+', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bфит\s+', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bprod\.?\s*by\s+', ' ', s, flags=re.IGNORECASE)
    # Оставляем только буквы (любых алфавитов), цифры, пробелы и апострофы (для Cooper's и т.д.)
    # Всё остальное (♬►«»""●·&()[]#+ и любой другой мусор) → пробел
    s = re.sub(r"[^\w\s']", ' ', s)
    s = re.sub(r'_', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def build_search_query(artist, title):
    """Строит поисковый запрос для Spotify Web."""
    # Чистим артиста
    clean_artist = clean_for_search(artist)
    # Убираем feat из артиста
    clean_artist = re.sub(r'\s*(feat\.?|ft\.?)\s+.*$', '', clean_artist, flags=re.IGNORECASE).strip()
    # Берём первого артиста если несколько
    first_artist = re.split(r'[,&]|\band\b|\bи\b|\bvs\.?\b', clean_artist, flags=re.IGNORECASE)[0].strip()

    # Чистим название
    clean_title = clean_for_search(title)
    # Убираем " - " в начале (бывает "- Waking Life")
    clean_title = re.sub(r'^-\s*', '', clean_title).strip()

    query = f"{first_artist} {clean_title}".strip()
    # Ограничиваем длину
    if len(query) > 80:
        query = query[:80]
    return query


# ============================================================
# НОРМАЛИЗАЦИЯ ДЛЯ FUZZY MATCHING
# ============================================================

# Маппинг визуально похожих латинских ↔ кириллических символов → единая форма (кириллица)
LOOKALIKE_MAP = {
    'a': 'а', 'b': 'в', 'c': 'с', 'e': 'е', 'h': 'н', 'k': 'к',
    'm': 'м', 'o': 'о', 'p': 'р', 't': 'т', 'x': 'х', 'y': 'у',
}

# Слова-мусор, которые можно игнорировать при сравнении названий
NOISE_WORDS = {
    'скит', 'skit', 'intro', 'outro', 'interlude',
    'original', 'mix', 'version', 'edit', 'remaster', 'remastered',
    'bonus', 'track', 'deluxe', 'explicit', 'clean',
}

# Слова-мусор в именах артистов
ARTIST_NOISE = {'не', 'the', 'dj', 'mc', 'feat', 'ft', 'фит', 'при', 'уч', 'aka'}

# Известные алиасы артистов (все ключи и значения — lowercase)
# Формат: 'alias' → {'canonical1', 'canonical2', ...}
# При сравнении проверяем обе стороны через этот маппинг
_ARTIST_ALIASES_RAW = {
    'ак47': {'витя ак', 'vitya ak'},
    'витя ак': {'ак47'},
    'vitya ak': {'ак47'},
    'ноггано': {'noggano', 'баста'},
    'noggano': {'ноггано', 'баста'},
    'баста': {'ноггано', 'noggano', 'basta'},
    'гуф': {'guf'},
    'guf': {'гуф'},
    'oxxxymiron': {'оксимирон', 'oxxxymiron'},
    'оксимирон': {'oxxxymiron'},
    'тбили': {'тбили теплый', 'tbili'},
    'тбили теплый': {'тбили', 'tbili'},
    'рэм дигга': {'r.e.m. digga', 'rem digga'},
    'кто там': {'кто там?'},
    'кто там?': {'кто там'},
    'гамора': {'gamora'},
    'lmfao': {'lmfao'},
    'crystal castles': {'crystal castles'},
}

def _build_alias_lookup():
    """Строит двусторонний lookup: normalized_name → set of all aliases."""
    lookup = {}
    for key, aliases in _ARTIST_ALIASES_RAW.items():
        all_names = {key} | aliases
        for name in all_names:
            if name not in lookup:
                lookup[name] = set()
            lookup[name].update(all_names - {name})
    return lookup

ARTIST_ALIASES = _build_alias_lookup()


def normalize_text(s):
    """
    Глубокая нормализация текста для fuzzy-сравнения:
    - lower case
    - ё → е
    - убираем [ВАРИАНТЫ] и подобные суффиксы в квадратных скобках
    - убираем апострофы (4'K → 4K)
    - нормализуем визуально похожие лат/кир символы (K→К, A→А и т.д.)
    - убираем пунктуацию, скобки, тире, кавычки
    - схлопываем пробелы
    """
    s = s.lower().strip()
    s = s.replace('ё', 'е')
    # Убираем диакритику (ü→u, é→e, ø→o и т.д.)
    s = strip_diacritics(s)
    # Убираем содержимое квадратных скобок ([ВАРИАНТЫ], [320], [HD] и т.д.)
    s = re.sub(r'\[.*?\]', ' ', s)
    # Убираем апострофы (4'K → 4K, don't → dont)
    s = s.replace("'", '').replace('\u2019', '').replace('\u2018', '')
    # Убираем звёздочки-цензуру (О*уенный → Оуенный, f**k → fk)
    s = s.replace('*', '')
    # Нормализуем визуально похожие латинские → кириллические
    # (только если слово содержит смесь или полностью латинское в кириллическом контексте)
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'_', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def unify_lookalikes(s):
    """
    Приводит визуально похожие латинские буквы к кириллическим аналогам.
    Используется для сравнения имён типа 4'K vs 4К, ASYS vs A*S*Y*S.
    """
    return ''.join(LOOKALIKE_MAP.get(ch, ch) for ch in s)


def extract_words(s):
    """Извлекает множество значимых слов из нормализованного текста.
    Сохраняет однобуквенные слова если они цифры (важно для 'Часть 2', 'Vol 3')."""
    words = set()
    for w in normalize_text(s).split():
        if len(w) > 1:
            words.add(w)
        elif w.isdigit():
            words.add(w)
    return words


def extract_all_words(text):
    """Извлекает ВСЕ слова из текста (включая содержимое скобок, через тире и т.д.).
    Сохраняет однобуквенные цифры."""
    words = set()
    for w in normalize_text(text).split():
        if len(w) > 1:
            words.add(w)
        elif w.isdigit():
            words.add(w)
    return words


def split_artists(artist_str):
    """Разбивает строку артистов на список отдельных имён.
    Сначала сплитим по разделителям, потом нормализуем каждое имя."""
    # Сплитим ДО нормализации, чтобы не потерять запятые/амперсанды
    parts = re.split(r'\s*(?:,|&|\band\b|\bи\b|\bvs\.?\b|\bfeat\.?\b|\bft\.?\b|\bфит\b)\s*',
                     artist_str, flags=re.IGNORECASE)
    result = []
    for p in parts:
        norm = normalize_text(p)
        if norm:
            result.append(norm)
    return result


def words_overlap_ratio(words_a, words_b):
    """Доля совпадающих слов (Jaccard-подобная метрика, от меньшего множества)."""
    if not words_a or not words_b:
        return 0.0
    common = words_a & words_b
    smaller = min(len(words_a), len(words_b))
    return len(common) / smaller if smaller > 0 else 0.0


def has_remix_mismatch(expected_title, found_title):
    """
    Проверяет конфликт ремиксов:
    - Если в ожидаемом есть конкретный ремикс, а в найденном другой — это mismatch
    - Если в ожидаемом нет ремикса, а в найденном "Original Mix" — это ОК
    """

    def extract_remix_info(title):
        """Извлекает название ремикса из заголовка."""
        bracket_match = re.findall(r'[\(\[]([^)\]]*(?:remix|rmx|mix|edit|rework|bootleg|dub)[^)\]]*)[)\]]',
                                    title, re.IGNORECASE)
        dash_match = re.findall(r'[-–—]\s*([^-–—]*(?:remix|rmx|mix|edit|rework|bootleg|dub)[^-–—]*)',
                                 title, re.IGNORECASE)
        all_matches = bracket_match + dash_match
        return [normalize_text(m) for m in all_matches]

    expected_remixes = extract_remix_info(expected_title)
    found_remixes = extract_remix_info(found_title)

    # Оба без ремиксов — ОК
    if not expected_remixes and not found_remixes:
        return False

    # Ожидаемый без ремикса, найденный с "Original Mix" — ОК
    if not expected_remixes and found_remixes:
        for r in found_remixes:
            if 'original' in r:
                return False
        return True

    # Ожидаемый с ремиксом, найденный без — mismatch
    # НО: если ожидаемый содержит только "Original Mix" — это НЕ mismatch (тот же трек)
    if expected_remixes and not found_remixes:
        all_original = all('original' in r for r in expected_remixes)
        if all_original:
            return False  # "(Original Mix)" vs без суффикса — ОК
        return True

    # Оба с ремиксами — сравниваем слова ремикса
    exp_words = set()
    for r in expected_remixes:
        exp_words.update(r.split())
    found_words = set()
    for r in found_remixes:
        found_words.update(r.split())

    noise = {'remix', 'rmx', 'mix', 'edit', 'rework', 'bootleg', 'dub', 'original'}
    exp_clean = exp_words - noise
    found_clean = found_words - noise

    if not exp_clean and not found_clean:
        return False

    if not exp_clean or not found_clean:
        return True

    overlap = exp_clean & found_clean
    if len(overlap) > 0:
        return False  # есть общие слова — ремиксы совпадают

    # Нет пословного совпадения — пробуем no-space сравнение
    # (PeaceTreaty vs Peace Treaty, DeadMau5 vs Dead Mau5 и т.д.)
    exp_joined = ''.join(sorted(exp_clean))
    found_joined = ''.join(sorted(found_clean))
    if exp_joined == found_joined:
        return False  # склеенные слова совпали — не mismatch

    # Fallback: схлопываем удвоенные буквы (Trentemoller vs Trentemellor)
    def _collapse_doubles(s):
        return re.sub(r'(.)\1+', r'\1', s)

    if _collapse_doubles(exp_joined) == _collapse_doubles(found_joined):
        return False

    return True


def fuzzy_match(expected_artist, expected_title, found_artist, found_title):
    """
    Умное сравнение ожидаемого и найденного трека.

    Обрабатывает:
    - ё/е, регистр, пунктуация (? ! . ,)
    - перестановка слов в названии
    - скобки и тире как разделители подназваний
    - "Скит" / "Skit" как часть названия в разных местах
    - feat/ft артисты
    - ремиксы (Akira Kiteshi Remix vs Original Mix)
    - частичное совпадение при длинных названиях

    Возвращает True если трек считается совпавшим.
    """
    # === АРТИСТ ===
    expected_artists = split_artists(expected_artist)
    found_artists = split_artists(found_artist)

    artist_ok = False

    def _clean_artist_words(name):
        """Убирает мусорные слова из имени артиста для сравнения."""
        words = set(name.split())
        meaningful = words - ARTIST_NOISE
        return meaningful if meaningful else words

    def _collapse_doubles(s):
        """Схлопывает удвоенные буквы: ll→l, ss→s, ee→e и т.д."""
        return re.sub(r'(.)\1+', r'\1', s)

    def _artists_match(a, b):
        """Проверяет совпадение двух имён артистов."""
        if a == b:
            return True
        if a in b or b in a:
            return True
        # Проверяем алиасы (АК47 ↔ Витя АК, Ноггано ↔ Noggano и т.д.)
        a_aliases = ARTIST_ALIASES.get(a, set())
        if b in a_aliases:
            return True
        b_aliases = ARTIST_ALIASES.get(b, set())
        if a in b_aliases:
            return True
        # Схлопывание удвоенных букв (Trentemoller vs Trentemellor)
        a_col = _collapse_doubles(a)
        b_col = _collapse_doubles(b)
        if a_col == b_col:
            return True
        if a_col in b_col or b_col in a_col:
            return True
        # Lookalike-нормализация (K↔К, A↔А и т.д.)
        a_uni = unify_lookalikes(a)
        b_uni = unify_lookalikes(b)
        if a_uni == b_uni:
            return True
        if a_uni in b_uni or b_uni in a_uni:
            return True
        # Транслитерация
        a_tr = normalize_text(transliterate(a))
        b_tr = normalize_text(transliterate(b))
        if a_tr == b_tr:
            return True
        if a_tr in b_tr or b_tr in a_tr:
            return True
        # Транслитерация + алиасы (проверяем транслит алиасов)
        for alias in a_aliases:
            alias_tr = normalize_text(transliterate(alias))
            if alias_tr == b_tr or alias_tr in b_tr or b_tr in alias_tr:
                return True
        for alias in b_aliases:
            alias_tr = normalize_text(transliterate(alias))
            if alias_tr == a_tr or alias_tr in a_tr or a_tr in alias_tr:
                return True
        # Транслитерация + схлопывание
        if _collapse_doubles(a_tr) == _collapse_doubles(b_tr):
            return True
        # Nospace-сравнение (Obe 1 Kanobe vs Obe1kanobe, Lesha Kenny vs leshakenny)
        a_nospace = a.replace(' ', '')
        b_nospace = b.replace(' ', '')
        if a_nospace == b_nospace:
            return True
        if a_nospace in b_nospace or b_nospace in a_nospace:
            return True
        # Nospace + collapse doubles
        if _collapse_doubles(a_nospace) == _collapse_doubles(b_nospace):
            return True
        # Nospace + транслитерация
        a_tr_nospace = a_tr.replace(' ', '')
        b_tr_nospace = b_tr.replace(' ', '')
        if a_tr_nospace == b_tr_nospace:
            return True
        if a_tr_nospace in b_tr_nospace or b_tr_nospace in a_tr_nospace:
            return True
        if _collapse_doubles(a_tr_nospace) == _collapse_doubles(b_tr_nospace):
            return True
        # Fuzzy ratio для коротких имён (компания vs кампания, 1 буква разницы)
        # Порог 0.82 = допускаем ~1 ошибку на 6 символов
        if len(a) >= 4 and len(b) >= 4:
            ratio = SequenceMatcher(None, a_nospace, b_nospace).ratio()
            if ratio >= 0.82:
                return True
            # То же для транслитерированных
            ratio_tr = SequenceMatcher(None, a_tr_nospace, b_tr_nospace).ratio()
            if ratio_tr >= 0.82:
                return True
        # Пословное сравнение по значимым словам
        a_words = _clean_artist_words(a)
        b_words = _clean_artist_words(b)
        if not a_words or not b_words:
            return False
        common = a_words & b_words
        smaller = min(len(a_words), len(b_words))
        if smaller <= 2:
            return common == a_words or common == b_words
        ratio = len(common) / smaller
        return ratio >= 0.6

    if expected_artists and found_artists:
        first_exp = expected_artists[0]
        first_found = found_artists[0]

        if _artists_match(first_exp, first_found):
            artist_ok = True

        if not artist_ok:
            for ea in expected_artists:
                for fa in found_artists:
                    if _artists_match(ea, fa):
                        artist_ok = True
                        break
                if artist_ok:
                    break
    else:
        artist_ok = True

    # === РЕМИКСЫ ===
    remix_mismatch = has_remix_mismatch(expected_title, found_title)
    if remix_mismatch:
        return False

    # === НАЗВАНИЕ ===
    expected_words = extract_all_words(expected_title) - NOISE_WORDS
    found_words = extract_all_words(found_title) - NOISE_WORDS

    expected_all = extract_all_words(expected_artist + ' ' + expected_title) - NOISE_WORDS
    found_all = extract_all_words(found_artist + ' ' + found_title) - NOISE_WORDS

    # Транслитерация
    exp_title_tr = normalize_text(transliterate(expected_title))
    found_title_tr = normalize_text(transliterate(found_title))
    expected_words_tr = {w for w in exp_title_tr.split() if len(w) > 1 or w.isdigit()}
    found_words_tr = {w for w in found_title_tr.split() if len(w) > 1 or w.isdigit()}

    # Стратегия 1: прямое пословное совпадение названий
    if expected_words and found_words:
        title_ratio = words_overlap_ratio(expected_words, found_words)
    else:
        title_ratio = 1.0

    # Стратегия 1b: транслитерированное сравнение
    if expected_words_tr and found_words_tr:
        title_ratio_tr = words_overlap_ratio(expected_words_tr, found_words_tr)
    else:
        title_ratio_tr = 0.0
    title_ratio = max(title_ratio, title_ratio_tr)

    # Стратегия 2: все слова (артист + название) перекрёстно
    if expected_all and found_all:
        full_ratio = words_overlap_ratio(expected_all, found_all)
    else:
        full_ratio = 1.0

    # Стратегия 3: проверяем что ВСЕ ключевые слова ожидаемого есть в найденном
    if expected_words and found_words:
        expected_in_found = len(expected_words & found_words) / len(expected_words)
    else:
        expected_in_found = 0.0

    # Стратегия 3b: cross-field — слова из expected_title ищем в found_all (artist+title)
    # Это помогает когда feat-артист из VK title оказывается в found_artist
    if expected_words and found_all:
        expected_in_found_all = len(expected_words & found_all) / len(expected_words)
    else:
        expected_in_found_all = 0.0

    # --- Решение по названию ---
    title_ok = title_ratio >= 0.6
    if not title_ok and title_ratio >= 0.35 and full_ratio >= 0.7:
        title_ok = True
    # Cross-field: если слова из expected_title почти все есть в found_all (artist+title)
    if not title_ok and expected_in_found_all >= 0.75 and title_ratio >= 0.3:
        title_ok = True

    # Специальный случай: очень короткое название (1-2 значимых слова)
    if len(expected_words) <= 2 and expected_words:
        title_ok = expected_words.issubset(found_words) or expected_words.issubset(found_all)
        if not title_ok and expected_words_tr:
            title_ok = expected_words_tr.issubset(found_words_tr)

    # Специальный случай: среднее название (3-4 слова)
    elif len(expected_words) <= 4 and expected_words:
        title_ok = title_ratio >= 0.65 or (expected_in_found >= 0.75 and title_ratio >= 0.5)
        # Cross-field fallback для средних названий
        if not title_ok and expected_in_found_all >= 0.75:
            title_ok = True

    # Дополнительная проверка: если в названии есть числа, они ДОЛЖНЫ совпадать
    expected_numbers = {w for w in expected_words if w.isdigit()}
    found_numbers = {w for w in found_words if w.isdigit()}
    if expected_numbers and found_numbers:
        if expected_numbers != found_numbers:
            title_ok = False

    # Дополнительная проверка: нормализованные строки целиком
    if not title_ok:
        exp_norm = normalize_text(expected_title)
        found_norm = normalize_text(found_title)
        if exp_norm and found_norm:
            if exp_norm == found_norm or exp_norm in found_norm or found_norm in exp_norm:
                title_ok = True
            elif exp_title_tr == found_title_tr or exp_title_tr in found_title_tr or found_title_tr in exp_title_tr:
                title_ok = True

    # Дополнительная проверка: склеенные слова (Ruff House vs Ruffhouse)
    # Убираем пробелы и сравниваем
    if not title_ok:
        exp_nospace = normalize_text(expected_title).replace(' ', '')
        found_nospace = normalize_text(found_title).replace(' ', '')
        if exp_nospace and found_nospace:
            if exp_nospace == found_nospace or exp_nospace in found_nospace or found_nospace in exp_nospace:
                title_ok = True
            # Схлопывание удвоенных букв (Ilyushhin vs Ilyushin)
            elif _collapse_doubles(exp_nospace) == _collapse_doubles(found_nospace):
                title_ok = True
            else:
                # Транслитерация без пробелов
                exp_nospace_tr = exp_title_tr.replace(' ', '')
                found_nospace_tr = found_title_tr.replace(' ', '')
                if exp_nospace_tr == found_nospace_tr or exp_nospace_tr in found_nospace_tr or found_nospace_tr in exp_nospace_tr:
                    title_ok = True
                elif _collapse_doubles(exp_nospace_tr) == _collapse_doubles(found_nospace_tr):
                    title_ok = True

    return artist_ok and title_ok
