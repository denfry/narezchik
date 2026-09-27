"""Чистое построение ffmpeg filter_complex из edit plan.

Ни одна функция здесь не запускает ffmpeg и не трогает файловую систему — это
делает render_short.py. Такое разделение позволяет покрыть логику сборки графа
обычными unit-тестами.
"""
from dataclasses import dataclass, replace

CROP_TYPES = ("talking_head", "punch_in", "punch_out", "reframe", "jump_cut")
MIN_SUBCLIP_SEC = 0.15
DEFAULT_EASE_MS = 300
# Доля высоты выходного кадра, на которой держатся глаза говорящего при наезде.
# zoompan по умолчанию увеличивает кадр от центра, и на крупном плане глаза уезжают
# вверх тем сильнее, чем больше зум: смена крупности внутри реплики читается как
# скачок, а не как вторая камера. 0.40 — стандарт монтажа говорящей головы (глаза
# чуть выше середины). Работает только если в событии кропа задан eye_y.
EYE_TARGET_Y = 0.40


@dataclass
class Subclip:
    src_start: float
    src_end: float
    crop_x: float | None
    zoom_from: float
    zoom_to: float
    ease_ms: int
    seg_id: str
    input_idx: int = 0
    # Сегмент из другого файла (врезка-зазывалка из cta/): читается
    # напрямую, без рабочего окна, со своим звуком — это полноценный кусок монтажа,
    # а не картинка поверх речи.
    source_path: str | None = None
    # Громкость врезки со своим звуком, дБ. Дубль зазывалки из cta/ записан на
    # петличку на ~11 LU тише подкаста, и loudnorm на общем миксе этот провал не вытягивает.
    gain_db: float = 0.0
    # Высота линии глаз говорящего в кропнутом кадре, доля 0..1 сверху. Считает
    # autoframe.py по лендмаркам. None — наезд идёт от центра кадра, как раньше.
    eye_y: float | None = None

    @property
    def duration(self):
        return self.src_end - self.src_start


def build_subclips(speech_edits, visual_events):
    """Разложить speech_edits на субклипы, разрезая их событиями кропа.

    visual_events[].start_sec задан в кумулятивном времени готового ролика,
    поэтому пересчитывается в локальное время сегмента.
    """
    subclips = []
    cum_offset = 0.0
    current_zoom = 1.0

    for seg in speech_edits:
        seg_id = seg["id"]
        s, e = seg["source_start_sec"], seg["source_end_sec"]
        dur = e - s

        events = [
            ev for ev in visual_events
            if ev.get("segment_ref") == seg_id and ev.get("type") in CROP_TYPES
        ]
        cuts = []
        for ev in events:
            local = ev.get("start_sec", cum_offset) - cum_offset
            local = max(0.0, min(dur, local))
            crop = ev.get("crop") or {}
            default_ease = DEFAULT_EASE_MS if ev["type"] in ("punch_in", "punch_out") else 0
            # zoom=None означает «крупность не задана, унаследовать текущую», а не 1.0:
            # события reframe от автотрекинга отвечают только за горизонталь и не имеют
            # права отменять наезд, поставленный visual-director.
            cuts.append((local, crop.get("crop_x"), crop.get("zoom"),
                         crop.get("ease_ms", default_ease), crop.get("eye_y")))
        cuts.sort(key=lambda c: c[0])

        if not cuts or cuts[0][0] > MIN_SUBCLIP_SEC:
            cuts.insert(0, (0.0, None, current_zoom, 0, None))
        else:
            # событие стоит почти в начале сегмента — прижимаем его к нулю, иначе
            # первые доли секунды сегмента выпали бы из монтажа вовсе
            cuts[0] = (0.0,) + tuple(cuts[0][1:])

        # События ближе MIN_SUBCLIP_SEC друг к другу не порождают отдельный субклип, но и
        # не выбрасываются: их свойства складываются. reframe от автотрекинга задаёт
        # только горизонталь, punch_in — только крупность, и оба обычно целятся в начало
        # сегмента. При простом отбрасывании машинный кроп терялся бы каждый раз.
        merged = [cuts[0]]
        for c in cuts[1:]:
            if c[0] - merged[-1][0] < MIN_SUBCLIP_SEC:
                prev = merged[-1]
                merged[-1] = (
                    prev[0],
                    prev[1] if prev[1] is not None else c[1],
                    prev[2] if prev[2] is not None else c[2],
                    prev[3] if prev[2] is not None else c[3],
                    prev[4] if prev[4] is not None else c[4],
                )
                continue
            merged.append(c)

        # crop_x наследуется внутри сегмента так же, как зум: punch_in задаёт только
        # крупность, и без наследования он сбрасывал горизонталь в центр кадра — на
        # общем плане подкаста это пустая середина стола (short05, 31–35 с). Между
        # сегментами не наследуется: там исходник прыгает и старый кроп бессмыслен.
        current_crop_x = None
        current_eye_y = None
        for idx, (local_start, crop_x, zoom, ease_ms, eye_y) in enumerate(merged):
            local_end = merged[idx + 1][0] if idx + 1 < len(merged) else dur
            if local_end - local_start < MIN_SUBCLIP_SEC:
                continue
            target_zoom = current_zoom if zoom is None else zoom
            if crop_x is None:
                crop_x = current_crop_x
            current_crop_x = crop_x
            # eye_y наследуется внутри сегмента по той же причине, что и crop_x:
            # punch_in обычно задаёт только крупность, а линию глаз ставит reframe.
            if eye_y is None:
                eye_y = current_eye_y
            current_eye_y = eye_y
            subclips.append(Subclip(
                src_start=s + local_start,
                src_end=s + local_end,
                crop_x=crop_x,
                zoom_from=current_zoom,
                zoom_to=target_zoom,
                ease_ms=ease_ms,
                seg_id=seg_id,
                source_path=seg.get("source_path"),
                gain_db=float(seg.get("gain_db", 0) or 0),
                eye_y=eye_y,
            ))
            current_zoom = target_zoom

        cum_offset += dur

    return subclips


