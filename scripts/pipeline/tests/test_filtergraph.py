import pytest

import filtergraph


def test_cover_is_prepended_as_its_own_input_not_drawn_over_speech():
    g = filtergraph.build_graph(_two_subclips(), cover_path="temp/c.png", cover_sec=2.5)
    # обложка — отдельный вход с зацикленной картинкой и своей тишиной
    assert "[1:v:0]" in g
    assert "anullsrc" in g
    # и она первая в concat, а речь идёт после неё
    assert "concat=n=3:v=1:a=1[vcat][aout]" in g


def test_tail_card_is_appended_as_its_own_input():
    """CTA-плашка удлиняет ролик, а не ложится поверх панчлайна.

    Наложением её поставить нельзя: ролик заканчивается ровно на панчлайне, и
    оверлей закрыл бы то, ради чего зритель досматривал.
    """
    g = filtergraph.build_graph(_two_subclips(), tail_path="cta/cta.png",
                                tail_sec=2.5)
    assert "[1:v:0]" in g
    assert "concat=n=3:v=1:a=1[vcat][aout]" in g
    # плашка последняя в concat, речь идёт до неё
    assert g.index("[v0][a0]") < g.index("[vtail][atail]")


def test_cover_and_tail_wrap_the_speech_from_both_sides():
    g = filtergraph.build_graph(_two_subclips(), cover_path="temp/c.png", cover_sec=2.0,
                                tail_path="cta/cta.png", tail_sec=2.5)
    assert "concat=n=4:v=1:a=1[vcat][aout]" in g
    assert g.index("[vcover][acover]") < g.index("[v0][a0]") < g.index("[vtail][atail]")
    # входы идут по порядку: обложка, потом плашка
    assert g.index("[1:v:0]") < g.index("[2:v:0]")


def test_overlay_input_numbering_accounts_for_the_tail_card():
    g = filtergraph.build_graph(
        _two_subclips(), tail_path="cta.png", tail_sec=2.0,
        overlays=[{"path": "x.png", "start_sec": 1.0, "end_sec": 3.0,
                   "mode": "fullscreen", "is_video": False}])
    # 1 — плашка, 2 — врезка; при сбитой нумерации оверлей взял бы саму плашку
    assert "[2:v:0]" in g


def test_fullscreen_overlay_covers_the_frame_for_its_window():
    g = filtergraph.build_graph(
        _two_subclips(),
        overlays=[{"path": "cta/x.png", "start_sec": 10.0, "end_sec": 13.0,
                   "mode": "fullscreen", "is_video": False}])
    assert "overlay=" in g
    assert "between(t,10.000,13.000)" in g


def test_corner_overlay_is_scaled_down_and_offset():
    g = filtergraph.build_graph(
        _two_subclips(),
        overlays=[{"path": "cta/x.png", "start_sec": 5.0, "end_sec": 9.0,
                   "mode": "corner", "is_video": False}])
    assert "scale=" in g
    assert "overlay=x=" in g


def test_corner_overlay_goes_bottom_left_and_small_on_a_horizontal_frame():
    """В 16:9 крупный план занимает половину кадра: пин 42% в любом верхнем углу ложится
    на лицо. Поэтому у горизонтали пин 26% ширины и стоит внизу слева, над субтитрами."""
    g = filtergraph.build_graph(
        _two_subclips(), width=1920, height=1080,
        overlays=[{"path": "cta/x.mp4", "start_sec": 5.0, "end_sec": 9.0,
                   "mode": "corner", "is_video": True}])
    assert f"scale={int(1920 * 0.26) // 2 * 2}:-2" in g
    assert f"overlay=x={int(1920 * 0.04)}:y={int(1080 * 0.56)}" in g


def test_corner_overlay_keeps_top_right_and_full_size_on_vertical():
    g = filtergraph.build_graph(
        _two_subclips(),
        overlays=[{"path": "cta/x.mp4", "start_sec": 5.0, "end_sec": 9.0,
                   "mode": "corner", "is_video": True}])
    assert f"scale={int(1080 * 0.42) // 2 * 2}:-2" in g
    assert f"overlay=x={int(1080 * 0.54)}:y={int(1920 * 0.13)}" in g
 
