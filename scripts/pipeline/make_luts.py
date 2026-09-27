#!/usr/bin/env python
"""Генерация собственных 3D LUT (.cube) для цветокора — без чужих лицензий.

Скачанные «бесплатные» LUT почти всегда идут с условиями (email, атрибуция, запрет
перепродажи), а нам нужно 5-6 стабильных грейдов, которые можно чередовать между
роликами. Поэтому LUT считаются математикой: S-кривая контраста, сплит-тонирование
(тени в один оттенок, света в другой), насыщенность и лёгкий подъём чёрного «под
плёнку». Файлы маленькие (25^3 точек), лежат в assets/luts/ и коммитятся.

Применение — render_short.py, render_settings.lut + lut_strength: LUT смешивается с
оригиналом (blend), поэтому один и тот же файл на 0.4 — деликатная коррекция, на 0.8 —
явный «киношный» грейд.

Usage:
    python make_luts.py            # пересоздать все LUT в assets/luts/
    python make_luts.py --size 33  # плотнее сетка (файлы крупнее)
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ASSETS_DIR  # noqa: E402

LUT_DIR = os.path.join(ASSETS_DIR, "luts")


def _clamp(x):
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _luma(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _scurve(x, strength):
    """Мягкая S-кривая вокруг серого 0.5: strength 0 — линейно, 0.3 — заметный
    контраст без клиппинга (края поджимаются плавно)."""
    t = x - 0.5
    return _clamp(0.5 + t * (1.0 + strength) - strength * 4.0 * t ** 3)


def _saturate(r, g, b, s):
    y = _luma(r, g, b)
    return (_clamp(y + (r - y) * s), _clamp(y + (g - y) * s), _clamp(y + (b - y) * s))


def _split_tone(r, g, b, shadow, highlight, amount):
    """Тени тянутся к shadow (rgb-смещение), света — к highlight. amount ~0.03-0.08."""
    y = _luma(r, g, b)
    ws = (1.0 - y) ** 2
    wh = y ** 2
    return (
        _clamp(r + amount * (ws * shadow[0] + wh * highlight[0])),
        _clamp(g + amount * (ws * shadow[1] + wh * highlight[1])),
        _clamp(b + amount * (ws * shadow[2] + wh * highlight[2])),
    )


def _lift(x, lift):
    """Подъём чёрной точки: плёночная «дымка» в тенях."""
    return _clamp(lift + x * (1.0 - lift))


# Каждый пресет — функция (r,g,b)->(r,g,b). Оттенки подобраны так, чтобы кожа
# оставалась кожей: сплит-тон не сильнее 0.08, насыщенность не выше 1.15.
PRESETS = {
    # Классика блокбастера: тени в бирюзу, света в тёплый — спикер на тёмном фоне
    # «отлипает» от заднего плана.
    "teal_orange": lambda r, g, b: _saturate(
        *_split_tone(_scurve(r, 0.25), _scurve(g, 0.25), _scurve(b, 0.25),
                     shadow=(-0.6, 0.15, 0.5), highlight=(0.7, 0.3, -0.5), amount=0.07),
        1.08),
    # Тёплая плёнка: приподнятый чёрный, тёплые средние, мягкие света.
    "warm_film": lambda r, g, b: _saturate(
        *_split_tone(_lift(_scurve(r, 0.15), 0.03), _lift(_scurve(g, 0.15), 0.025),
                     _lift(_scurve(b, 0.15), 0.02),
                     shadow=(0.2, 0.05, -0.3), highlight=(0.5, 0.25, -0.2), amount=0.05),
        0.97),
    # Холодная сталь: деньги/дисциплина — синие тени, чистые света, плотный контраст.
    "cool_steel": lambda r, g, b: _saturate(
        *_split_tone(_scurve(r, 0.3), _scurve(g, 0.3), _scurve(b, 0.3),
                     shadow=(-0.4, -0.1, 0.7), highlight=(0.1, 0.1, 0.1), amount=0.06),
        0.92),
    # Bleach bypass: выбеленный высококонтрастный кадр — для провокации/жёстких тем.
    "bleach": lambda r, g, b: _saturate(
        *_split_tone(_scurve(r, 0.4), _scurve(g, 0.4), _scurve(b, 0.4),
                     shadow=(0.1, 0.05, -0.1), highlight=(0.2, 0.15, 0.0), amount=0.03),
        0.72),
    # Почти монохром с красным остатком — под бренд-палитру канала (чёрный/красный).
    "noir_red": lambda r, g, b: _saturate(
        *_split_tone(_lift(_scurve(r, 0.35), 0.02), _lift(_scurve(g, 0.35), 0.02),
                     _lift(_scurve(b, 0.35), 0.02),
                     shadow=(0.0, 0.0, 0.0), highlight=(0.6, -0.1, -0.2), amount=0.04),
        0.35),
    # Чистый «панч»: контраст + насыщенность без тонирования — для ярких студийных
    # исходников, где цвет уже хороший.
    "clean_punch": lambda r, g, b: _saturate(_scurve(r, 0.3), _scurve(g, 0.3),
                                             _scurve(b, 0.3), 1.14),
    # Графит: приглушённый цвет, холодно-зелёные тени, тёплые нейтральные света и
    # лёгкая дымка в чёрном — седьмой грейд, чтобы соседние лонги не повторялись
    "graphite": lambda r, g, b: _saturate(
        *_split_tone(_lift(_scurve(r, 0.28), 0.015), _lift(_scurve(g, 0.28), 0.015),
                     _lift(_scurve(b, 0.28), 0.015),
                     shadow=(-0.25, 0.1, 0.2), highlight=(0.35, 0.18, -0.1), amount=0.05),
        0.88),
}


def write_cube(path, fn, size):
    with io.open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(f"TITLE \"{os.path.splitext(os.path.basename(path))[0]}\"\n")
        f.write(f"LUT_3D_SIZE {size}\n")
        f.write("DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n")
        n = size - 1
        # порядок .cube: красный меняется быстрее всего, синий — медленнее всего
        for bi in range(size):
            for gi in range(size):
                for ri in range(size):
                    r, g, b = fn(ri / n, gi / n, bi / n)
                    f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--only", nargs="*", help="имена пресетов")
    args = ap.parse_args()
    os.makedirs(LUT_DIR, exist_ok=True)
    names = args.only or list(PRESETS)
    for name in names:
        out = os.path.join(LUT_DIR, f"{name}.cube")
        write_cube(out, PRESETS[name], args.size)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
