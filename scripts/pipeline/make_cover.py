#!/usr/bin/env python
"""Автосборка вертикальной обложки шортса.

Правила композиции — из docs/playbook.md, раздел про обложки: текст занимает минимум
треть ширины кадра, на обложке максимум два конкурирующих элемента (лицо и текстовый
блок), один цвет-акцент. Мелкие брендовые элементы — монограмма и угловой шеврон
из channel/brand.json — за внимание не конкурируют и в эти два элемента не считаются.

Usage:
    python make_cover.py edit-plans/podcast_short01.json
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand
from common import TEMP_DIR, load_json, log, resolve_repo_path, save_json

BRAND = brand.load()
FONT_PATH = brand.font_path(BRAND)
ACCENT = brand.rgb(BRAND["accent_color"])
WHITE = brand.rgb(BRAND["text_color"])
BLACK = (12, 12, 12)

COVER_W, COVER_H = 1080, 1920
HEADLINE_SIZE = 118
LINE_GAP = 18
SIDE_MARGIN = 60


def sharpness(frame):
    """Дисперсия лапласиана — стандартная мера резкости кадра."""
    import cv2
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def frame_score(sharp, face_area_ratio):
    """Кадр без лица не должен побеждать по одной резкости — отсюда штраф.

    Самый резкий кадр в говорящей голове часто оказывается кадром, где спикер
    отвернулся или вышел из фокуса внимания: резкая стена позади резче лица.
    """
    if face_area_ratio <= 0:
        return sharp * 0.001
    return sharp * (0.5 + face_area_ratio * 10)


def cover_crop_x(src_width, crop_w, face_cx):
    """Левый край вертикального кропа обложки, отцентрированный по лицу.

    Центр-кроп здесь не годится: спикер редко стоит по центру 4K-кадра, и при
    центрировании по кадру лицо уезжает к краю обложки или за неё.
    """
    x0 = int(round(face_cx - crop_w / 2))
    return max(0, min(int(src_width - crop_w), x0))


def headline_top(face_top_ratio, face_bottom_ratio, height, block_h=400):
    """Вертикальная позиция заголовка — с той стороны кадра, где лица нет.

    Текст и лицо — два элемента композиции, и они не должны налезать друг на друга:
    перекрытый текстом взгляд убивает обложку вернее, чем мелкий шрифт.
    """
    margin = 40
    if face_top_ratio + face_bottom_ratio < 1.0:      # лицо в верхней половине
        top = face_bottom_ratio * height + margin
    else:                                             # лицо в нижней половине
        top = face_top_ratio * height - block_h - margin
    return int(max(margin, min(height - block_h - margin, top)))


def headline_box(text, width, margin=SIDE_MARGIN):
    """Прямоугольник заголовка. Минимум треть ширины кадра — правило docs/playbook.md."""
    w = max(width / 3, width - margin * 2)
    return (margin, int(width * 0.06), int(margin + w), int(width * 0.06 + 300))


def monogram_placement(width, height):
    """Монограмма канала: левый нижний угол, не крупнее 8% ширины кадра."""
    size = int(width * 0.075)
    return int(width * 0.05), int(height - size - width * 0.05), size


def _font(size):
    from PIL import ImageFont
    if os.path.isfile(FONT_PATH):
        return ImageFont.truetype(FONT_PATH, size)
    return ImageFont.load_default()


def trim_words(text, max_chars=60):
    """Обрезать по границе слова, а не по символу.

    Срез по символу разрубает слово посреди («ПЕРЕСТАЁ»), и заголовок обложки
    превращается в опечатку.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text
    out = []
    for word in text.split():
        if len(" ".join(out + [word])) > max_chars:
            break
        out.append(word)
    return " ".join(out) if out else text[:max_chars]


def _wrap(draw, text, font, max_width):
    lines, cur = [], ""
    for word in text.upper().split():
        probe = (cur + " " + word).strip()
        if draw.textlength(probe, font=font) > max_width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines[:3]


