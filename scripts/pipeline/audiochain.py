"""Чистая сборка строк аудиофильтров. ffmpeg отсюда не запускается.

Порядок фильтров в голосовой цепочке не произволен: сначала убирается то, что не
должно попасть в компрессор (низкочастотный гул, свистящие), потом работает динамика,
и только в конце выравнивается громкость. Компрессия до эквализации даёт худший
результат — компрессор реагирует на ещё не срезанный гул.
"""

VOICE_FILTERS = (
    "highpass=f=80",                       # срез гула помещения и рокота микрофона
    "deesser=i=0.4:m=0.5:f=0.15",          # свистящие
    "acompressor=threshold=-18dB:ratio=3:attack=8:release=180:makeup=2",
    "equalizer=f=3500:width_type=q:w=1.2:g=3",   # разборчивость
    "alimiter=limit=0.95",                 # страховка от клиппинга перед loudnorm
)


def voice_chain(target_lufs=-14.0):
    return ",".join(VOICE_FILTERS) + f",loudnorm=I={target_lufs:g}:TP=-1.5:LRA=11"


def music_gate(start=None, end=None, fade_in=0.6, fade_out=0.3):
    """Фильтры, включающие музыку событием, а не с нулевого кадра.

    Стандарт канала: подложка входит после хука (обычно на 0:03) и обрывается за
    ~0,3 с до ударного слова, чтобы панчлайн прозвучал в тишине (music-stop).
    До start и после end музыка глушится volume=0, на границах — короткие afade,
    чтобы включение не щёлкало. Ничего не задано — пустая строка, музыка идёт всю длину.
    """
    parts = []
    if start is not None and start > 0:
        parts.append(f"volume=0:enable='lt(t,{start:.3f})'")
        parts.append(f"afade=t=in:st={start:.3f}:d={fade_in:g}")
    if end is not None:
        parts.append(f"afade=t=out:st={max(0.0, end - fade_out):.3f}:d={fade_out:g}")
        parts.append(f"volume=0:enable='gt(t,{end:.3f})'")
    return ("," + ",".join(parts)) if parts else ""


def music_windows_gate(windows, fade_in=0.8, fade_out=1.2):
    """Музыка звучит только внутри окон — «событием на швах», а не фоном под спором.

    windows — [(start, end)] или [(start, end, level)] по шкале готового ролика; level —
    множитель громкости окна (по умолчанию 1): меньше единицы — тише, под личной историей;
    больше — громче, на заставке, где голоса нет и дакинг не опускает подложку.

    Цепочка из нескольких music_gate здесь не работает: afade=t=in глушит всё ДО своей
    точки, afade=t=out — всё ПОСЛЕ, и зазвучало бы только первое окно. Поэтому огибающая —
    одно выражение volume с eval=frame: сумма трапеций по окнам (линейный подъём, полка,
    спад), вне окон ноль. asetnsamples мельчит аудиокадры до 256 сэмплов (~5 мс): по
    умолчанию кадр ~21 мс, и громкость на подъёме менялась бы слышными ступеньками.
    Внахлёст ставятся только окна разной громкости со встречными подъёмом и спадом — это
    кроссфейд; два полных окна внахлёст сложились бы в двойную громкость.
    """
    if not windows:
        return ""
    terms = []
    for w in windows:
        s, e = float(w[0]), float(w[1])
        level = float(w[2]) if len(w) > 2 else 1.0
        half = max(0.05, (e - s) / 2)
        fi, fo = min(fade_in, half), min(fade_out, half)
        term = f"clip((t-{s:.3f})/{fi:g},0,1)*clip(({e:.3f}-t)/{fo:g},0,1)"
        terms.append(term if abs(level - 1.0) < 1e-3 else f"{level:g}*{term}")
    return f",asetnsamples=n=256,volume='{'+'.join(terms)}':eval=frame"