def test_video_overlay_starts_at_event_time():
    """Анимированная врезка должна начинаться в момент события, а не на нулевой секунде."""
    g = filtergraph.build_graph(
        _two_subclips(),
        overlays=[{"path": "cta/cta.mp4", "start_sec": 5.0, "end_sec": 9.0,
                   "mode": "corner", "is_video": True}])
    assert "[1:v:0]trim=duration=4.000,setpts=PTS-STARTPTS+5.000/TB" in g
 
def test_video_overlay_is_trimmed_to_event_window():
    """Длинный исходник врезки не должен продлевать готовый ролик."""
    g = filtergraph.build_graph(
        _two_subclips(),
        overlays=[{"path": "cta/cta.mp4", "start_sec": 5.0, "end_sec": 9.0,
                   "mode": "corner", "is_video": True}])
    assert "[1:v:0]trim=duration=4.000,setpts=PTS-STARTPTS+5.000/TB" in g


def test_corner_overlay_honours_an_explicit_corner():
    g = filtergraph.build_graph(
        _two_subclips(), width=1920, height=1080,
        overlays=[{"path": "cta/x.mp4", "start_sec": 5.0, "end_sec": 9.0,
                   "mode": "corner", "is_video": True, "corner": "bottom_right"}])
    assert f"overlay=x={int(1920 * (0.96 - 0.26))}:y={int(1080 * 0.56)}" in g


def test_plate_is_positioned_by_expression_not_by_numbers():
    """Высота плашки графу неизвестна — низ считается через H-h, иначе она уедет за кадр."""
    g = filtergraph.build_graph(
        _two_subclips(), width=1920, height=1080,
        overlays=[{"path": "assets/cta/legendarium_plate.png", "start_sec": 550.0,
                   "end_sec": 566.0, "mode": "plate", "is_video": False,
                   "corner": "bottom_right"}])
    m = int(1920 * 0.035)
    assert f"overlay=x=W-w-{m}:y=H-h-{int(1080 * 0.08)}" in g


def test_plate_keeps_its_alpha():
    """Плашка — PNG с прозрачностью; без rgba она ляжет чёрным прямоугольником."""
    g = filtergraph.build_graph(
        _two_subclips(), width=1920, height=1080,
        overlays=[{"path": "assets/cta/legendarium_plate.png", "start_sec": 550.0,
                   "end_sec": 566.0, "mode": "plate", "is_video": False}])
    assert "format=rgba" in g
    assert f"overlay=x={int(1920 * 0.035)}:y={int(1080 * 0.07)}" in g


def test_overlays_and_cover_can_coexist():
    g = filtergraph.build_graph(
        _two_subclips(), cover_path="temp/c.png", cover_sec=2.0,
        overlays=[{"path": "cta/x.mp4", "start_sec": 5.0, "end_sec": 7.0,
                   "mode": "fullscreen", "is_video": True}])
    assert "concat=n=3" in g
    assert "[vout]" in g


def test_cover_shifts_overlay_times_because_it_occupies_the_start():
    """Тайминги врезок заданы в таймлайне речи, а обложка вставляется перед ней.

    Без сдвига врезка сработает на cover_sec раньше — на обложке или на первых
    словах хука вместо запланированного места.
    """
    g = filtergraph.build_graph(
        _two_subclips(), cover_path="temp/c.png", cover_sec=2.0,
        overlays=[{"path": "x.png", "start_sec": 5.0, "end_sec": 7.0,
                   "mode": "fullscreen", "is_video": False}])
    assert "between(t,7.000,9.000)" in g