def pick_best_frame(video_path, max_candidates=40):
    import cv2

    import autoframe

    frames = autoframe.detect_faces(video_path, sample_fps=2.0)
    cap = cv2.VideoCapture(video_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    best, best_score, best_t, best_face = None, -1.0, 0.0, None
    for fr in frames[:max_candidates]:
        if not fr["faces"]:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fr["t"] * src_fps))
        ok, img = cap.read()
        if not ok:
            continue
        face = max(fr["faces"], key=lambda f: f["w"] * f["h"])
        ratio = (face["w"] * face["h"]) / (img.shape[0] * img.shape[1])
        score = frame_score(sharpness(img), ratio)
        if score > best_score:
            best, best_score, best_t, best_face = img, score, fr["t"], face
    cap.release()
    if best is None:
        raise RuntimeError("не нашлось ни одного кадра с лицом для обложки")
    log(f"обложка: выбран кадр на {best_t:.2f}с (оценка {best_score:.0f})")
    return best_t, best, best_face


def compose(frame, hook_text, accent=ACCENT, face=None):
    import cv2
    from PIL import Image, ImageDraw

    h, w = frame.shape[:2]
    crop_w = int(h * COVER_W / COVER_H) // 2 * 2
    face_cx = face["cx"] if face else w / 2
    x0 = cover_crop_x(w, crop_w, face_cx)
    crop = frame[:, x0:x0 + crop_w]
    img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)).resize((COVER_W, COVER_H))

    draw = ImageDraw.Draw(img, "RGBA")
    font = _font(HEADLINE_SIZE)
    lines = _wrap(draw, hook_text, font, COVER_W - SIDE_MARGIN * 2)
    block_h = len(lines) * (HEADLINE_SIZE + LINE_GAP)

    if face:
        top_ratio = max(0.0, (face["cy"] - face["h"] / 2) / h)
        bottom_ratio = min(1.0, (face["cy"] + face["h"] / 2) / h)
    else:
        top_ratio, bottom_ratio = 0.15, 0.55
    y = headline_top(top_ratio, bottom_ratio, COVER_H, block_h)

    # затемнение только под текстом: сплошная плашка во весь верх кадра гасит лицо
    draw.rectangle([0, y - 30, COVER_W, y + block_h + 20], fill=(0, 0, 0, 170))

    for i, line in enumerate(lines):
        draw.text((SIDE_MARGIN, y), line, font=font,
                  fill=accent if i == 0 else WHITE,
                  stroke_width=6, stroke_fill=BLACK)
        y += HEADLINE_SIZE + LINE_GAP

    # угловой шеврон цвета акцента, правый верхний угол
    if BRAND["cover_chevron"]:
        draw.polygon([(COVER_W, 0), (COVER_W, 190), (COVER_W - 190, 0)], fill=accent)

    if BRAND["monogram"]:
        mx, my, msize = monogram_placement(COVER_W, COVER_H)
        draw.ellipse([mx, my, mx + msize, my + msize], fill=BLACK, outline=accent, width=4)
        draw.text((mx + msize / 2, my + msize / 2), BRAND["monogram"],
                  font=_font(int(msize * 0.42)), fill=WHITE, anchor="mm")

    return img


def make(edit_plan_path):
    import workfile

    edit_plan_path = resolve_repo_path(edit_plan_path)
    plan = load_json(edit_plan_path)
    clip_id = plan["project"]["clip_id"]

    start, end = workfile.window_bounds(plan["speech_edits"])
    work = workfile.extract(resolve_repo_path(plan["source"]["path"]), clip_id, start, end)

    rs = plan.setdefault("render_settings", {})
    cover = rs.setdefault("cover_frame", {})
    # clip.hook — это реплика целиком, она годится как исходник, но не как заголовок
    # обложки: срез цитаты не обещает зрителю ничего. Осмысленный короткий заголовок
    # задаётся в render_settings.cover_frame.hook_text редактором.
    hook = cover.get("hook_text") or plan.get("clip", {}).get("hook", "")
    if not hook:
        raise RuntimeError("нет текста для обложки: заполните clip.hook")
    if not cover.get("hook_text"):
        log("ВНИМАНИЕ: cover_frame.hook_text не задан, беру начало clip.hook — "
            "почти наверняка это плохой заголовок, задайте его явно")

    _t, frame, face = pick_best_frame(work)
    img = compose(frame, trim_words(hook), face=face)
    out = os.path.join(TEMP_DIR, f"{clip_id}_cover.png")
    img.save(out)

    cover["enabled"] = True
    cover.setdefault("duration_sec", 2.5)
    cover["image_path"] = f"temp/{clip_id}_cover.png"
    save_json(edit_plan_path, plan)

    log(f"обложка собрана -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("edit_plan")
    args = ap.parse_args()
    make(args.edit_plan)


if __name__ == "__main__":
    main()