def music_mix(music_gain_db, duck, target_lufs, music_index=1,
              music_start=None, music_end=None, music_windows=None):
    """Смешать уже нормализованный [voice] и музыку [<music_index>:a] в [afinal].

    music_index — номер входа ffmpeg с музыкой. У Shorts рабочее окно одно, музыка идёт
    вторым входом, и единица тут была верной по случайности. У лонга окон несколько
    (см. workfile.window_clusters), музыка стоит СРАЗУ ЗА НИМИ, и жёстко зашитая [1:a]
    брала аудио второго рабочего окна: вместо подложки в микс уходил кусок той же речи
    на -24 дБ. На слух это читается как эхо/каша под голосом, а не как отсутствие музыки,
    поэтому индекс задаётся явно.

    Громкость выравнивается ДО микса, на голосе, а на выходе стоит только лимитер.
    Раньше loudnorm стоял на готовой смеси, и уровень музыки начинал гулять вслед за
    динамикой речи: после минуты громкого монолога динамическая нормализация держала
    низкое усиление, и подложка под финальной плашкой проваливалась на 15 дБ ниже,
    чем под обложкой в начале. Аргумент target_lufs сохранён для совместимости
    вызова и в самой смеси больше не используется.

    normalize=0 у amix обязателен: по умолчанию amix делит сигнал на число входов,
    то есть уже выровненный голос теряет 6 дБ и весь ролик выходит тише цели.
    Раньше это скрывал финальный loudnorm, добиравший громкость обратно.

    sidechaincompress потребляет оба своих входа, поэтому голос предварительно
    раздваивается через asplit: одна копия идёт ключом сайдчейна, вторая — в микс.

    Порядок входов у sidechaincompress: ПЕРВЫЙ — сигнал, который сжимают, ВТОРОЙ —
    ключ, по которому считают сжатие. Значит музыка первая, голос второй. Обратный
    порядок выглядит правдоподобно и молча ломает всё: на выходе оказывается
    сжатый голос, музыка не попадает в микс вообще, а в amix голос идёт дважды.
    Слышно это не сразу — голос просто звучит плотнее, — но подложки в ролике нет.
    """
    # окна важнее пары start/end: подложка «на швах» (хук, байт, CTA), под спором тишина
    gate = (music_windows_gate(music_windows) if music_windows
            else music_gate(music_start, music_end))
    if not duck:
        return (
            f"[{music_index}:a]volume={music_gain_db:g}dB{gate}[music];"
            f"[voice][music]amix=inputs=2:duration=first:normalize=0[mixed];"
            f"[mixed]alimiter=limit=0.95[afinal]"
        )
    return (
        f"[voice]asplit=2[voicekey][voicemix];"
        f"[{music_index}:a]volume={music_gain_db:g}dB{gate}[music];"
        f"[music][voicekey]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=300[musicduck];"
        f"[voicemix][musicduck]amix=inputs=2:duration=first:normalize=0[mixed];"
        f"[mixed]alimiter=limit=0.95[afinal]"
    )


def _fmt(x):
    return f"{x:g}"


def bleep_chain(windows, src_label, dst_label, offset=0.0, tone_hz=1000, tone_gain=0.25):
    """Запикать окна (start, end) выходной шкалы: голос в окне глушится, поверх — тон.

    Окна приходят по той же шкале, что subtitle_events (0 = начало речи), поэтому при
    вклеенной обложке они сдвигаются на offset так же, как субтитры в render_short.

    Цепочка ставится ПОСЛЕ voice_chain: loudnorm не должен мерить тон как часть
    речи, иначе он бы поджал громкость всего ролика под пиковую громкость бипа.
    Тон генерируется одним aevalsrc с суммой between() — в окне выражение даёт 1,
    вне окна 0, так что отдельный volume-гейт на тоне не нужен. duration=first у
    amix обрезает бесконечный источник по длине голоса.
    """
    if not windows:
        return f"[{src_label}]anull[{dst_label}]"
    gates = "+".join(
        f"between(t,{_fmt(s + offset)},{_fmt(e + offset)})" for s, e in windows
    )
    return (
        # loudnorm отдаёт 192 кГц со своими PTS; без ресемплинга и пересчёта PTS amix
        # с генератором тона молча обрезал хвост речи (проверено: -91 dB после 6-й секунды)
        # asetnsamples: enable у volume срабатывает на весь аудиокадр (~21 мс), и после окна
        # оставался кадр цифровой тишины; тон в стерео — моно-тон в amix сводил в моно весь микс
        # (замерено: выход «aac 1ch»)
        f"[{src_label}]aresample=48000,asetpts=N/SR/TB,asetnsamples=n=64,volume=0:enable='{gates}'[bleepmute];"
        f"aevalsrc='{tone_gain:g}*sin(2*PI*{tone_hz}*t)*({gates})':s=48000:c=stereo[bleeptone];"
        f"[bleepmute][bleeptone]amix=inputs=2:duration=first:normalize=0[{dst_label}]"
    )
