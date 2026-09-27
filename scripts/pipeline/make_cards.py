#!/usr/bin/env python
"""Полноэкранные карточки и плашки лонга (раунды, счёт, пруфы, вопрос, топ-N) поверх фонов.

Фоны — без текста, файлы `assets/gfx/<имя>_{a,b}.jpg` (см. assets/gfx/README.md). Если
своего фона нет, рисуется запасной: тёмный градиент с отсветом цвета акцента из
channel/brand.json. Текст всегда кладётся здесь, шрифтом бренда: одна ошибка в русском
слове на сгенерированной картинке портит весь кадр.

Два вида результата:
- полноэкранные карточки 3840×2160 (round, final, question, proof, tail) — в графе идут
  оверлеем на весь кадр;
- плашки с альфой 1536 px шириной (score, plate) — в графе режим `plate` масштабирует их
  до 40% ширины кадра, на 4K-мастере это пиксель в пиксель. Прежние 768 px там
  растягивались вдвое и мылились.

Usage:
    python make_cards.py round  temp/x_round1.png --n 1 --title ИЗМЕНА
    python make_cards.py score  temp/x_score1.png --left 1 --right 0
    python make_cards.py final  temp/x_final.png  --left 3 --right 2
    python make_cards.py question temp/x_q.png --title "А ТЫ БЫ ПРОСТИЛ?" --sub "ПИШИ В КОММЕНТАРИЯХ"
    python make_cards.py proof  temp/x_proof.png --title "5%" --sub "мужчин получают 80% лайков" --source "Tinder, 2014"
    python make_cards.py plate  temp/x_plate.png --title "БЕСПЛАТНЫЙ УРОК" --sub "ССЫЛКА ПОД ВИДЕО"
    python make_cards.py qr cta/qr_plate.png --url https://t.me/your_bot --title "БЕСПЛАТНЫЙ УРОК"
    python make_cards.py question temp/x_qv.png --vertical --title "А ТЫ БЫ СМОГ?"   # 2160×3840 для Shorts

Горизонтальная карточка в вертикальном ролике обрезается по краям вместе с текстом —
для Shorts всегда --vertical (фон bg_<имя>_v_{a,b} или bg_short_v_{a,b}, иначе запасной).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, log, resolve_repo_path
from make_endplate import ACCENT, BRAND, WHITE, _font, fit_size

GFX = os.path.join(ROOT, "assets", "gfx")
FULL = (3840, 2160)
PLATE = (1536, 352)
NAMES = tuple(BRAND["speaker_names"][:2]) + ("",) * max(0, 2 - len(BRAND["speaker_names"]))


def _fallback_bg(size, variant):
    """Запасной фон: почти чёрный, с мягким отсветом акцента — снизу для «a», сверху для «b»."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    small = Image.new("RGB", (w // 8, h // 8), (10, 10, 12))
    d = ImageDraw.Draw(small)
    sw, sh = small.size
    cy = sh * (1.05 if variant == "a" else -0.05)
    r, g, b = ACCENT
    for i in range(12, 0, -1):
        k = i / 12
        rad = sw * 0.55 * k
        col = tuple(int(10 + (c - 10) * (1 - k) * 0.55) for c in (r, g, b))
        d.ellipse([sw / 2 - rad * 1.6, cy - rad, sw / 2 + rad * 1.6, cy + rad], fill=col)
    small = small.filter(ImageFilter.GaussianBlur(sw / 40))
    return small.resize(size).convert("RGBA")


def _bg(name, variant, size=None):
    from PIL import Image
    size = size or FULL
    names = [name] if size[0] >= size[1] else [f"{name}_v", "bg_short_v"]
    for n in names:
        for ext in ("jpg", "png"):
            path = os.path.join(GFX, f"{n}_{variant}.{ext}")
            if os.path.isfile(path):
                return Image.open(path).convert("RGBA").resize(size)
    return _fallback_bg(size, variant)


def _texture(name, variant, size, fallback):
    from PIL import Image
    path = os.path.join(GFX, f"{name}_{variant}.png")
    if os.path.isfile(path):
        return Image.open(path).convert("RGBA")
    return Image.new("RGBA", size, fallback)


