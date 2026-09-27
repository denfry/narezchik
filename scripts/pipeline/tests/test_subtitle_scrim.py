"""Затемнение нижней части кадра под субтитрами.

Белый текст с обводкой читается на тёмной студии, но на светлом фоне обводка на
пределе. Фильтр добавляется только когда его явно попросили планом.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import render_short as rs_mod

W, H = 1080, 1920


def test_absent_by_default():
    parts, label = rs_mod.scrim_graph("vout", {}, W, H)
    assert parts == []
    assert label == "vout", "без настройки метка не меняется, граф прежний"


def test_zero_strength_is_a_no_op():
    parts, label = rs_mod.scrim_graph("vout", {"subtitle_scrim": 0}, W, H)
    assert parts == [] and label == "vout"


def test_number_shorthand_builds_three_bands():
    parts, label = rs_mod.scrim_graph("vout", {"subtitle_scrim": 0.45}, W, H)
    assert len(parts) == 3, "градиент имитируется тремя полосами"
    assert label == "sc2"
    assert all("drawbox" in p and "color=black@" in p for p in parts)


def test_bands_fade_upwards_and_stay_inside_frame():
    parts, _ = rs_mod.scrim_graph("vout", {"subtitle_scrim": {"strength": 0.6}}, W, H)
    alphas, tops = [], []
    for p in parts:
        alphas.append(float(p.split("color=black@")[1].split(":")[0]))
        tops.append(int(p.split(":y=")[1].split(":")[0]))
    assert alphas == sorted(alphas, reverse=True), "нижняя полоса плотнее верхних"
    assert max(alphas) <= 0.6 + 1e-9
    assert all(0 <= y < H for y in tops)
    assert tops == sorted(tops, reverse=True), "полосы идут снизу вверх"


def test_height_fraction_is_respected_and_capped():
    parts, _ = rs_mod.scrim_graph("vout", {"subtitle_scrim": {"strength": 0.4,
                                                              "height_frac": 0.3}}, W, H)
    band = int(parts[0].split(":h=")[1].split(":")[0])
    assert abs(band * 3 - H * 0.3) <= 3

    huge, _ = rs_mod.scrim_graph("vout", {"subtitle_scrim": {"strength": 0.4,
                                                             "height_frac": 5.0}}, W, H)
    band_h = int(huge[0].split(":h=")[1].split(":")[0])
    assert band_h * 3 <= H * 0.6 + 3, "доля высоты зажимается, иначе зальёт полкадра"