def test_overlay_input_maps_first_video_stream_explicitly():
    """В файлах с камеры (DJI) рядом с видео лежит встроенный mjpeg-превью.

    Без явного :v:0 ffmpeg может взять именно его и собрать слайдшоу.
    """
    g = filtergraph.build_graph(
        _two_subclips(),
        overlays=[{"path": "cta/DJI.MP4", "start_sec": 1.0, "end_sec": 3.0,
                   "mode": "fullscreen", "is_video": True}])
    assert "[1:v:0]" in g


def test_event_without_zoom_keeps_the_zoom_it_inherited():
    """reframe от автотрекинга не задаёт крупность и не имеет права её сбрасывать.

    Иначе машинное ведение за говорящим молча отменяет наезд, поставленный
    visual-director, — а по контракту автотрекинг отвечает только за горизонталь.
    """
    speech = [{"id": "s1", "source_start_sec": 0.0, "source_end_sec": 10.0}]
    events = [
        {"type": "punch_in", "segment_ref": "s1", "start_sec": 0.0, "end_sec": 10.0,
         "crop": {"crop_x": 100, "zoom": 1.3, "ease_ms": 300}},
        {"type": "reframe", "segment_ref": "s1", "start_sec": 5.0, "end_sec": 10.0,
         "crop": {"crop_x": 900}},
    ]
    subs = filtergraph.build_subclips(speech, events)
    assert len(subs) == 2
    assert subs[1].crop_x == 900, "reframe обязан сдвинуть кадр по горизонтали"
    assert subs[1].zoom_to == 1.3, "reframe не должен сбрасывать крупность в 1.0"
    assert subs[1].zoom_from == 1.3


def test_coincident_events_merge_instead_of_one_winning():
    """Автотрекинг и визуальный режиссёр часто целятся в одну и ту же точку.

    reframe задаёт только горизонталь, punch_in — только крупность. Если события
    попадают на один таймкод, они обязаны сложиться: иначе машинный кроп молча
    теряется на каждом первом событии сегмента, и кадр остаётся по центру.
    """
    speech = [{"id": "s1", "source_start_sec": 0.0, "source_end_sec": 10.0}]
    events = [
        {"type": "punch_in", "segment_ref": "s1", "start_sec": 0.0, "end_sec": 10.0,
         "crop": {"zoom": 1.25, "ease_ms": 300}},
        {"type": "reframe", "segment_ref": "s1", "start_sec": 0.0, "end_sec": 10.0,
         "crop": {"crop_x": 1500}},
    ]
    subs = filtergraph.build_subclips(speech, events)
    assert len(subs) == 1
    assert subs[0].crop_x == 1500, "горизонталь от автотрекинга потеряна"
    assert subs[0].zoom_to == 1.25, "крупность от visual-director потеряна"
    assert subs[0].ease_ms == 300


def test_build_subclips_splits_segment_at_crop_event(speech_edits, visual_events):
    subs = filtergraph.build_subclips(speech_edits, visual_events)

    # seg01 целиком один субклип; seg02 разрезан событием punch_in на 8-й секунде
    # кумулятивного времени, то есть на 3-й секунде самого seg02 (seg01 длится 5 с).
    assert len(subs) == 3

    assert subs[0].seg_id == "seg01"
    assert subs[0].src_start == 100.0
    assert subs[0].src_end == 105.0
    assert subs[0].crop_x == 400

    assert subs[1].seg_id == "seg02"
    assert subs[1].src_start == 200.0
    assert subs[1].src_end == 203.0
    assert subs[1].crop_x is None  # до первого события кропа — центр

    assert subs[2].src_start == 203.0
    assert subs[2].src_end == 210.0
    assert subs[2].crop_x == 500
    assert subs[2].zoom_to == 1.25
    assert subs[2].ease_ms == 300


def test_punch_in_eases_from_previous_zoom(speech_edits, visual_events):
    subs = filtergraph.build_subclips(speech_edits, visual_events)
    # наезд начинается с того зума, на котором стоял предыдущий субклип
    assert subs[2].zoom_from == 1.0