def _text(draw, xy, text, size, fill=WHITE, anchor="mm", max_width=None):
    """Строка с мягкой тенью: фоны тёмные, но красные лучи местами подходят к тексту."""
    if max_width:
        size = fit_size(text, size, max_width, min_size=40)
    off = max(3, size // 40)
    draw.text((xy[0] + off, xy[1] + off), text, font=_font(size), fill=(0, 0, 0, 200), anchor=anchor)
    draw.text(xy, text, font=_font(size), fill=fill, anchor=anchor)


GOLD = (232, 178, 44)


def _text_block(draw, center, text, size, max_width, max_lines=3, fill=WHITE, min_size=60):
    """Текст по центру в несколько строк самым крупным кеглем, при котором он влезает.

    Одной строкой длинный вопрос ужимался под ширину кадра и в вертикали становился
    нечитаемым: «РАВНОПРАВИЕ — А КТО ГИБНЕТ В ОКОПАХ?» выходил кеглем подписи.
    """
    words = text.split()
    best = None
    for n in range(1, max_lines + 1):
        if n > len(words):
            break
        # делим слова на n строк примерно равной длины
        target = len(text) / n
        lines, cur = [], ""
        for w in words:
            probe = f"{cur} {w}".strip()
            if cur and len(probe) > target and len(lines) < n - 1:
                lines.append(cur)
                cur = w
            else:
                cur = probe
        lines.append(cur)
        s = min(fit_size(ln, size, max_width, min_size=min_size) for ln in lines)
        if best is None or s > best[0]:
            best = (s, lines)
    s, lines = best
    step = s * 1.12
    y0 = center[1] - step * (len(lines) - 1) / 2
    for i, ln in enumerate(lines):
        _text(draw, (center[0], y0 + i * step), ln, s, fill=fill)
    return s, len(lines)


def round_card(n, title, variant="a", label="РАУНД"):
    """label — префикс над темой: «РАУНД» для поединка, «ТОП-3» и т.п. для re-hook
    (n тогда не печатается, если label уже содержит цифру или n <= 0)."""
    from PIL import ImageDraw
    img = _bg("bg_round", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    head = f"{label} {n}" if n > 0 else label
    _text(d, (w / 2, h * 0.36), head, 190, fill=ACCENT)
    d.rectangle([w / 2 - 260, h * 0.445, w / 2 + 260, h * 0.445 + 10], fill=ACCENT)
    _text(d, (w / 2, h * 0.58), title.upper(), 380, max_width=w * 0.78)
    return img


def rank_card(n, title, of=7, variant="a", accent=ACCENT):
    """Карточка места в обратном отсчёте: «№ N» очень крупно, вопрос под ним,
    внизу полоса прогресса из `of` делений — пройденные (от of до n) закрашены."""
    from PIL import ImageDraw
    img = _bg("bg_round", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    _text(d, (w / 2, h * 0.30), f"№ {n}", 760, fill=accent)
    d.rectangle([w / 2 - 260, h * 0.535, w / 2 + 260, h * 0.535 + 10], fill=accent)
    lines = _wrap(title.upper(), 26)
    size = 250 if len(lines) == 1 else 190
    y = h * 0.66 if len(lines) == 1 else h * 0.615
    for i, ln in enumerate(lines):
        _text(d, (w / 2, y + i * size * 1.12), ln, size, max_width=w * 0.86)
    # прогресс: of делений слева направо, закрашены места от `of` до `n` включительно
    seg_w, gap, bar_h = 300, 28, 22
    total = of * seg_w + (of - 1) * gap
    x0, yb = (w - total) / 2, h * 0.90
    for i in range(of):
        place = of - i
        fill = accent if place >= n else (70, 70, 70, 255)
        d.rounded_rectangle([x0 + i * (seg_w + gap), yb, x0 + i * (seg_w + gap) + seg_w, yb + bar_h],
                            radius=8, fill=fill)
    return img


def _wrap(text, max_chars):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        if cur and len(cur) + 1 + len(wd) > max_chars:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    if cur:
        lines.append(cur)
    return lines


def list_card(items, variant="a", accent=ACCENT):
    """Итоговый список: строки «7. …» … «1. …», последняя — акцентом."""
    from PIL import ImageDraw
    img = _bg("bg_question", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    n = len(items)
    size = 132 if n >= 7 else 150
    step = h * 0.80 / n
    y = h * 0.12
    for i, it in enumerate(items):
        last = i == n - 1
        _text(d, (w * 0.12, y + i * step), it.upper(), size, fill=accent if last else WHITE,
              anchor="lm", max_width=w * 0.8)
    return img


def rank_plate(n, title, variant="a"):
    """Угловая плашка «№ 5 · ЧЕГО ДОБИЛИСЬ ЗА 60 ЛЕТ?» — висит весь пункт."""
    from PIL import ImageDraw
    img = _plate_base(variant=variant)
    w, h = img.size
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([0, 0, 22, h - 1], radius=11, fill=ACCENT)
    pad = 40
    box = h - pad * 2
    bx = 22 + pad
    d.rounded_rectangle([bx, pad, bx + box, pad + box], radius=18, fill=ACCENT)
    _text(d, (bx + box / 2, h / 2), str(n), int(box * 0.72))
    tx = bx + box + pad
    tw = w - tx - pad
    d.text((tx, h / 2), title.upper(), font=_font(fit_size(title.upper(), 96, tw, min_size=40)),
           fill=WHITE, anchor="lm")
    return img


def final_card(left, right, names=NAMES, variant="a"):
    from PIL import ImageDraw
    img = _bg("bg_score_final", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    # подложка под «ИТОГ»: слово стоит на раскалённой линии-разделителе и без неё не читается
    d.rounded_rectangle([w / 2 - 330, h * 0.13 - 120, w / 2 + 330, h * 0.13 + 120], radius=24,
                        fill=(8, 8, 8, 255), outline=ACCENT, width=6)
    _text(d, (w / 2, h * 0.13), "ИТОГ", 150)
    for cx, name, score, lead in ((w * 0.25, names[0], left, left > right),
                                  (w * 0.75, names[1], right, right > left)):
        _text(d, (cx, h * 0.50), str(score), 900, fill=ACCENT if lead else WHITE)
        _text(d, (cx, h * 0.84), name.upper(), 170)
    return img


def question_card(title, sub="", variant="a"):
    from PIL import ImageDraw
    img = _bg("bg_question", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    vertical = h > w
    s, n = _text_block(d, (w / 2, h * (0.46 if vertical else 0.56)), title.upper(), 300,
                       w * 0.86, max_lines=4 if vertical else 2)
    if sub:
        y = h * (0.46 if vertical else 0.56) + s * 1.12 * n / 2 + (h * 0.05 if vertical else h * 0.09)
        _text_block(d, (w / 2, y), sub.upper(), 150 if vertical else 120, w * 0.8,
                    max_lines=2, fill=ACCENT, min_size=50)
    return img


def proof_card(title, sub="", source="", variant="a"):
    from PIL import ImageDraw
    img = _bg("bg_proof", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    _text(d, (w / 2, h * 0.42), title.upper(), 620, fill=ACCENT, max_width=w * 0.5)
    if sub:
        _text(d, (w / 2, h * 0.66), sub.upper(), 130, max_width=w * 0.5)
    if source:
        _text(d, (w / 2, h * 0.76), f"ИСТОЧНИК: {source}".upper(), 64, fill=(190, 190, 190),
              max_width=w * 0.5)
    return img


def tail_card(title="СМОТРИ ДАЛЬШЕ", variant="a"):
    from PIL import ImageDraw
    img = _bg("bg_tail", variant)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    # середина кадра остаётся пустой: туда Studio кладёт превью видео и кнопку подписки
    _text_block(d, (w / 2, h * 0.12), title.upper(), 170, w * 0.8, max_lines=2)
    return img


def _plate_base(size=PLATE, variant="a", radius=28):
    """Стальная плашка со скруглением, альфа по маске."""
    from PIL import Image, ImageDraw
    w, h = size
    steel = _texture("tex_steel", variant, (4096, 1024), (38, 38, 42, 255))
    # текстура 4:1, плашка шире — режем по высоте, иначе кроп выходит за правый край в чёрное
    steel = steel.crop((0, 0, steel.width, min(steel.height, int(steel.width * h / w)))).resize(size)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=240)
    steel.putalpha(mask)
    ImageDraw.Draw(steel, "RGBA").rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=radius, outline=(70, 70, 70, 230), width=3)
    return steel


def score_plate(left, right, names=NAMES, variant="a"):
    from PIL import Image, ImageDraw
    img = _plate_base(variant=variant)
    w, h = img.size
    box = int(h * 0.74)
    gap = 18
    y0 = (h - box) // 2
    red = _texture("tex_red", variant, (box, box), ACCENT + (255,)).resize((box, box))
    d = ImageDraw.Draw(img, "RGBA")
    # ячейки счёта по центру, имена — по бокам; красная ячейка у того, кто ведёт
    for i, (score, lead) in enumerate(((left, left > right), (right, right > left))):
        x0 = w // 2 - box - gap // 2 + i * (box + gap)
        cell = Image.new("L", (box, box), 0)
        ImageDraw.Draw(cell).rounded_rectangle([0, 0, box - 1, box - 1], radius=18, fill=255)
        if lead:
            img.paste(red, (x0, y0), cell)
        else:
            d.rounded_rectangle([x0, y0, x0 + box - 1, y0 + box - 1], radius=18,
                                fill=(8, 8, 8, 255), outline=(90, 90, 90, 255), width=3)
        _text(d, (x0 + box / 2, h / 2), str(score), int(box * 0.74))
    side = w // 2 - box - gap // 2 - 60
    _text(d, (30 + side / 2, h / 2), names[0].upper(), 92, max_width=side)
    _text(d, (w - 30 - side / 2, h / 2), names[1].upper(), 92, max_width=side)
    return img


def brand_plate(title=None, sub=None, variant="a"):
    from PIL import ImageDraw
    img = _plate_base(variant=variant)
    w, h = img.size
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([0, 0, 22, h - 1], radius=11, fill=ACCENT)
    pad = 44
    mono = h - pad * 2
    mx = 22 + pad
    d.ellipse([mx, pad, mx + mono, pad + mono], fill=(8, 8, 8, 255), outline=ACCENT, width=8)
    if BRAND["monogram"]:
        _text(d, (mx + mono / 2, h / 2), BRAND["monogram"], int(mono * 0.44))
    title = title or BRAND["cta_title"]
    sub = sub or BRAND["cta_sub"]
    tx = mx + mono + pad
    tw = w - tx - pad
    d.text((tx, h * 0.34), title.upper(), font=_font(fit_size(title.upper(), 108, tw)),
           fill=WHITE, anchor="lm")
    d.text((tx, h * 0.70), sub.upper(), font=_font(fit_size(sub.upper(), 54, tw, min_size=28)),
           fill=ACCENT, anchor="lm")
    return img


QR_PLATE = (1536, 600)


def funnel_url():
    """Ссылка воронки по умолчанию — funnel_url из channel/settings.json."""
    import json
    p = os.path.join(ROOT, "channel", "settings.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("funnel_url", "")
    return ""


def qr_plate(url, title=None, sub="НАВЕДИ КАМЕРУ ТЕЛЕФОНА", note="ИЛИ ССЫЛКА ПОД ВИДЕО",
             variant="a"):
    """Плашка с QR-кодом на время байта: с телевизора и с десктопа перейти по ссылке в
    описании нечем или неудобно, QR даёт переход с телефона.

    Уровень коррекции L: на экране код не пачкается, а меньше модулей — крупнее модуль,
    его берёт камера с дивана. Модуль рисуется целым числом пикселей, иначе после
    масштабирования в графе края модулей плывут.
    """
    try:
        import segno
    except ImportError:
        sys.exit("для QR нужен пакет segno: pip install segno (или python install.py --only core)")
    from PIL import Image, ImageDraw
    if not url:
        sys.exit("нужна ссылка: --url или funnel_url в channel/settings.json")
    title = title or BRAND["cta_title"]
    img = _plate_base(size=QR_PLATE, variant=variant)
    w, h = img.size
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([0, 0, 22, h - 1], radius=11, fill=ACCENT)

    matrix = [list(row) for row in segno.make(url, error="l", micro=False).matrix]
    quiet, pad = 4, 36
    n = len(matrix) + quiet * 2
    mod = (h - pad * 2) // n
    side = mod * n
    qx, qy = 22 + pad, (h - side) // 2
    qr = Image.new("RGB", (side, side), (255, 255, 255))
    qd = ImageDraw.Draw(qr)
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x0, y0 = (c + quiet) * mod, (r + quiet) * mod
                qd.rectangle([x0, y0, x0 + mod - 1, y0 + mod - 1], fill=(0, 0, 0))
    img.paste(qr, (qx, qy))

    tx = qx + side + pad
    tw = w - tx - pad
    for i, line in enumerate(_wrap(title.upper(), 14)[:2]):
        d.text((tx, h * (0.20 + 0.20 * i)), line, font=_font(fit_size(line, 132, tw, min_size=60)),
               fill=WHITE, anchor="lm")
    d.text((tx, h * 0.66), sub.upper(), font=_font(fit_size(sub.upper(), 60, tw, min_size=30)),
           fill=ACCENT, anchor="lm")
    d.text((tx, h * 0.82), note.upper(), font=_font(fit_size(note.upper(), 44, tw, min_size=24)),
           fill=(200, 200, 200), anchor="lm")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["round", "score", "final", "question", "proof", "tail", "plate",
                                     "rank", "rankplate", "list", "qr"])
    ap.add_argument("--url", help="ссылка в QR-коде (по умолчанию funnel_url из settings.json)")
    ap.add_argument("--label", default="РАУНД", help="префикс карточки round («РАУНД», «ТОП-3»)")
    ap.add_argument("--of", type=int, default=7, help="сколько всего мест в rank")
    ap.add_argument("--accent", default="red", choices=["red", "gold"])
    ap.add_argument("--items", action="append", default=[], help="строки list, повторяемый")
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--title", default="")
    ap.add_argument("--sub", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--left", type=int, default=0)
    ap.add_argument("--right", type=int, default=0)
    ap.add_argument("--names", nargs=2, default=list(NAMES))
    ap.add_argument("--variant", default="a", choices=["a", "b"])
    ap.add_argument("--vertical", action="store_true", help="2160×3840 для Shorts")
    a = ap.parse_args()
    if a.vertical:
        global FULL
        FULL = (FULL[1], FULL[0])

    v = a.variant
    acc = GOLD if a.accent == "gold" else ACCENT
    img = {
        "round": lambda: round_card(a.n, a.title, v, a.label),
        "rank": lambda: rank_card(a.n, a.title, a.of, v, acc),
        "rankplate": lambda: rank_plate(a.n, a.title, v),
        "list": lambda: list_card(a.items, v, acc),
        "score": lambda: score_plate(a.left, a.right, a.names, v),
        "final": lambda: final_card(a.left, a.right, a.names, v),
        "question": lambda: question_card(a.title, a.sub, v),
        "proof": lambda: proof_card(a.title, a.sub, a.source, v),
        "tail": lambda: tail_card(a.title or BRAND["tail_title"], v),
        "plate": lambda: brand_plate(a.title or None, a.sub or None, v),
        "qr": lambda: qr_plate(a.url or funnel_url(), a.title or None,
                               a.sub or "НАВЕДИ КАМЕРУ ТЕЛЕФОНА", variant=v),
    }[a.kind]()

    out = resolve_repo_path(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # полноэкранным альфа не нужна, а RGB-PNG заметно легче для закачки на VDS
    (img if a.kind in ("score", "plate", "rankplate", "qr") else img.convert("RGB")).save(out)
    log(f"карточка {a.kind} -> {out}")


if __name__ == "__main__":
    main()
