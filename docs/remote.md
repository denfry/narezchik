# Свой сервер для транскрипции и рендера (необязательно)

Если домашний ПК слабый или занят, тяжёлые стадии можно гонять на VPS/VDS по SSH.
Нужен Linux-сервер с Docker; GPU не требуется (whisper на CPU int8).

## Настройка

1. SSH-доступ по ключу и алиас в `~/.ssh/config`:
   ```
   Host my-render
     HostName 203.0.113.10
     User root
     IdentityFile ~/.ssh/id_ed25519
   ```
2. Переменные окружения (или `remote_host` в `channel/settings.json` — агент подставит):
   ```
   NAREZCHIK_SSH_HOST=my-render
   NAREZCHIK_REMOTE_ROOT=/srv/mk      # по умолчанию
   ```
3. Компонент `remote` установщика (paramiko), затем один раз:
   `python scripts/remote/remote.py setup` — соберёт образ `mk-worker` (python + ffmpeg +
   faster-whisper) и зальёт код пайплайна.

## Команды

```bash
python scripts/remote/remote.py transcribe input/X.mp4 --clip-id X [--model small] [--detach]
python scripts/remote/remote.py render edit-plans/X_short01.json [--detach] [--mem 8g] [--cpus 5]
python scripts/remote/remote.py run -- python scripts/pipeline/probe_video.py input/X.mp4
python scripts/remote/remote.py status | logs <job> [-f] | pull <job>
```

- Для транскрипции на сервер уезжает не видео, а локально извлечённый 16 кГц mono wav
  (~115 МБ на час).
- Для рендера уезжает исходник (один раз, дальше докачка по размеру) и все файлы, на
  которые ссылается план: ассеты, LUT, плашки, врезки со своим звуком.
- Код сверяется по md5 и перезаливается целиком (докачка хвоста по размеру однажды
  дописала изменённый .py к старому и дала SyntaxError).
- Результат скачивается в те же папки проекта.

## Лимиты контейнера

По умолчанию 3 CPU / 4 GiB, `nice 19`, `ionice idle`, одно задание за раз (flock) —
чтобы сайты и базы на том же сервере не страдали. 4K-лонг в 4 GiB не влезает:
`--mem 9g --pids 2048` и `SHORTS_THREADS=3`:

```bash
python scripts/remote/remote.py run --pull output/X_long01.mp4 --mem 9g --pids 2048 --detach -- \
  env SHORTS_THREADS=3 python scripts/pipeline/render_short.py edit-plans/X_long01.json
```

Прогресс ffmpeg в лог задания не пишется до конца — смотреть `docker stats` и размер
`/srv/mk/temp/<id>_render.mp4` на сервере.

## Исходник прямо на сервер

Большой файл из публичной папки Яндекс.Диска можно скачать сразу на сервер, минуя домашний
диск: `scripts/remote/dl_yadisk.py` (команда в докстринге, ключ — `YADISK_PUBLIC_KEY`).
`remote.py` видит, что локально файла нет, а на сервере есть, и берёт серверную копию.

## Если образ пропал

`pull access denied for mk-worker` (rc=125) — образ удалила чистка Docker на сервере.
`python scripts/remote/remote.py setup` пересоберёт его за пару минут.
