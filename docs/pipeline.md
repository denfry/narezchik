# Пайплайн: стадии, план монтажа, скрипты

Обычно всё это запускают агенты. Этот документ — для ручной отладки, перерендера после
правки плана руками и для тех, кто хочет понять, что под капотом.

## План монтажа — `edit-plans/<clip_id>.json`

Единственный канал передачи состояния между стадиями. Схема —
`schemas/edit-plan.schema.json`, пример — `examples/edit-plan.short.example.json`.
Один исходник даёт несколько планов: `<id>.json` (базовый, только `source`),
`<id>_short01.json`, `<id>_short02.json`, `<id>_long01.json`.

| Секция | Кто пишет | Что внутри |
|---|---|---|
| `project` | все | `clip_id`, `stage`, `notes`, `youtube_metadata`, `related_long` |
| `source` | render-engineer | путь (или URL), длительность, fps, разрешение, дорожки, LUFS, путь к транскрипту |
| `clip` | idea-scout | диапазон исходника, хук, суть, payoff, скоры, рабочий заголовок |
| `speech_edits` | scriptwriter | EDL: куски речи по порядку ролика — таймкоды исходника, роль, дословный текст |
| `visual_events` | shorts-editor / long-editor | кроп/наезды/врезки/текст — время готового ролика, обязательный `purpose` |
| `subtitle_events` | shorts-editor / long-editor | слова с таймингами и акцентами, плашки |
| `audio_events` | shorts-editor / long-editor | подложка, дакинг, бипы, SFX |
| `assets` | shorts-editor / long-editor | файлы врезок и музыки с лицензией |
| `render_settings` | scout/продюсер, shorts-editor / long-editor | формат, кодек, LUT, плашки |
| `qc` | render-engineer, qc-critic | технические замеры и редакторские оценки |

### Стадии (`project.stage`)

`ingested` (базовый план) → `moment_selected` → `script_edited` → `retention_passed` → `visual_planned` →
`broll_planned` → `cta_planned` → `subtitles_planned` → `audio_planned` → `rendered` →
`final`. Правка = откат `stage` до нужной точки и прогон дальше (таблица — в
`.claude/skills/fix/SKILL.md`).

### Две шкалы времени

- `speech_edits[].source_start_sec/end_sec` — **время исходника**.
- `visual_events`, `subtitle_events`, `audio_events` — **время готового ролика**: 0 — начало
  ролика, сумма длительностей предыдущих сегментов.

## Скрипты по порядку

```bash
# Приём: техпараметры исходника
python scripts/pipeline/probe_video.py input/podcast.mp4 --json-out temp/podcast_probe.json

# Транскрипция с таймкодами слов → transcripts/<id>.json + .txt
python scripts/pipeline/transcribe.py input/podcast.mp4 --clip-id podcast
python scripts/pipeline/transcribe.py input/podcast.mp4 --clip-id podcast --model small --device cpu

# Чтение транскрипта кусками (для агентов)
python scripts/pipeline/transcript_window.py podcast --outline 60
python scripts/pipeline/transcript_window.py podcast --from 1520 --to 1580 --words
python scripts/pipeline/transcript_window.py edit-plans/podcast_short03.json

# Вычитка ошибок распознавания без потери таймингов
python scripts/pipeline/proofread_transcript.py dump transcripts/podcast.json
python scripts/pipeline/proofread_transcript.py apply transcripts/podcast.json temp/podcast_fixes.json

# Черновой EDL: нарезка clip-диапазона по паузам и междометиям
python scripts/pipeline/auto_cut.py edit-plans/podcast_short03.json [--max-pause 0.6] [--dry-run]

# Кроп 9:16 по активному говорящему + линия глаз → reframe-события в план
python scripts/pipeline/autoframe.py --plan edit-plans/podcast_short03.json

# Субтитры из speech_edits и пословного транскрипта (word — вертикаль, chunk — горизонталь)
python scripts/pipeline/build_subtitles.py edit-plans/podcast_short03.json [--mode word]

# Проверка плана — перед каждым рендером
python scripts/pipeline/validate_edit_plan.py edit-plans/podcast_short03.json

# Рендер → output/<clip_id>.mp4
python scripts/pipeline/render_short.py edit-plans/podcast_short03.json [--use-gpu] [--keep-work]

# Технический QC → temp/<id>_qc.json (exit 1 при проблемах)
python scripts/pipeline/qc_video.py output/podcast_short03.mp4 --plan edit-plans/podcast_short03.json --json-out temp/podcast_short03_qc.json
python scripts/pipeline/check_people.py output/podcast_short03.mp4 --plan edit-plans/podcast_short03.json

# Лист кадров / лёгкое превью для ревью
python scripts/pipeline/extract_preview.py output/podcast_short03.mp4 --mode grid --tiles 16
python scripts/pipeline/extract_preview.py output/podcast_short03.mp4 --mode clip
```

### Графика и звук

```bash
python scripts/pipeline/make_cover.py edit-plans/x_short01.json      # вертикальная обложка из кадра
python scripts/pipeline/make_endplate.py temp/x_plate.png            # плашка CTA (brand.json)
python scripts/pipeline/make_cards.py round temp/r1.png --n 1 --title "ТЕМА"
python scripts/pipeline/make_cards.py score temp/s.png --left 2 --right 1
python scripts/pipeline/make_cards.py proof temp/p.png --title "5%" --sub "подпись" --source "источник"
python scripts/pipeline/make_cards.py question|final|tail|plate|rank|rankplate|list ...
python scripts/pipeline/make_luts.py                                  # пересоздать LUT
python scripts/pipeline/synth_assets.py                               # синтез подложек и SFX
python scripts/pipeline/gen_music.py --mood dark_debate --clip-id x   # ACE-Step, docs/music.md
```

### Источники и очередь

```bash
# Докачка большого файла из публичной папки Яндекс.Диска (по частям, с возобновлением)
python scripts/pipeline/yadisk_download.py "/Папка/Выпуск 12.mp4" "input/Выпуск 12.mp4" \
    --public-key https://disk.yandex.ru/d/XXXX

# Очередь «по одному в щадящем режиме» по channel/queue.json (образец — queue.example.json)
python scripts/pipeline/run_queue.py prepare   # закачки + транскрипция + заготовки планов
python scripts/pipeline/run_queue.py render    # рендер всех планов со stage audio_planned
python scripts/pipeline/run_queue.py status
```

## Вспомогательные модули (не CLI)

`common.py` (пути, запуск ffmpeg, JSON, приоритет процессов), `brand.py` (бренд из
`channel/brand.json`), `workfile.py` (рабочие окна), `filtergraph.py` (единый граф),
`audiochain.py` (звуковые фильтры). Чистые функции, покрыты тестами:
`cd scripts/pipeline && python -m pytest tests/ -q`.

## Принципы

- Исходники в `input/` только читаются; временное — только в `temp/`.
- Скрипты не принимают редакторских решений: они исполняют план.
- Повторный рендер без повторного анализа: план можно править руками (например,
  `crop_x` одного сегмента) и просто перезапустить рендер.
- `render_short.py` не рендерит пустой план — явная ошибка вместо пустого файла.