def external_sources(speech_edits):
    """Уникальные внешние файлы сегментов (source_path) в порядке первого появления."""
    seen = []
    for seg in speech_edits:
        p = seg.get("source_path")
        if p and p not in seen:
            seen.append(p)
    return seen


def assign_windows(subclips, windows, external=None):
    """Привязать субклипы (их время задано в координатах исходника) к рабочим окнам
    и пересчитать время внутрь окна.

    Субклипы с source_path идут отдельными входами ПОСЛЕ рабочих окон (в порядке
    списка external), время у них не сдвигается — это координаты самого файла.

    Возвращает новый список — исходный не меняется. Если субклип не попал ни в одно
    окно, это ошибка построения окон, а не повод молча отрендерить не тот кусок.
    """
    external = list(external or [])
    out = []
    for sub in subclips:
        if sub.source_path:
            if sub.source_path not in external:
                raise ValueError(f"субклип {sub.seg_id}: внешний файл {sub.source_path} "
                                 f"не передан в external")
            out.append(replace(sub, input_idx=len(windows) + external.index(sub.source_path)))
            continue
        for idx, (win_start, win_end) in enumerate(windows):
            if sub.src_start >= win_start - 1e-6 and sub.src_end <= win_end + 1e-6:
                out.append(replace(sub,
                                   src_start=sub.src_start - win_start,
                                   src_end=sub.src_end - win_start,
                                   input_idx=idx))
                break
        else:
            raise ValueError(
                f"субклип {sub.seg_id} {sub.src_start:.2f}-{sub.src_end:.2f} "
                f"не попал ни в одно рабочее окно {windows}"
            )
    return out


def snap_to_frames(subclips, fps):
    """Границы субклипов — на сетку кадров (кратно 1/fps).

    concat склеивает видео и звук каждого субклипа и добивает более короткий поток до длины
    более длинного. Видео — всегда целое число кадров, звук режется по сэмплу, поэтому
    субклип, не кратный кадру, получал до 40 мс цифровой тишины в звуке. На лонге с 56
    субклипами это дало +0.8 с к концу ролика: субтитры, бипы и плашки размечены по плану и
    уезжали раньше голоса (замер на лонге с 56 субклипами: +0.36 с на 5:35, +0.79 с на 12:37), а в
    сплошной речи на границах punch-in были слышны провалы по 20 мс.

    Округляются концы, а не длительности: соседние субклипы получают одну и ту же границу, и
    ошибка не накапливается. Чтобы план совпал с рендером кадр в кадр, границы сегментов и
    события в самом плане тоже стоит ставить на сетку (рабочее окно тогда начинается на кадре).
    """
    step = 1.0 / fps
    out = []
    for sub in subclips:
        s = round(round(sub.src_start * fps) * step, 6)
        e = round(round(sub.src_end * fps) * step, 6)
        if e - s < step - 1e-9:
            e = round(s + step, 6)
        out.append(replace(sub, src_start=s, src_end=e))
    return out


