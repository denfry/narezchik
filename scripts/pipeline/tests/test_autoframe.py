import autoframe

CROP_W = 606  # вертикальный кроп из 1080p-исходника
SAME = CROP_W * autoframe.SAME_PERSON_FRAC
SPEAKER_A, SPEAKER_B = 460, 1440  # центры лиц на общем плане подкаста


def _samples(spec, step=0.25):
    """spec: [(сколько выборок подряд, cx)] -> [(t, cx)]."""
    out, t = [], 0.0
    for n, cx in spec:
        for _ in range(n):
            out.append((round(t, 3), cx))
            t += step
    return out


def test_active_speaker_is_the_one_with_open_jaw():
    frame = {"t": 1.0, "faces": [
        {"cx": 300, "cy": 400, "w": 100, "h": 120, "jaw_open": 0.02},
        {"cx": 900, "cy": 400, "w": 110, "h": 130, "jaw_open": 0.41},
    ]}
    assert autoframe.pick_active(frame, audio_rms=0.3)["cx"] == 900


def test_silence_keeps_the_largest_face_instead_of_guessing():
    frame = {"t": 1.0, "faces": [
        {"cx": 300, "cy": 400, "w": 200, "h": 240, "jaw_open": 0.01},
        {"cx": 900, "cy": 400, "w": 90, "h": 110, "jaw_open": 0.02},
    ]}
    assert autoframe.pick_active(frame, audio_rms=0.001)["cx"] == 300


def test_no_faces_returns_none():
    assert autoframe.pick_active({"t": 0.0, "faces": []}, audio_rms=0.5) is None


def test_shot_starts_on_the_person_not_on_a_drifting_average():
    # регрессия: кроп стартовал со старой позиции и ступеньками
    # полз к человеку через пустой стол
    runs = autoframe.frame_shot(_samples([(12, SPEAKER_B)]), 0.0, same_px=SAME)
    assert runs == [(0.0, SPEAKER_B)]


def test_first_position_uses_the_majority_of_the_first_second():
    runs = autoframe.frame_shot(_samples([(1, SPEAKER_A), (11, SPEAKER_B)]), 0.0, same_px=SAME)
    assert runs == [(0.0, SPEAKER_B)]


def test_brief_listener_jaw_noise_does_not_cut_the_frame():
    runs = autoframe.frame_shot(_samples([(8, SPEAKER_A), (2, SPEAKER_B), (8, SPEAKER_A)]),
                                0.0, same_px=SAME)
    assert runs == [(0.0, SPEAKER_A)]


def test_sustained_speaker_change_is_a_hard_cut_not_a_pan():
    runs = autoframe.frame_shot(_samples([(8, SPEAKER_A), (12, SPEAKER_B)]), 0.0, same_px=SAME)
    # ровно две позиции, без промежуточных шагов, перерез в момент начала реплики
    assert runs == [(0.0, SPEAKER_A), (2.0, SPEAKER_B)]


def test_single_noisy_sample_inside_a_reply_does_not_cancel_the_cut():
    runs = autoframe.frame_shot(
        _samples([(8, SPEAKER_A), (3, SPEAKER_B), (1, SPEAKER_A), (6, SPEAKER_B)]), 0.0, same_px=SAME)
    assert [cx for _, cx in runs] == [SPEAKER_A, SPEAKER_B]
    assert runs[1][0] == 2.0


def test_switch_waits_for_minimum_hold_of_the_current_position():
    runs = autoframe.frame_shot(_samples([(2, SPEAKER_A), (12, SPEAKER_B)]), 0.0, same_px=SAME)
    assert runs[1][0] >= autoframe.MIN_HOLD_SEC


def test_small_head_movement_stays_one_position():
    runs = autoframe.frame_shot(_samples([(4, 460), (4, 500), (4, 430)]), 0.0, same_px=SAME)
    assert len(runs) == 1


def test_shot_without_faces_leaves_horizontal_undecided():
    assert autoframe.frame_shot([(0.0, None), (0.25, None)], 0.0, same_px=SAME) == [(0.0, None)]


def test_camera_cuts_split_a_speech_piece_into_shots():
    cuts = [5.0, 12.5, 12.6, 19.95, 25.0]
    # 12.6 — мелькание короче MIN_SHOT_SEC, 19.95 — у самого края куска
    assert autoframe.shot_ranges(10.0, 20.0, cuts) == [(10.0, 12.5), (12.5, 20.0)]


def test_scene_cut_times_are_read_from_showinfo():
    text = ("[Parsed_showinfo_2 @ 0x1] n:   0 pts:  3100 pts_time:12.4    duration:1\n"
            "[Parsed_showinfo_2 @ 0x1] n:   1 pts:  5000 pts_time:20      duration:1\n")
    assert autoframe.parse_scene_cuts(text) == [12.4, 20.0]


def test_shot_events_are_contiguous_clamped_and_zoomless():
    ev = autoframe.shot_events([(0.0, 50), (2.0, 1900)], 5.0, "seg01",
                               offset=3.0, src_width=1920, crop_w=CROP_W)
    assert [(e["start_sec"], e["end_sec"]) for e in ev] == [(3.0, 5.0), (5.0, 8.0)]
    assert ev[0]["crop"]["crop_x"] == 0
    assert ev[1]["crop"]["crop_x"] == 1920 - CROP_W
    assert all("zoom" not in e["crop"] for e in ev)
    assert all(e["reason"].startswith("autoframe") and e.get("purpose") for e in ev)