def test_subclips_shorter_than_minimum_are_dropped():
    speech = [{"id": "s1", "source_start_sec": 0.0, "source_end_sec": 4.0}]
    events = [
        {"type": "jump_cut", "segment_ref": "s1", "start_sec": 0.05, "end_sec": 4.0,
         "crop": {"crop_x": 10, "zoom": 1.0}},
    ]
    subs = filtergraph.build_subclips(speech, events)
    # событие на 0.05 с ближе минимальной длины субклипа — не порождает огрызок
    assert len(subs) == 1
    assert subs[0].src_start == 0.0
    assert subs[0].src_end == 4.0


from filtergraph import Subclip


def test_static_subclip_has_constant_zoom_expression():
    sub = Subclip(src_start=0, src_end=3, crop_x=None, zoom_from=1.0,
                  zoom_to=1.0, ease_ms=0, seg_id="s1")
    assert filtergraph.zoom_expr(sub, fps=30) == "1.0"


def test_punch_in_expression_uses_smoothstep_over_ease_frames():
    sub = Subclip(src_start=0, src_end=3, crop_x=None, zoom_from=1.0,
                  zoom_to=1.25, ease_ms=300, seg_id="s1")
    expr = filtergraph.zoom_expr(sub, fps=30)
    # 300 мс при 30 fps = 9 кадров
    assert "on/9" in expr
    assert "1.0+" in expr and "0.25" in expr
    assert "(3-2*" in expr  # smoothstep, а не линейная интерполяция


def test_zero_ease_is_instant_cut_not_division_by_zero():
    sub = Subclip(src_start=0, src_end=3, crop_x=None, zoom_from=1.0,
                  zoom_to=1.4, ease_ms=0, seg_id="s1")
    assert filtergraph.zoom_expr(sub, fps=30) == "1.4"


def test_ease_longer_than_subclip_is_clamped_to_subclip_length():
    sub = Subclip(src_start=0, src_end=0.2, crop_x=None, zoom_from=1.0,
                  zoom_to=1.3, ease_ms=5000, seg_id="s1")
    expr = filtergraph.zoom_expr(sub, fps=30)
    assert "on/6" in expr  # 0.2 с * 30 fps = 6 кадров, а не 150


def _two_subclips():
    return [
        Subclip(0.0, 2.0, crop_x=100, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="s1"),
        Subclip(5.0, 8.0, crop_x=None, zoom_from=1.0, zoom_to=1.2, ease_ms=300, seg_id="s2"),
    ]


def test_graph_gives_each_subclip_its_own_input_without_split():
    g = filtergraph.build_graph(_two_subclips())
    # номер потока указан явно: у файлов с камеры рядом с видео лежит mjpeg-превью.
    # split'а нет намеренно: он копил несжатые 4K-кадры поздних субклипов в памяти
    assert "[0:v:0]setpts=PTS-STARTPTS" in g and "[0:a:0]aformat" in g and "[a0]" in g
    assert "[1:v:0]setpts=PTS-STARTPTS" in g and "[1:a:0]aformat" in g and "[a1]" in g
    assert "split=" not in g


def test_subclips_are_trimmed_at_the_input_not_in_the_graph():
    g = filtergraph.build_graph(_two_subclips())
    # начало режется на входе (-ss); в графе допустима только отсечка хвоста до точного
    # числа кадров/сэмплов (trim=end_frame / atrim=end_sample), без перемотки окна
    assert "trim=start" not in g and "trim=duration" not in g
    args = filtergraph.subclip_inputs(_two_subclips(), ["temp/w.mp4"])
    assert args == ["-threads", "1", "-ss", "0.000", "-to", "2.000", "-i", "temp/w.mp4",
                    "-threads", "1", "-ss", "5.000", "-to", "8.000", "-i", "temp/w.mp4"]