def zoom_expr(sub, fps):
    """Выражение параметра z фильтра zoompan.

    Считается от номера выходного кадра on, а не инкрементом от предыдущего zoom:
    инкрементная форма ('min(zoom+0.001,1.25)') накапливает погрешность и зависит
    от того, сколько раз zoompan успел вызваться, — до конечного зума она не
    доезжает предсказуемо.
    """
    z0, z1 = round(sub.zoom_from, 4), round(sub.zoom_to, 4)
    if abs(z1 - z0) < 1e-6 or sub.ease_ms <= 0:
        return str(z1)

    ease_frames = max(1, int(round(sub.ease_ms / 1000.0 * fps)))
    max_frames = max(1, int(round(sub.duration * fps)))
    ease_frames = min(ease_frames, max_frames)

    t = f"min(1,on/{ease_frames})"
    smooth = f"({t}*{t}*(3-2*{t}))"
    return f"{z0}+{round(z1 - z0, 4)}*{smooth}"



def zoom_y_expr(sub, eye_target=EYE_TARGET_Y):
    """Выражение параметра y фильтра zoompan — высота окна наезда.

    Без eye_y окно центрируется по вертикали, как раньше. С eye_y окно смещается так,
    чтобы линия глаз осталась на постоянной высоте кадра: zoompan берёт окно высотой
    ih/zoom начиная с y, значит точка eye_y*ih попадёт на долю (eye_y*ih - y)*zoom/ih
    выходного кадра. Приравняв её к eye_target, получаем y = ih*(eye_y - eye_target/zoom).
    Зажимаем в [0, ih-ih/zoom], иначе окно уедет за кадр и ffmpeg упадёт на всём графе.
    """
    if sub.eye_y is None:
        return "ih/2-(ih/zoom/2)"
    eye = min(1.0, max(0.0, float(sub.eye_y)))
    target = min(1.0, max(0.0, float(eye_target)))
    raw = f"ih*({eye:.4f}-{target:.4f}/zoom)"
    return f"max(0,min(ih-ih/zoom,{raw}))"


def subclip_inputs(subclips, works):
    """Аргументы `-ss/-to -i` для ffmpeg: по одному входу на субклип.

    Подрезка делается на входе, а не фильтром trim: так декодер читает только
    нужный кусок, а кадры не копятся в очередях графа (см. build_graph).

    Декодер каждого входа — в один поток. С автопотоками h264 берёт по потоку на ядро, и
    лонг с 70 субклипами упёрся в pids-limit контейнера VDS (EAGAIN при открытии декодера,
    реальный случай); заодно в памяти не висят кадровые буферы десятков потоков.
    concat читает входы по очереди, узкое место рендера — энкод, а не декод.
    """
    args = []
    for sub in subclips:
        args += ["-threads", "1", "-ss", f"{sub.src_start:.3f}", "-to", f"{sub.src_end:.3f}",
                 "-i", works[sub.input_idx]]
    return args


def assembly_size(target, plan, src):
    """Размер кадра, в котором собирается граф: кроп, наезды, склейка, врезки.

    target — размер мастера, plan — render_settings.width/height, src — размер исходника.
    Если мастер крупнее плана (master_4k и inherit_source_resolution поднимают 1080 до 2160
    или 1440), а исходник не крупнее плана, 4K-кадры в каждой из десятков веток графа
    деталей не добавляют, только память: лонг с 70 субклипами был убит OOM-killer'ом
    на 8 ГБ. Тогда граф собирается в размере плана, а до мастера кадр
    поднимается одним lanczos-скейлом перед цветокором и субтитрами. Из 4K-исходника сборка
    идёт в размере мастера: кроп в родном разрешении — ради этого 4K и нужен.
    """
    (tw, th), (pw, ph), (sw, sh) = target, plan, src
    if tw * th <= pw * ph or min(sw, sh) > min(pw, ph):
        return tw, th
    return pw, ph


def even(n):
    """ffmpeg с libx264 требует чётные размеры кадра."""
    return int(n) // 2 * 2


