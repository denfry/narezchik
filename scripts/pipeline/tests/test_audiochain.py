import audiochain


def test_voice_chain_order_is_cleanup_then_dynamics_then_loudness():
    chain = audiochain.voice_chain()
    order = [chain.index(f) for f in
             ("highpass", "deesser", "acompressor", "equalizer", "alimiter", "loudnorm")]
    assert order == sorted(order), f"порядок фильтров нарушен: {chain}"


def test_voice_chain_cuts_rumble_at_80hz():
    assert "highpass=f=80" in audiochain.voice_chain()


def test_voice_chain_normalises_to_requested_loudness():
    assert "I=-14" in audiochain.voice_chain(target_lufs=-14.0)
    assert "I=-16" in audiochain.voice_chain(target_lufs=-16.0)


def test_music_mix_without_duck_has_no_sidechain():
    m = audiochain.music_mix(music_gain_db=-18, duck=False, target_lufs=-14.0)
    assert "sidechaincompress" not in m
    assert "volume=-18dB" in m
    assert "[afinal]" in m


def test_music_mix_with_duck_uses_voice_as_sidechain_key():
    m = audiochain.music_mix(music_gain_db=-18, duck=True, target_lufs=-14.0)
    assert "sidechaincompress" in m
    # У sidechaincompress ПЕРВЫЙ вход — обрабатываемый сигнал, ВТОРОЙ — ключ.
    # Обрабатываем музыку, ключом служит голос. Обратный порядок компрессирует
    # голос по музыке, а сама музыка тогда не попадает в микс вообще — ровно это
    # и происходило: подложки в роликах не было, зато голос подмешивался дважды.
    assert "[music][voicekey]sidechaincompress" in m


def test_music_input_index_follows_the_number_of_work_windows():
    """У лонга рабочих окон несколько, и музыка стоит входом сразу за ними.

    Жёстко зашитая [1:a] брала аудио ВТОРОГО рабочего окна: под голос подмешивался
    кусок той же речи на -24 дБ, а подложки в ролике не было вовсе.
    """
    short = audiochain.music_mix(music_gain_db=-18, duck=True, target_lufs=-14.0)
    assert "[1:a]volume" in short, "у шортса одно окно — музыка второй вход"

    long_form = audiochain.music_mix(music_gain_db=-24, duck=True, target_lufs=-14.0,
                                     music_index=10)
    assert "[10:a]volume" in long_form
    assert "[1:a]" not in long_form


def test_ducked_music_survives_when_the_voice_is_silent():
    """Под обложкой голос — цифровая тишина, и музыка обязана звучать.

    При перепутанных входах sidechaincompress здесь получалась мёртвая тишина
    в первые секунды ролика.
    """
    m = audiochain.music_mix(music_gain_db=-18, duck=True, target_lufs=-14.0)
    mix = [p for p in m.split(";") if "amix" in p][0]
    assert "[musicduck]" in mix, "в микс должна попадать именно музыка"
    assert "[voicemix]" in mix, "и голос отдельной ветвью"


def test_music_mix_duplicates_voice_because_sidechain_consumes_a_stream():
    m = audiochain.music_mix(music_gain_db=-18, duck=True, target_lufs=-14.0)
    assert "asplit=2" in m


def test_bleep_chain_empty_is_passthrough():
    assert audiochain.bleep_chain([], "aout", "voice") == "[aout]anull[voice]"


def test_bleep_chain_mutes_voice_and_adds_tone_in_windows():
    b = audiochain.bleep_chain([(1.0, 1.5), (4.25, 4.9)], "aout", "voice", offset=0.0)
    assert "between(t,1,1.5)" in b and "between(t,4.25,4.9)" in b
    assert "volume=0" in b                 # голос в окне глушится
    assert "aevalsrc" in b and "1000" in b # тон 1 кГц только внутри окон
    assert "normalize=0" in b              # микс не режет уровень голоса
    assert "aresample=48000,asetpts=N/SR/TB" in b  # иначе после loudnorm amix обрезает хвост
    assert b.endswith("[voice]")


def test_bleep_chain_shifts_windows_by_cover_offset():
    b = audiochain.bleep_chain([(1.0, 1.5)], "aout", "voice", offset=2.5)
    assert "between(t,3.5,4)" in b


def test_music_gate_starts_after_hook_and_stops_before_punchline():
    """Стандарт: подложка входит на 0:03, а не с кадра 0, и глохнет за 0,3 с до удара."""
    m = audiochain.music_mix(music_gain_db=-22, duck=True, target_lufs=-14.0,
                             music_start=3.0, music_end=40.0)
    music = [p for p in m.split(";") if p.startswith("[1:a]")][0]
    assert "volume=0:enable='lt(t,3.000)'" in music
    assert "afade=t=in:st=3.000" in music
    assert "afade=t=out:st=39.700:d=0.3" in music
    assert "volume=0:enable='gt(t,40.000)'" in music
    # без окна — старое поведение, гейта нет
    assert "enable=" not in audiochain.music_mix(-22, True, -14.0)