def test_each_subclip_decoder_is_single_threaded():
    """Каждый субклип — отдельный вход со своим декодером. С автопотоками декодер h264 берёт
    по потоку на ядро, и на лонге с 70 субклипами контейнер VDS упёрся в pids-limit 512:
    «Error while opening decoder: Resource temporarily unavailable» (реальный лонг).
    concat всё равно читает входы по очереди, узкое место — энкод, а не декод."""
    args = filtergraph.subclip_inputs(_two_subclips(), ["temp/w.mp4"])
    for i in range(0, len(args), 8):
        assert args[i:i + 2] == ["-threads", "1"] and args[i + 6] == "-i"


def test_crop_is_constant_size_and_vertically_centred():
    g = filtergraph.build_graph(_two_subclips(), src_height=2160)
    # 9:16 от высоты 2160 = 1215, округляется до чётного
    assert "crop=1214:2160:100:0" in g
    # без crop_x — по центру по горизонтали
    assert "crop=1214:2160:(in_w-out_w)/2:0" in g


def test_zoompan_centres_the_zoom_and_outputs_target_size():
    g = filtergraph.build_graph(_two_subclips(), width=1080, height=1920, fps=30)
    assert "x='iw/2-(iw/zoom/2)'" in g
    assert "y='ih/2-(ih/zoom/2)'" in g
    assert "s=1080x1920" in g
    assert "d=1" in g

 
def test_zoompan_normalizes_source_fps_before_frame_duplication():
    """25fps исходник не должен давать видео короче аудио при выводе 30fps."""
    g = filtergraph.build_graph(_two_subclips(), width=1080, height=1920, fps=30)
    # субклип подрезан на входе (-ss/-to), trim в графе нет — fps идёт сразу за setpts
    assert "[0:v:0]setpts=PTS-STARTPTS,fps=30," in g

def test_graph_concats_video_and_audio_together():
    g = filtergraph.build_graph(_two_subclips())
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1" in g


def test_single_subclip_still_produces_vout_and_aout():
    g = filtergraph.build_graph([_two_subclips()[0]])
    assert "[vout]" in g and "[aout]" in g


# --- лонг: несколько рабочих окон и горизонтальный формат -------------------

def test_subclips_are_rebased_into_the_window_that_contains_them():
    """У лонга куски речи разбросаны по исходнику, поэтому окон несколько.

    Время субклипа задано в координатах исходника, а граф работает по вырезанному
    окну — без пересчёта хук из 07:51 искался бы в первых секундах окна.
    """
    subs = [
        Subclip(100.0, 105.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="s1"),
        Subclip(1130.0, 1140.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="s2"),
    ]
    out = filtergraph.assign_windows(subs, [(98.0, 107.0), (1128.0, 1142.0)])
    assert (out[0].input_idx, out[0].src_start, out[0].src_end) == (0, 2.0, 7.0)
    assert (out[1].input_idx, out[1].src_start, out[1].src_end) == (1, 2.0, 12.0)


def test_subclip_outside_every_window_is_an_error_not_a_silent_wrong_cut():
    subs = [Subclip(500.0, 505.0, crop_x=None, zoom_from=1.0, zoom_to=1.0,
                    ease_ms=0, seg_id="s1")]
    with pytest.raises(ValueError):
        filtergraph.assign_windows(subs, [(98.0, 107.0)])


def test_each_subclip_reads_its_own_work_window():
    subs = [
        Subclip(0.0, 2.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                seg_id="s1", input_idx=0),
        Subclip(0.0, 3.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                seg_id="s2", input_idx=1),
        Subclip(4.0, 6.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                seg_id="s3", input_idx=1),
    ]
    g = filtergraph.build_graph(subs)
    args = filtergraph.subclip_inputs(subs, ["temp/w00.mp4", "temp/w01.mp4"])
    # субклипы одного окна читают один и тот же файл, но каждый — своим входом
    assert args[7::8] == ["temp/w00.mp4", "temp/w01.mp4", "temp/w01.mp4"]
    assert "[2:v:0]" in g
    # склейка по-прежнему одна на весь ролик, промежуточных файлов между окнами нет
    assert "concat=n=3:v=1:a=1" in g