def build_graph(subclips, *, width=1080, height=1920, fps=30.0, src_height=2160,
                src_width=None, cover_path=None, cover_sec=2.5, tail_path=None,
                tail_sec=2.5, overlays=None, first_extra_input=1,
                eye_target=EYE_TARGET_Y):
    """Собрать filter_complex целиком. Выходные метки — [vout] и [aout].

    Один граф вместо цепочки промежуточных файлов: это убирает и щелчки на стыках
    (AAC priming padding в начале каждого промежуточного файла), и деградацию
    качества от повторных перекодов.

    cover_path вклеивается отдельным входом в начало concat, а не рисуется поверх
    первых секунд: иначе начало речи оказалось бы под картинкой, а хук должен
    звучать целиком.

    first_extra_input — номер входа ffmpeg, с которого начинаются дополнительные
    входы (обложка, врезки). Входы 0..n-1 — субклипы (по одному на каждый, см.
    subclip_inputs), вход n — музыка, если она есть. Порядок входов в команде обязан
    совпадать с порядком здесь.
    """
    n = len(subclips)
    if n == 0:
        raise ValueError("build_graph: нет ни одного субклипа")

    # кроп никогда не шире самого кадра: у горизонтального лонга 16:9 из 16:9-исходника
    # расчётная ширина совпадает с шириной кадра, и лишний пиксель округления уронил бы
    # ffmpeg целиком
    crop_w = even(src_height * width / height)
    if src_width:
        crop_w = min(crop_w, even(src_width))
    parts = []

    # Каждый субклип — отдельный вход ffmpeg, уже подрезанный на входе через
    # `-ss/-to` (см. subclip_inputs). Раньше окно подавалось один раз и раздавалось
    # split'ом на все субклипы: split обязан отдать кадр всем веткам сразу, а concat
    # берёт ветки по очереди, поэтому кадры поздних субклипов копились в очередях
    # несжатыми. На 1080p это влезало в память, на 4K (12 МБ на кадр) ffmpeg падал с
    # «Cannot allocate memory». Входы же читаются лениво, по требованию concat.
    for i, sub in enumerate(subclips):
        if sub.crop_x is None:
            x_expr = "(in_w-out_w)/2"
        else:
            # автотрекинг считает горизонталь под вертикальный кроп; для формата, где
            # кропать нечего, это значение выходит за кадр — прижимаем к границе
            x = int(sub.crop_x)
            if src_width:
                x = min(max(0, x), max(0, even(src_width) - crop_w))
            x_expr = str(x)
        # scale к геометрии основного исходника: внешняя врезка может быть 1080p при
        # 4K-основе, а concat требует одинаковый размер кадра у всех сегментов
        norm = f"scale={even(src_width)}:{src_height}," if src_width else ""
        # fps ДО zoompan обязателен: zoompan выдаёт d кадров на каждый входной кадр и
        # просто объявляет выход как fps, время не ресемплирует. Врезка 30 fps в
        # ролике 25 fps растягивалась на 20 % (8 с → 9.6 с), и после неё видео
        # отставало от звука и субтитров на 2–3 с (замерено на реальном лонге).
        # Точная длина ветки: -to на входе включает кадр, попавший ровно на границу, а
        # snap_to_frames как раз ставит границы на кадры; у 25-fps врезки fps=30 ещё и
        # округляет длительность вверх. Каждый субклип получал ±1 кадр, лонг
        # с 50 субклипами вырос на 19 кадров (0.63 с), и субтитры к концу обгоняли голос.
        n_frames = max(1, round(sub.duration * fps))
        n_samples = max(1, round(n_frames / fps * 48000))
        parts.append(
            f"[{i}:v:0]setpts=PTS-STARTPTS,fps={fps:g},trim=end_frame={n_frames},{norm}"
            f"crop={crop_w}:{src_height}:{x_expr}:0,"
            f"zoompan=z='{zoom_expr(sub, fps)}':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='{zoom_y_expr(sub, eye_target)}':"
            f"s={width}x{height}:fps={fps:g}[v{i}]"
        )
        # единый формат звука: у врезок из cta/ может быть 44.1 кГц или моно,
        # а concat склеивает только одинаковые дорожки
        gain = f"volume={sub.gain_db:g}dB," if abs(sub.gain_db) > 0.01 else ""
        parts.append(f"[{i}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"{gain}asetpts=PTS-STARTPTS,atrim=end_sample={n_samples}[a{i}]")

    idx_input = first_extra_input

    def _still(label, sec):
        """Неподвижная картинка своим входом: кадр + собственная тишина.

        Своя тишина нужна потому, что concat требует одинакового числа дорожек
        у всех сегментов — без неё звук ролика оборвётся на первом же стыке.
        """
        parts.append(
            f"[{idx_input}:v:0]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},setsar=1,fps={fps:g},"
            f"trim=duration={sec:.3f},setpts=PTS-STARTPTS[v{label}]"
        )
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=duration={sec:.3f},asetpts=PTS-STARTPTS[a{label}]"
        )

    if cover_path:
        # :v:0 обязателен: в файлах с камеры рядом с видео лежит встроенный mjpeg-кадр,
        # и без явного номера потока ffmpeg может взять именно его.
        _still("cover", cover_sec)
        idx_input += 1

    if tail_path:
        _still("tail", tail_sec)
        idx_input += 1

    pairs = ("[vcover][acover]" if cover_path else "") \
        + "".join(f"[v{i}][a{i}]" for i in range(n)) \
        + ("[vtail][atail]" if tail_path else "")
    total = n + bool(cover_path) + bool(tail_path)

    parts.append(f"{pairs}concat=n={total}:v=1:a=1[vcat][aout]")

    # Тайминги врезок заданы в таймлайне речи, а обложка занимает начало ролика —
    # без этого сдвига врезка сработала бы на cover_sec раньше нужного места.
    shift = cover_sec if cover_path else 0.0

    last = "vcat"
    for j, ov in enumerate(overlays or []):
        mode = ov.get("mode")
        # У вертикали говорящий по центру кадра и врезка в правом верху ему не мешает.
        # В 16:9 он сидит правее центра — там же оказывается и врезка, и восемь секунд
        # зритель слушает голос из-за картинки. Поэтому у горизонтали угол по умолчанию левый.
        # Проверено на лонгах «Дебатов»: в 16:9 крупный план говорящего занимает левую
        # или правую половину, и пин в 42% ширины в любом верхнем углу ложится на лицо.
        # Поэтому у горизонтали пин меньше (26%) и стоит в нижнем левом углу — между
        # лицом (верхняя половина) и субтитрами (MarginV 90, нижние ~15%).
        landscape = width > height
        if ov.get("corner"):
            corner = ov["corner"]
        elif landscape:
            corner = "bottom_left" if mode == "corner" else "top_left"
        else:
            corner = "top_right"
        video_prefix = (
            f"trim=duration={ov['end_sec'] - ov['start_sec']:.3f},"
            f"setpts=PTS-STARTPTS+{ov['start_sec'] + shift:.3f}/TB,"
            if ov.get("is_video") else ""
        )
        if mode == "plate":
            # Плашка — не кадр, а подпись: её высота заранее не известна графу, поэтому
            # позиция считается выражениями overlay (H/W — кадр, h/w — сама плашка),
            # а не подставленными числами, как у прямоугольной врезки.
            parts.append(f"[{idx_input}:v:0]{video_prefix}scale={even(width * 0.40)}:-2,"
                         f"setsar=1,format=rgba[ovs{j}]")
            m = int(width * 0.035)
            x = str(m) if corner.endswith("left") else f"W-w-{m}"
            y = str(int(height * 0.07)) if corner.startswith("top") else f"H-h-{int(height * 0.08)}"
            pos = f"x={x}:y={y}"
        elif mode == "corner":
            pin_w = 0.26 if landscape else 0.42
            parts.append(f"[{idx_input}:v:0]{video_prefix}scale={even(width * pin_w)}:-2,setsar=1[ovs{j}]")
            x = int(width * 0.04) if corner.endswith("left") else int(width * (0.96 - pin_w))
            y_bottom = 0.56 if landscape else 0.55
            y = int(height * 0.13) if corner.startswith("top") else int(height * y_bottom)
            pos = f"x={x}:y={y}"
        else:
            parts.append(
                f"[{idx_input}:v:0]{video_prefix}scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},setsar=1[ovs{j}]"
            )
            pos = "x=0:y=0"
        parts.append(
            f"[{last}][ovs{j}]overlay={pos}:"
            f"enable='between(t,{ov['start_sec'] + shift:.3f},{ov['end_sec'] + shift:.3f})'"
            f"[ovo{j}]"
        )
        last = f"ovo{j}"
        idx_input += 1

    parts.append(f"[{last}]null[vout]")
    return ";".join(parts)
