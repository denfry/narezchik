import re

import generate_subtitles as gs

WORDS = [
    {"word": "Мужчина", "start_sec": 0.0, "end_sec": 0.5, "emphasis": False},
    {"word": "должен", "start_sec": 0.5, "end_sec": 0.9, "emphasis": False},
    {"word": "решать", "start_sec": 0.9, "end_sec": 1.4, "emphasis": True},
    {"word": "сам", "start_sec": 1.4, "end_sec": 1.8, "emphasis": False},
]


def test_one_dialogue_line_per_word():
    lines = gs.build_word_events([{"style": "caption", "words": WORDS}], max_words=3)
    assert len(lines) == 4


def test_each_line_shows_a_window_of_neighbouring_words():
    lines = gs.build_word_events([{"style": "caption", "words": WORDS}], max_words=3)
    assert "Мужчина" in lines[0] and "должен" in lines[0]
    assert "сам" not in lines[0]


def test_active_word_is_coloured_and_others_are_not():
    lines = gs.build_word_events([{"style": "caption", "words": WORDS}], max_words=3)
    first = lines[0]
    # активное слово обёрнуто в тег цвета, за ним идёт возврат к базовому
    assert first.index("{\\c&H") < first.index("Мужчина")
    assert first.count("{\\c&H") >= 1


def test_line_timing_matches_the_active_word():
    lines = gs.build_word_events([{"style": "caption", "words": WORDS}], max_words=3)
    assert "0:00:00.90,0:00:01.40" in lines[2]


def test_emphasised_word_gets_scale_pop():
    lines = gs.build_word_events([{"style": "caption", "words": WORDS}], max_words=3)
    assert "\\t(" in lines[2] and "\\fscx" in lines[2]
    assert "\\t(" not in lines[0]


def test_event_without_words_falls_back_to_whole_chunk():
    lines = gs.build_word_events(
        [{"style": "caption", "text": "целиком одним куском",
          "start_sec": 0.0, "end_sec": 2.0}], max_words=3)
    assert len(lines) == 1
    assert "целиком одним куском" in lines[0]


def test_hyphen_prefix_is_joined_to_the_previous_word():
    lines = gs.build_word_events([{
        "style": "caption",
        "words": [
            {"word": "что", "start_sec": 0.0, "end_sec": 0.2, "emphasis": False},
            {"word": "-то", "start_sec": 0.2, "end_sec": 0.4, "emphasis": False},
            {"word": "важно", "start_sec": 0.4, "end_sec": 0.8, "emphasis": False},
        ],
    }])
    assert len(lines) == 2
    assert "что-то" in lines[0]
    assert "что -то" not in lines[0]


def test_default_captions_use_one_large_word_at_a_time():
    header = gs.ass_header()
    lines = gs.build_word_events([{"style": "caption", "words": WORDS}])
    assert "Style: Dialogue,Montserrat ExtraBold,128," in header
    assert "Мужчина" in lines[0] and "должен" not in lines[0]


def test_subtitles_stay_above_the_shorts_interface():
    header = gs.ass_header(1080, 1920)
    caption = next(l for l in header.splitlines() if l.startswith("Style: Dialogue"))
    margin_v = int(caption.split(",")[-2])
    assert margin_v >= 380



def test_vertical_caption_margin_can_be_lowered_within_safe_area():
    header = gs.ass_header(1080, 1920, subtitle_margin_v=340)
    caption = next(l for l in header.splitlines() if l.startswith("Style: Dialogue"))
    assert int(caption.split(",")[-2]) == 340

def test_landscape_plan_uses_phrase_subtitles_not_one_word_at_a_time():
    """Пословный режим — язык вертикального фида. В горизонтальном лонге зритель
    смотрит дольше и со звуком, и мигание по слову читается как дёрганый шум."""
    events = [{"style": "caption", "words": WORDS}]
    lines = gs.build_events(events, max_chars=42)
    assert len(lines) == 1
    visible = re.sub(r"\{[^}]*\}", "", lines[0]).replace(r"\N", " ")
    assert "Мужчина должен решать сам" in visible


def test_phrase_mode_keeps_the_emphasis_marked_per_word():
    lines = gs.build_events([{"style": "caption", "words": WORDS}], max_chars=42)
    assert gs.EMPHASIS_COLOR in lines[0], "выделение слова потеряно во фразовом режиме"


def test_landscape_styles_do_not_use_the_vertical_headline_size():
    """128 пунктов на 1080×1920 — это одно слово во весь кадр; те же пропорции
    на 1920×1080 закрыли бы говорящего целиком."""
    header = gs.ass_header(1920, 1080)
    assert "Montserrat ExtraBold,54" in header
    assert "Montserrat ExtraBold,128" not in header
    assert gs.is_landscape(1920, 1080)
    assert not gs.is_landscape(1080, 1920)
