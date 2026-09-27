import build_subtitles as bs


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def _transcript(words):
    return {"segments": [{"start": words[0]["start"], "end": words[-1]["end"],
                          "text": " ".join(w["word"] for w in words), "words": words}]}


def test_output_time_is_cumulative_across_segments():
    """Второй сегмент начинается там, где кончился первый, а не в исходном времени.

    Ровно эта арифметика раньше делалась руками и давала рассинхрон: субтитр
    второго куска уезжал на разницу между source_start и позицией в готовом ролике.
    """
    tr = _transcript([_w("раз", 10.0, 10.5), _w("два", 100.0, 100.5)])
    plan = {"speech_edits": [
        {"id": "a", "source_start_sec": 10.0, "source_end_sec": 12.0},
        {"id": "b", "source_start_sec": 100.0, "source_end_sec": 101.0},
    ]}
    events, total, skipped = bs.build(plan, tr, "chunk", 42, 3.2, 0.45)
    assert [e["text"] for e in events] == ["раз", "два"]
    assert events[0]["start_sec"] == 0.0          # 10.0 - 10.0 + 0
    assert events[1]["start_sec"] == 2.0          # 100.0 - 100.0 + длительность первого
    assert total == 3.0 and skipped == []


def test_external_insert_shifts_timeline_but_gets_no_subtitles():
    """У врезки со своим звуком (зазывалка) речи в этом транскрипте нет.

    Пропустить её мало — надо всё равно сдвинуть на её длительность, иначе всё,
    что после врезки, поедет вперёд на её длину.
    """
    tr = _transcript([_w("после", 50.0, 50.4)])
    plan = {"speech_edits": [
        {"id": "cta", "source_start_sec": 0.0, "source_end_sec": 8.0,
         "source_path": "cta/invite.mkv"},
        {"id": "a", "source_start_sec": 50.0, "source_end_sec": 51.0},
    ]}
    events, total, skipped = bs.build(plan, tr, "chunk", 42, 3.2, 0.45)
    assert skipped == ["cta"]
    assert len(events) == 1 and events[0]["start_sec"] == 8.0
    assert total == 9.0


def test_chunk_breaks_on_pause_length_and_duration():
    words = [_w("одно", 0.0, 0.4), _w("слово", 0.4, 0.8),
             _w("после", 2.0, 2.4)]  # пауза 1.2с > PAUSE_GAP
    assert [len(c) for c in bs.chunk_words(words, 42, 3.2, 0.45)] == [2, 1]
    long_line = [_w("длинноеслово", i * 0.3, i * 0.3 + 0.3) for i in range(6)]
    assert len(bs.chunk_words(long_line, 42, 3.2, 0.45)) > 1


def test_word_boundary_is_not_duplicated_between_segments():
    """Слово на стыке принадлежит тому сегменту, в который попала его середина."""
    tr = _transcript([_w("край", 9.8, 10.4)])  # середина 10.1
    plan_a = {"speech_edits": [{"id": "a", "source_start_sec": 5.0, "source_end_sec": 10.0}]}
    plan_b = {"speech_edits": [{"id": "b", "source_start_sec": 10.0, "source_end_sec": 15.0}]}
    assert bs.build(plan_a, tr, "chunk", 42, 3.2, 0.45)[0] == []
    assert len(bs.build(plan_b, tr, "chunk", 42, 3.2, 0.45)[0]) == 1


def test_edge_word_is_clamped_into_the_segment():
    """Слово попадает в сегмент по середине — значит на краю оно торчит наружу.

    Без прижатия первое слово получало отрицательное время: план не проходил
    валидацию (start_sec >= 0), а субтитр уезжал за начало ролика.
    """
    tr = _transcript([_w("край", 9.5, 10.5), _w("внутри", 11.0, 11.4)])
    plan = {"speech_edits": [{"id": "a", "source_start_sec": 10.0, "source_end_sec": 11.5}]}
    events, total, _ = bs.build(plan, tr, "word", 42, 3.2, 0.45)
    words = events[0]["words"]
    assert words[0]["word"] == "край"
    assert words[0]["start_sec"] == 0.0          # было -0.5
    assert all(0.0 <= w["start_sec"] <= total for w in words)
    assert all(w["end_sec"] <= total for w in words)
