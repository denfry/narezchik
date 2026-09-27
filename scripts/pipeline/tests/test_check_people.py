import check_people

W = 1080


def _frames(spec, step=0.5):
    """spec: [(сколько кадров подряд, [cx лиц])] -> кадры в формате detect_faces."""
    out, t = [], 0.0
    for n, xs in spec:
        for _ in range(n):
            out.append({"t": round(t, 3), "faces": [{"cx": x} for x in xs]})
            t += step
    return out


def test_person_cut_at_the_edge_is_reported_even_though_a_face_is_found():
    # регрессия: лицо у края кропа, проверка «есть лицо» молчала
    frames = _frames([(2, [540]), (4, [60]), (2, [540])])
    assert check_people._edge_runs(frames, W, min_gap=1.0) == [(1.0, 3.0)]


def test_centred_speaker_is_not_an_edge_problem():
    assert check_people._edge_runs(_frames([(8, [520])]), W, min_gap=1.0) == []


def test_listener_at_the_edge_is_fine_while_the_speaker_is_centred():
    assert check_people._edge_runs(_frames([(8, [540, 1040])]), W, min_gap=1.0) == []


def test_no_face_frames_are_left_to_the_gap_check():
    assert check_people._edge_runs(_frames([(8, [])]), W, min_gap=1.0) == []
