#!/usr/bin/env python
"""Маленькая брендовая плашка для конца ролика (название проекта + куда идти).

Это не полноэкранный CTA из `cta/` — тот перекрывает кадр и годится только
как отдельный экран. Здесь нужна плашка, которая живёт поверх говорящего последние
10-20 секунд: длинный оффер в такой размер не влезает читаемым, поэтому
на ней только название проекта и куда идти. Тексты, цвет и монограмма по умолчанию —
из channel/brand.json (cta_title, cta_sub, accent_color, monogram).

Ширина PNG подобрана под кадр 1920: в графе плашка масштабируется до 40% ширины
кадра, то есть на горизонтальном лонге отдаётся пиксель в пиксель.

Usage:
    python make_endplate.py temp/myclip_endplate.png
    python make_endplate.py temp/x.png --title "БЕСПЛАТНЫЙ УРОК" --sub "ССЫЛКА В ОПИСАНИИ"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand
from common import log, resolve_repo_path

BRAND = brand.load()
FONT_PATH = brand.font_path(BRAND)
ACCENT = brand.rgb(BRAND["accent_color"])
WHITE = brand.rgb(BRAND["text_color"])

PLATE_W, PLATE_H = 768, 176
PAD = 22
BAR_W = 10
TITLE_SIZE = 54
SUB_SIZE = 27


def _font(size):
    from PIL import ImageFont
    if os.path.isfile(FONT_PATH):
        return ImageFont.truetype(FONT_PATH, size)
    return ImageFont.load_default()


def layout(width=PLATE_W, height=PLATE_H):
    """Геометрия плашки: монограмма слева, два текстовых ряда справа от неё.

    Возвращает словарь, чтобы раскладку можно было проверить тестом, не рисуя PNG.
    """
    mono = height - PAD * 2
    mono_x = BAR_W + PAD
    text_x = mono_x + mono + PAD
    return {
        "monogram": (mono_x, PAD, mono),
        "text_x": text_x,
        "text_w": width - text_x - PAD,
        "title_y": int(height * 0.28),
        "sub_y": int(height * 0.66),
    }


def fit_size(text, size, max_width, min_size=20):
    """Самый крупный кегль не больше size, при котором строка влезает в max_width.

    Без подбора длинный заголовок («БЕСПЛАТНЫЙ УРОК», «ВЫЙДИ ИЗ МАТРИЦЫ») при кегле 54
    уходил за правый край плашки и обрезался на полуслове.
    """
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    while size > min_size and draw.textlength(text, font=_font(size)) > max_width:
        size -= 1
    return size


def compose(title=None, sub=None, width=PLATE_W, height=PLATE_H):
    from PIL import Image, ImageDraw

    title = title or BRAND["cta_title"]
    sub = sub or BRAND["cta_sub"]
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    # не полностью непрозрачная: сквозь плашку должно быть видно, что за ней кадр,
    # иначе она читается как вставленный экран, а не как подпись поверх видео
    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=14,
                           fill=(10, 10, 10, 232), outline=(60, 60, 60, 220), width=2)
    draw.rounded_rectangle([0, 0, BAR_W * 2, height - 1], radius=14, fill=ACCENT)
    draw.rectangle([BAR_W, 0, BAR_W * 2, height - 1], fill=(10, 10, 10, 232))

    geo = layout(width, height)
    mx, my, msize = geo["monogram"]
    draw.ellipse([mx, my, mx + msize, my + msize], fill=(10, 10, 10, 255),
                 outline=ACCENT, width=4)
    if BRAND["monogram"]:
        draw.text((mx + msize / 2, my + msize / 2), BRAND["monogram"],
                  font=_font(int(msize * 0.44)), fill=WHITE, anchor="mm")

    title, sub = title.upper(), sub.upper()
    draw.text((geo["text_x"], geo["title_y"]), title,
              font=_font(fit_size(title, TITLE_SIZE, geo["text_w"])), fill=WHITE, anchor="lm")
    draw.text((geo["text_x"], geo["sub_y"]), sub,
              font=_font(fit_size(sub, SUB_SIZE, geo["text_w"], min_size=14)), fill=ACCENT, anchor="lm")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out")
    ap.add_argument("--title", help="по умолчанию cta_title из brand.json")
    ap.add_argument("--sub", help="по умолчанию cta_sub из brand.json")
    args = ap.parse_args()

    out = resolve_repo_path(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    compose(args.title, args.sub).save(out)
    log(f"плашка -> {out}")


if __name__ == "__main__":
    main()
