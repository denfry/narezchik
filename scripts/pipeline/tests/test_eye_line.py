"""Выравнивание кадра по линии глаз при наезде.

zoompan увеличивает кадр от центра, поэтому на крупном плане глаза уезжают вверх тем
сильнее, чем больше зум, и смена крупности внутри реплики читается как скачок. Если в
событии кропа задан eye_y, окно наезда смещается так, чтобы линия глаз оставалась на
постоянной высоте кадра (EYE_TARGET_Y).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import filtergraph as fg


def _sub(eye_y=None, zoom_to=1.0):
    return fg.Subclip(src_start=0.0, src_end=2.0, crop_x=None, zoom_from=1.0,
                      zoom_to=zoom_to, ease_ms=0, seg_id="s1", eye_y=eye_y)


def test_without_eye_y_window_stays_centred():
    assert fg.zoom_y_expr(_sub()) == "ih/2-(ih/zoom/2)"


def test_with_eye_y_window_is_clamped_into_frame():
    expr = fg.zoom_y_expr(_sub(eye_y=0.35))
    assert expr.startswith("max(0,min(ih-ih/zoom,")
    assert "0.3500" in expr


def _eval(expr, ih, zoom):
    return eval(expr.replace("ih", str(float(ih))).replace("zoom", str(float(zoom))),
                {"max": max, "min": min})


def test_eye_line_holds_its_height_across_zoom_levels():
    """Точка глаз попадает на одну и ту же долю кадра при любом зуме.

    Проверяется в рабочем диапазоне: eye_y ниже целевой линии и зум достаточный,
    чтобы окно влезло в кадр. При очень малом зуме окно почти равно кадру и сдвигать
    его некуда — тогда работает зажим, это проверяет отдельный тест.
    """
    ih, eye = 2160.0, 0.42
    expr = fg.zoom_y_expr(_sub(eye_y=eye))
    for zoom in (1.25, 1.4, 1.6):
        y = _eval(expr, ih, zoom)
        out_frac = (eye * ih - y) * zoom / ih
        assert abs(out_frac - fg.EYE_TARGET_Y) < 1e-6, (zoom, out_frac)


def test_window_never_leaves_the_frame():
    """Крайние значения eye_y не выводят окно за границы — иначе падает весь граф."""
    ih = 2160.0
    for eye in (0.0, 0.05, 0.95, 1.0):
        expr = fg.zoom_y_expr(_sub(eye_y=eye))
        for zoom in (1.0, 1.3, 2.0):
            y = _eval(expr, ih, zoom)
            assert 0 <= y <= ih - ih / zoom + 1e-9, (eye, zoom, y)


def test_eye_y_is_read_and_inherited_inside_segment():
    """reframe ставит линию глаз, следующий punch_in её наследует."""
    speech = [{"id": "s1", "source_start_sec": 10.0, "source_end_sec": 16.0}]
    events = [
        {"type": "reframe", "segment_ref": "s1", "start_sec": 0.0,
         "crop": {"crop_x": 300, "eye_y": 0.36}},
        {"type": "punch_in", "segment_ref": "s1", "start_sec": 3.0,
         "crop": {"zoom": 1.15}},
    ]
    subs = fg.build_subclips(speech, events)
    assert len(subs) == 2
    assert subs[0].eye_y == 0.36
    assert subs[1].eye_y == 0.36, "линия глаз должна наследоваться, как crop_x"


def test_graph_uses_eye_expression_only_where_given():
    speech = [
        {"id": "a", "source_start_sec": 0.0, "source_end_sec": 3.0},
        {"id": "b", "source_start_sec": 5.0, "source_end_sec": 8.0},
    ]
    events = [{"type": "punch_in", "segment_ref": "a", "start_sec": 0.0,
               "crop": {"zoom": 1.2, "eye_y": 0.4}}]
    subs = fg.build_subclips(speech, events)
    graph = fg.build_graph(subs, width=1080, height=1920, fps=25.0,
                           src_height=1080, src_width=1920)
    if isinstance(graph, (list, tuple)):
        graph = "\n".join(graph)
    assert "max(0,min(ih-ih/zoom," in graph
    assert "ih/2-(ih/zoom/2)" in graph, "сегмент без eye_y остаётся центрированным"