def test_horizontal_output_crops_nothing_and_clamps_a_vertical_crop_x():
    """Для 16:9 из 16:9 кропать нечего, а crop_x от автотрекинга посчитан под вертикаль
    и увёл бы окно кропа за пределы кадра — ffmpeg на таком графе падает целиком."""
    subs = [Subclip(0.0, 2.0, crop_x=900, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                    seg_id="s1")]
    g = filtergraph.build_graph(subs, width=1920, height=1080,
                                src_width=3840, src_height=2160)
    assert "crop=3840:2160:0:0" in g
    assert "s=1920x1080" in g


def test_external_segment_gets_input_after_windows_and_keeps_its_own_time():
    subs = [
        Subclip(100.0, 105.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                seg_id="s1"),
        Subclip(10.0, 22.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                seg_id="cta", source_path="cta/invite.mp4"),
        Subclip(1130.0, 1140.0, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0,
                seg_id="s2"),
    ]
    out = filtergraph.assign_windows(subs, [(98.0, 107.0), (1128.0, 1142.0)],
                                     external=["cta/invite.mp4"])
    # внешняя врезка — вход №2 (после двух окон), время не сдвигается
    assert (out[1].input_idx, out[1].src_start, out[1].src_end) == (2, 10.0, 22.0)
    assert out[2].input_idx == 1
    args = filtergraph.subclip_inputs(out, ["w0.mp4", "w1.mp4", "cta.mp4"])
    assert args[7::8] == ["w0.mp4", "cta.mp4", "w1.mp4"]


def test_assembly_size_keeps_graph_at_plan_size_when_master_only_upscales():
    """4K-кадры в десятках веток графа из 1080p-исходника не дают деталей, только память:
    лонг из 70 субклипов с мастером 4K был убит OOM-killer'ом на 8 ГБ."""
    # лонг: план 1920x1080, мастер 4K, исходник 1080p → сборка 1080p, апскейл в конце
    assert filtergraph.assembly_size((3840, 2160), (1920, 1080), (1920, 1080)) == (1920, 1080)
    # inherit_source_resolution поднимает 1080p до 1440p — то же самое
    assert filtergraph.assembly_size((2560, 1440), (1920, 1080), (1920, 1080)) == (1920, 1080)
    # шортс из 1080p-исходника: план 1080x1920, мастер 2160x3840
    assert filtergraph.assembly_size((2160, 3840), (1080, 1920), (1920, 1080)) == (1080, 1920)


def test_assembly_size_uses_target_for_4k_source_or_plain_master():
    # из 4K-исходника кроп в родном разрешении — ради этого мастер 4K и нужен
    assert filtergraph.assembly_size((3840, 2160), (1920, 1080), (3840, 2160)) == (3840, 2160)
    assert filtergraph.assembly_size((2160, 3840), (1080, 1920), (3840, 2160)) == (2160, 3840)
    # мастер не крупнее плана — собирать больше нечего
    assert filtergraph.assembly_size((1920, 1080), (1920, 1080), (1920, 1080)) == (1920, 1080)


def test_external_sources_are_unique_and_ordered():
    edits = [{"source_path": None}, {"source_path": "a.mp4"}, {}, {"source_path": "b.mp4"},
             {"source_path": "a.mp4"}]
    assert filtergraph.external_sources(edits) == ["a.mp4", "b.mp4"]


def test_every_input_is_normalised_to_source_geometry_and_audio_format():
    g = filtergraph.build_graph(_two_subclips(), src_width=3840, src_height=2160,
                                width=1920, height=1080)
    # врезка может быть 1080p при 4K-основе — concat требует одинаковый кадр и звук
    assert "scale=3840:2160,crop=" in g
    assert "aformat=sample_rates=48000:channel_layouts=stereo" in g


def test_frame_rate_is_normalised_before_zoompan():
    # zoompan не ресемплирует время: без fps перед ним 30-fps врезка в 25-fps ролике
    # растягивается на 20 % и уводит видео от звука
    g = filtergraph.build_graph(_two_subclips(), fps=25)
    chain = g.split("[v0]")[0]
    assert "setpts=PTS-STARTPTS,fps=25," in chain
    assert chain.index("fps=25,") < chain.index("zoompan=")


