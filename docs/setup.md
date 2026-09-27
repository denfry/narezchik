# Установка

## Быстро

- **Windows:** двойной клик по `install.cmd` (или `install.cmd --auto` в консоли).
- **macOS / Linux:** `./install.sh` (или `./install.sh --auto`).

Установщик покажет таблицу — что уже стоит, чего нет — и спросит:

```
1) Установить всё рекомендуемое автоматически
2) Установить вообще всё (+ публикация на YouTube, сервер, тесты)
3) Выбрать компоненты вручную
4) Только проверить и выйти
```

Флаги для консоли: `--check` (только проверить), `--auto` (рекомендуемое без вопросов),
`--all`, `--only whisper,face`, `--yes`, `--venv` (ставить в отдельное окружение `.venv`),
`--test` (прогнать тесты после установки).

## Компоненты

| Ключ | Что это | Нужно? |
|---|---|---|
| ffmpeg | резка, склейка, звук, субтитры — всё видео | обязательно, ставится winget/brew/apt |
| `core` | jsonschema, Pillow, numpy, requests | обязательно |
| `whisper` | faster-whisper — транскрипция с таймкодами слов | рекомендуется |
| `face` | mediapipe + модель лиц 3.6 МБ — автокроп 9:16, обложки, QC | рекомендуется |
| `gpu` | cuBLAS/cuDNN для NVIDIA (~1.3 ГБ) — транскрипция в 10 раз быстрее | если есть NVIDIA |
| `channel` | yt-dlp — субтитры лучших роликов канала для `/setup-channel` | рекомендуется |
| `publish` | Google API client — загрузка роликов по расписанию | по желанию |
| `remote` | paramiko — рендер на своём сервере по SSH | по желанию |
| `dev` | pytest | по желанию |

Плюс ассеты: 7 LUT для цветокора (`assets/luts`, считаются математикой — без чужих
лицензий), синтезированные SFX и подложки, шаблоны `channel/brand.json` и `settings.json`.

## Требования

- Python 3.10–3.12 (3.13 — mediapipe может не иметь сборки).
- ffmpeg с libx264 и libass. На Windows — `Gyan.FFmpeg` (full build), ставится установщиком.
- Claude Code: https://claude.com/claude-code. Хватает подписки Claude Pro — см. docs/budget.md.
- Диск: исходники подкастов в 4K весят 10–40 ГБ; рендер 4K требует 6–9 ГБ RAM.

## GPU (NVIDIA) и транскрипция

`faster-whisper` работает через ctranslate2, у которого свой CUDA-рантайм: **CUDA Toolkit
и CUDA-сборка torch не нужны**. Нужны только DLL cuBLAS/cuDNN — их кладёт компонент
`gpu` (pip-пакеты `nvidia-*-cu12`), а `transcribe.py` сам подключает их к процессу.

Проверка: `python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"`.

- Печатает 0 — транскрипция на CPU. Это рабоче, но медленно: брать `--model small`.
- Ошибка `cublas64_12.dll is not found` на этапе распознавания — не установлен компонент `gpu`.
- `CUDA driver version is insufficient` — видеокарта не NVIDIA или старый драйвер;
  установщиком не лечится, работайте на CPU.

Ориентиры скорости: RTX-класса GPU, модель small — в разы быстрее реального времени;
CPU, модель small — около реального времени; medium на CPU — в 3 раза медленнее small.

## Если faster-whisper не ставится

Транскрипт можно получить любым способом и положить в `transcripts/<clip_id>.json`
в формате `{"segments": [{"start", "end", "text", "words": [{"start", "end", "word"}]}]}`
(и `.txt` построчно `[start-end] текст`). Остальной пайплайн от whisper не зависит.

## Щадящий режим (игры, работа за ПК, HDD)

- `SHORTS_LOW_PRIORITY=idle` — ffmpeg/whisper с приоритетом Idle: CPU и диск только в
  простое, машина не лагает, рендер идёт в 3–4 раза дольше.
- `SHORTS_THREADS=4` — ограничить потоки x264 и фильтров.
- Дисковые задачи (закачка, извлечение звука, рендер) не запускать параллельно — на HDD
  они делят диск, и всё ползёт. Очередь «по одному» — `scripts/pipeline/run_queue.py`.

## Проверка

```bash
python install.py --check
cd scripts/pipeline && python -m pytest tests/ -q     # ~170 тестов, ~1 минута
```

## Частые проблемы

| Симптом | Причина | Что делать |
|---|---|---|
| `ffmpeg` не найден после установки | PATH обновился только в новых окнах | перезапустить консоль и Claude Code |
| `fontselect: failed to find font` | шрифт из brand.json не лежит в `assets/fonts/` | положить .ttf, `font_name` = имя семейства |
| mediapipe не видит лиц на общем плане | детектор короткодистанционный | уже решено: `autoframe` режет кадр на плитки |
| Кириллица кракозябрами в консоли Windows | cp1251 | скрипты сами переключают вывод в UTF-8; в cmd — `chcp 65001` |
| `Cannot allocate memory` при 4K-рендере | мало RAM/файла подкачки | docs/render.md, раздел «Память» |
