"""Бренд канала: монограмма, цвета, тексты плашек, имена спикеров.

Все графические скрипты (обложка, плашки, карточки, субтитры) берут фирменные
элементы отсюда, а не из констант в коде. Источник — `channel/brand.json`
(его заполняет команда /setup-channel или человек руками). Нет файла или поля —
действует значение из DEFAULTS, поэтому пайплайн работает и без настройки.

Путь можно переопределить переменной окружения NAREZCHIK_BRAND — удобно,
если на одной копии репозитория ведётся несколько каналов.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT  # noqa: E402

BRAND_PATH = os.environ.get("NAREZCHIK_BRAND") or os.path.join(ROOT, "channel", "brand.json")

DEFAULTS = {
    "channel_name": "",
    # 1–3 символа в круглом значке на обложке и плашке; пустая строка — значка нет
    "monogram": "",
    # главный акцент: первая строка заголовка обложки, акцентные слова субтитров,
    # полоса на плашке, угловой шеврон
    "accent_color": "#E11111",
    # второй цвет: подпись спикера и крупные текстовые акценты в субтитрах
    "highlight_color": "#FFD944",
    "text_color": "#FFFFFF",
    "cta_title": "ПОДПИШИСЬ",
    "cta_sub": "ССЫЛКА В ОПИСАНИИ",
    "tail_title": "СМОТРИ ДАЛЬШЕ",
    "speaker_names": ["СПИКЕР", "ГОСТЬ"],
    "cover_chevron": True,
    # файл шрифта в assets/fonts/ и имя его семейства (нужно libass для субтитров)
    "font_file": "Montserrat-ExtraBold.ttf",
    "font_name": "Montserrat ExtraBold",
}

_cache = {}


def load(path=None):
    """Бренд с подставленными значениями по умолчанию. Кэшируется по пути."""
    path = path or BRAND_PATH
    if path in _cache:
        return _cache[path]
    data = dict(DEFAULTS)
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as f:
            user = json.load(f)
        data.update({k: v for k, v in user.items() if v is not None and not k.startswith("_")})
    _cache[path] = data
    return data


def rgb(hex_color):
    """'#E11111' -> (225, 17, 17)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ass_colour(hex_color, alpha="00"):
    """'#E11111' -> '&H001111E1' — ASS хранит цвет как AABBGGRR."""
    r, g, b = rgb(hex_color)
    return f"&H{alpha}{b:02X}{g:02X}{r:02X}"


def font_path(brand=None):
    brand = brand or load()
    return os.path.join(ROOT, "assets", "fonts", brand["font_file"])