def test_punch_in_inherits_crop_x_from_earlier_reframe_in_same_segment():
    """punch_in задаёт только крупность; горизонталь от reframe не должна сбрасываться
    в центр — на общем плане подкаста центр кадра это пустой стол."""
    speech = [{"id": "seg01", "source_start_sec": 100.0, "source_end_sec": 112.0}]
    events = [
        {"type": "reframe", "segment_ref": "seg01", "start_sec": 0.0, "end_sec": 0.1,
         "crop": {"crop_x": 110}},
        {"type": "punch_in", "segment_ref": "seg01", "start_sec": 6.0, "end_sec": 12.0,
         "crop": {"crop_x": None, "zoom": 1.1, "ease_ms": 300}},
    ]
    subs = filtergraph.build_subclips(speech, events)
    assert [sc.crop_x for sc in subs] == [110, 110]
    assert subs[1].zoom_to == 1.1


def test_crop_x_is_not_inherited_across_segments():
    speech = [{"id": "seg01", "source_start_sec": 100.0, "source_end_sec": 106.0},
              {"id": "seg02", "source_start_sec": 300.0, "source_end_sec": 306.0}]
    events = [{"type": "reframe", "segment_ref": "seg01", "start_sec": 0.0, "end_sec": 0.1,
               "crop": {"crop_x": 110}}]
    subs = filtergraph.build_subclips(speech, events)
    assert [sc.crop_x for sc in subs] == [110, None]


def test_external_segment_gain_is_applied_to_its_audio_only():
    speech = [{"id": "s1", "source_start_sec": 10.0, "source_end_sec": 14.0},
              {"id": "cta", "source_start_sec": 1.0, "source_end_sec": 5.0,
               "source_path": "cta/x.mkv", "gain_db": 10}]
    subs = filtergraph.build_subclips(speech, [])
    assert [s.gain_db for s in subs] == [0.0, 10.0]
    g = filtergraph.build_graph(subs, src_width=1920, src_height=1080, width=1920, height=1080)
    assert "[1:a:0]aformat=sample_rates=48000:channel_layouts=stereo,volume=10dB,asetpts" in g
    assert "[0:a:0]aformat=sample_rates=48000:channel_layouts=stereo,asetpts" in g


def test_subclip_bounds_snap_to_frame_grid_without_gaps():
    subs = [Subclip(10.013, 12.527, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="s1"),
            Subclip(12.527, 15.001, crop_x=None, zoom_from=1.0, zoom_to=1.1, ease_ms=300, seg_id="s1"),
            Subclip(20.0, 20.01, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="s2")]
    out = filtergraph.snap_to_frames(subs, 25)
    assert [(s.src_start, s.src_end) for s in out] == [(10.0, 12.52), (12.52, 15.0), (20.0, 20.04)]
    for s in out:
        frames = (s.src_end - s.src_start) * 25
        assert abs(frames - round(frames)) < 1e-6


def test_every_branch_is_trimmed_to_exact_frame_and_sample_count():
    # -to на входе включает кадр ровно на границе, а fps=30 у 25-fps врезки округляет
    # вверх: без trim каждый субклип получал лишний кадр, и план уезжал от рендера
    # (реальный лонг: 50 субклипов -> +19 кадров)
    subs = [Subclip(10.0, 14.8, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="s1"),
            Subclip(0.6, 12.9, crop_x=None, zoom_from=1.0, zoom_to=1.0, ease_ms=0, seg_id="o1",
                    input_idx=1, source_path="assets/x.mp4")]
    g = filtergraph.build_graph(subs, fps=30, src_width=1920, src_height=1080, width=1920, height=1080)
    assert "fps=30,trim=end_frame=144," in g
    assert "fps=30,trim=end_frame=369," in g
    assert "atrim=end_sample=230400[a0]" in g
    assert "atrim=end_sample=590400[a1]" in g
