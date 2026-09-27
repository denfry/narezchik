import numpy as np

import make_cover


def test_sharpness_prefers_detailed_frame_over_flat_one():
    flat = np.full((100, 100, 3), 128, dtype=np.uint8)
    noisy = (np.random.default_rng(0).integers(0, 255, (100, 100, 3))).astype(np.uint8)
    assert make_cover.sharpness(noisy) > make_cover.sharpness(flat)


def test_frame_score_requires_a_face():
    without = make_cover.frame_score(sharp=900.0, face_area_ratio=0.0)
    with_face = make_cover.frame_score(sharp=300.0, face_area_ratio=0.08)
    assert with_face > without, "кадр без лица не должен побеждать по одной резкости"


def test_cover_is_vertical_and_full_size():
    frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
    img = make_cover.compose(frame, "ПОЧЕМУ 95% МУЖЧИН")
    assert img.size == (1080, 1920)


def test_headline_occupies_at_least_a_third_of_the_width():
    box = make_cover.headline_box("ПОЧЕМУ 95%", width=1080)
    assert box[2] - box[0] >= 1080 / 3


def test_monogram_stays_small_and_bottom_left():
    x, y, size = make_cover.monogram_placement(1080, 1920)
    assert size <= 1080 * 0.08
    assert x < 1080 / 2 and y > 1920 / 2


def test_trim_keeps_whole_words():
    trimmed = make_cover.trim_words("КАК ТОЛЬКО ТЫ ПЕРЕСТАЁШЬ БЫТЬ ЗАГАДКОЙ", max_chars=20)
    assert not trimmed.endswith("ПЕРЕСТАЁ")
    assert trimmed == "КАК ТОЛЬКО ТЫ"


def test_crop_follows_the_face_instead_of_the_frame_centre():
    """Говорящий редко стоит по центру 4K-кадра — обложка обязана его догонять.

    При центр-кропе 3840->1215 лицо, снятое сбоку, уезжает за границу обложки.
    """
    x0 = make_cover.cover_crop_x(src_width=3840, crop_w=1214, face_cx=2900)
    assert x0 + 1214 / 2 == 2900, "кроп не отцентрирован по лицу"

    clamped = make_cover.cover_crop_x(src_width=3840, crop_w=1214, face_cx=100)
    assert clamped == 0, "кроп вышел за левую границу кадра"

    clamped_r = make_cover.cover_crop_x(src_width=3840, crop_w=1214, face_cx=3800)
    assert clamped_r == 3840 - 1214


def test_headline_does_not_cover_the_face():
    """Текст и лицо — два элемента, они не должны налезать друг на друга.

    Если лицо в кадре высоко, заголовок уходит вниз, и наоборот.
    """
    top = make_cover.headline_top(face_top_ratio=0.05, face_bottom_ratio=0.45, height=1920)
    assert top > 0.45 * 1920, "лицо сверху — заголовок обязан уйти под него"

    bottom = make_cover.headline_top(face_top_ratio=0.55, face_bottom_ratio=0.95, height=1920)
    assert bottom < 0.55 * 1920, "лицо снизу — заголовок обязан уйти над ним"
