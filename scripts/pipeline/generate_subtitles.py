#!/usr/bin/env python
"""subtitle-director deterministic backend.

Чисто механический шаг: берёт УЖЕ ПРИНЯТЫЕ редакторские решения из edit_plan.subtitle_events
(текст чанка, тайминги, какие слова выделить) и рендерит валидный .ass файл. Никаких решений
о том, как группировать слова или что выделять, этот скрипт не принимает — это работа
subtitle-director skill (LLM) при заполнении edit plan.

Usage:
    python generate_subtitles.py edit-plans/interview_short03.json --out temp/interview_short03.ass
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand
from common import TEMP_DIR, fmt_ts_ass, load_json, log, resolve_repo_path

STYLE_MAP = {
    "caption": "Dialogue",
    "hook_headline": "Hook",
    "speaker_label": "Speaker",
    "emphasis_callout": "Emphasis",
}

_BRAND = brand.load()
# акцент берётся из channel/brand.json (accent_color); ASS хранит цвет как BGR
EMPHASIS_COLOR = brand.ass_colour(_BRAND["accent_color"]).replace("&H00", "&H") + "&"
BASE_COLOR = "&HFFFFFF&"


# Стили заданы отдельно для вертикали и горизонтали, а не масштабированием одного набора.
# Кегль 128 на 1080×1920 — это ~12% высоты кадра и работает только потому, что в кадре
# видно одно слово; те же пропорции на 1920×1080 закрыли бы говорящего.
VERTICAL_STYLES = """\
Style: Dialogue,{font},128,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,4,2,60,60,420,1
Style: Speaker,{font},50,{highlight},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,1,2,40,40,800,1
Style: Hook,{font},116,{accent},&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,4,3,8,30,30,180,1
Style: Emphasis,{font},80,{highlight},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,3,2,40,40,650,1"""

# Горизонталь: нижняя треть, две строки максимум. MarginV 90 держит текст выше зоны,
# которую на YouTube закрывают таймлайн плеера и кнопки при наведении.
LANDSCAPE_STYLES = """\
Style: Dialogue,{font},54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,2,120,120,90,1
Style: Speaker,{font},36,{highlight},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,1,80,80,90,1
Style: Hook,{font},72,{accent},&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,4,3,8,80,80,80,1
Style: Emphasis,{font},60,{highlight},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,140,1"""


def is_landscape(width, height):
    return width >= height


def ass_header(width=1080, height=1920, subtitle_margin_v=None):
    styles = LANDSCAPE_STYLES if is_landscape(width, height) else VERTICAL_STYLES
    if not is_landscape(width, height) and subtitle_margin_v is not None:
        safe_margin = max(320, int(subtitle_margin_v))
        styles = styles.replace(",420,1", f",{safe_margin},1", 1)
    styles = styles.format(font=_BRAND["font_name"], accent=brand.ass_colour(_BRAND["accent_color"]),
                           highlight=brand.ass_colour(_BRAND["highlight_color"]))
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def wrap_visible(text, max_chars=16):
    """Перенос по словам, учитывая \\N уже расставленные вручную и игнорируя ASS-теги
    override при подсчёте видимой длины строки."""
    manual_lines = text.split("\\N")
    out_lines = []
    for line in manual_lines:
        words = line.split()
        cur, cur_len = [], 0
        for w in words:
            visible = re.sub(r"\{[^}]*\}", "", w)
            add = len(visible) + (1 if cur else 0)
            if cur and cur_len + add > max_chars:
                out_lines.append(" ".join(cur))
                cur, cur_len = [w], len(visible)
            else:
                cur.append(w)
                cur_len += add
        if cur:
            out_lines.append(" ".join(cur))
    return "\\N".join(out_lines)


def apply_emphasis(text, emphasized_words):
    if not emphasized_words:
        return text
    targets = {w.strip(".,!?:;\"'").lower() for w in emphasized_words if w.strip()}
    if not targets:
        return text

    def repl(m):
        word = m.group(0)
        bare = word.strip(".,!?:;\"'").lower()
        if bare in targets:
            return f"{{\\c{EMPHASIS_COLOR}}}{word}{{\\c{BASE_COLOR}}}"
        return word

    return re.sub(r"[^\s\\]+", repl, text)


# Сторона плашки-тезиса (стиль Hook): на крупном плане говорящего плашка по центру
# сверху ложится на макушку, поэтому в разметке можно указать угол, где фон.
ALIGN_TAGS = {"top_left": "\\an7", "top_center": "\\an8", "top_right": "\\an9"}
ACCENT_COLOUR = brand.ass_colour(_BRAND["accent_color"]) + "&"   # акцент из brand.json
BASE_COLOUR = "&H00FFFFFF&"     # белый


def join_hyphenated_tokens(words):
    """Склеивает токены распознавания вида «что», «-то» в одно слово."""
    joined = []
    for word in words:
        token = word["word"]
        if joined and token.startswith("-"):
            previous = joined[-1]
            previous["word"] += token
            previous["end_sec"] = word["end_sec"]
            previous["emphasis"] = previous.get("emphasis") or word.get("emphasis")
        else:
            joined.append(dict(word))
    return joined


HOOK_SIZE = {"vertical": (116, 1080 - 60), "landscape": (72, 1920 - 160)}
HOOK_MIN_SIZE = 64
HOOK_MAX_LINES = 3


def _text_width(text, size):
    """Ширина строки в пикселях PlayRes. Без Pillow — оценка по числу знаков."""
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(brand.font_path(_BRAND), size)
        return font.getlength(text)
    except Exception:  # noqa: BLE001
        return len(text) * size * 0.72


def _wrap_to_width(words, size, max_width):
    lines, cur = [], ""
    for w in words:
        probe = f"{cur} {w}".strip()
        if cur and _text_width(probe, size) > max_width:
            lines.append(cur)
            cur = w
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines


def fit_hook(text, landscape):
    """Текст плашки-тезиса с переносами и, если надо, уменьшенным кеглем.

    libass длинную строку не переносит по словам так, как нужно заголовку: «ЕМУ НУЖНА
    СЛАБАЯ ЖЕНЩИНА?» кеглем 116 уходил за оба края вертикального кадра. Здесь строка
    раскладывается не больше чем на три, а кегль уменьшается, пока всё не влезет.
    Переносы, расставленные вручную через \\N, не трогаются.
    """
    if "\\N" in text:
        return text
    base, max_width = HOOK_SIZE["landscape" if landscape else "vertical"]
    words = text.split()
    size = base
    while size > HOOK_MIN_SIZE:
        lines = _wrap_to_width(words, size, max_width)
        if len(lines) <= HOOK_MAX_LINES and all(_text_width(l, size) <= max_width for l in lines):
            break
        size -= 4
    lines = _wrap_to_width(words, size, max_width)
    tag = "" if size == base else "{\\fs%d}" % size
    return tag + "\\N".join(lines)


def _plain_event(ev, style, landscape):
    """Событие без пословной разметки: плашка-тезис, подпись спикера, акцент."""
    text = ev.get("text", "")
    effect = ""
    if style == "Hook":
        text = fit_hook(text, landscape)
        effect = "{\\fad(150,250)" + ALIGN_TAGS.get(ev.get("align"), "") + "}"
    layer = 1 if style == "Hook" else 0
    return (f"Dialogue: {layer},{fmt_ts_ass(ev['start_sec'])},{fmt_ts_ass(ev['end_sec'])},"
            f"{style},,0,0,0,,{effect}{text}")


def build_word_events(subtitle_events, max_words=1, landscape=False):
    """Одна строка Dialogue на каждое слово: в окне видно max_words слов,
    активное окрашено акцентным цветом.

    Тег \\k здесь не годится — он делает заливку караоке, а не смену цвета
    целого слова, и не даёт подсветить одно слово из нескольких видимых.
    """
    lines = []
    for ev in subtitle_events:
        style = STYLE_MAP.get(ev.get("style", "caption"), "Dialogue")
        words = join_hyphenated_tokens(ev.get("words") or [])

        if not words:
            if ev.get("text"):
                lines.append(_plain_event(ev, style, landscape))
            continue

        for i, w in enumerate(words):
            lo = max(0, min(i, len(words) - max_words))
            window = words[lo:lo + max_words]
            parts = []
            for w2 in window:
                if w2 is w:
                    # Стандарт §12: база белая, красный — только акцент. Цветом
                    # выделяем активное слово лишь когда в окне их несколько.
                    colour = ACCENT_COLOUR if (w2.get("emphasis") or max_words > 1) else BASE_COLOUR
                    piece = f"{{\\c{colour}}}{w2['word']}{{\\c{BASE_COLOUR}}}"
                    if w2.get("emphasis"):
                        dur_ms = int((w2["end_sec"] - w2["start_sec"]) * 1000)
                        pop = min(160, max(80, dur_ms // 3))
                        piece = (f"{{\\fscx112\\fscy112\\t(0,{pop},\\fscx100\\fscy100)}}"
                                 + piece)
                    parts.append(piece)
                else:
                    parts.append(w2["word"])
            lines.append(
                f"Dialogue: 0,{fmt_ts_ass(w['start_sec'])},{fmt_ts_ass(w['end_sec'])},"
                f"{style},,0,0,0,,{' '.join(parts)}"
            )
    return lines


def build_events(subtitle_events, max_chars):
    events = []
    for ev in subtitle_events:
        style = STYLE_MAP.get(ev.get("style", "caption"), "Dialogue")
        # пословная разметка годится и для фразового режима: текст чанка — это его слова
        raw = ev.get("text") or " ".join(w["word"] for w in (ev.get("words") or []))
        if not raw:
            continue
        words = ev.get("words") or []
        emphasized = ev.get("emphasized_words") or [
            w["word"] for w in words if w.get("emphasis")
        ]
        text = apply_emphasis(raw, emphasized)
        text = wrap_visible(text, max_chars)
        layer = 1 if style == "Hook" else 0
        effect = ""
        if style == "Hook":
            # align из разметки -> тег an7/an8/an9; поля MarginL/R/V остаются от стиля Hook
            effect = "{\\fad(150,250)" + ALIGN_TAGS.get(ev.get("align"), "") + "}"
        start_sec = ev.get("start_sec", words[0]["start_sec"] if words else 0.0)
        end_sec = ev.get("end_sec", words[-1]["end_sec"] if words else start_sec)
        start, end = fmt_ts_ass(start_sec), fmt_ts_ass(end_sec)
        events.append(f"Dialogue: {layer},{start},{end},{style},,0,0,0,,{effect}{text}")
    return events


def shift_events(subtitle_events, offset):
    """Сдвинуть всю разметку по времени.

    Нужно, когда перед речью вклеивается обложка: субтитры размечены в таймлайне
    речи и без сдвига пошли бы поверх обложки, разъехавшись с голосом.
    """
    if not offset:
        return subtitle_events
    out = []
    for ev in subtitle_events:
        new = dict(ev)
        for key in ("start_sec", "end_sec"):
            if key in new and new[key] is not None:
                new[key] = round(new[key] + offset, 3)
        if new.get("words"):
            new["words"] = [
                dict(w, start_sec=round(w["start_sec"] + offset, 3),
                     end_sec=round(w["end_sec"] + offset, 3))
                for w in new["words"]
            ]
        out.append(new)
    return out


def generate(edit_plan_path, out_path=None, max_chars=None, time_offset=0.0):
    plan = load_json(resolve_repo_path(edit_plan_path))
    subtitle_events = plan.get("subtitle_events", [])
    if not subtitle_events:
        raise RuntimeError("edit plan has no subtitle_events — nothing to render")

    render = plan.get("render_settings", {})
    width, height = render.get("width", 1080), render.get("height", 1920)
    landscape = is_landscape(width, height)

    # Пословный режим — язык вертикального фида: там субтитр и есть основной носитель
    # темпа. В горизонтальном лонге зритель смотрит со звуком и дольше, и мигающее
    # по слову слово читается как дёрганый шум — там субтитр идёт фразой.
    mode = render.get("subtitle_mode") or ("chunk" if landscape else "word")
    if max_chars is None:
        max_chars = 42 if landscape else 16

    subtitle_events = shift_events(subtitle_events, time_offset)
    ordered = sorted(subtitle_events, key=lambda e: e.get("start_sec",
                     (e.get("words") or [{"start_sec": 0}])[0]["start_sec"]))
    events = build_events(ordered, max_chars) if mode == "chunk" \
        else build_word_events(ordered, landscape=landscape)

    if out_path is None:
        clip_id = plan.get("project", {}).get("clip_id", "clip")
        out_path = os.path.join(TEMP_DIR, f"{clip_id}.ass")
    else:
        out_path = resolve_repo_path(out_path)

    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write(ass_header(width, height, render.get("subtitle_margin_v")))
        f.write("\n".join(events) + "\n")

    log(f"wrote {out_path} ({len(events)} events)")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("edit_plan")
    ap.add_argument("--out", help="путь к .ass (по умолчанию temp/<clip_id>.ass)")
    ap.add_argument("--max-chars", type=int, default=None,
                    help="макс. символов в строке субтитра (по умолчанию 16 для вертикали, 42 для горизонтали)")
    args = ap.parse_args()

    try:
        generate(args.edit_plan, args.out, args.max_chars)
    except Exception as e:
        log(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
