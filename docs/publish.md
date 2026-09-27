# Публикация

## Лист публикации (без API)

`/pack <префикс> <дата>` или вручную:

```bash
python scripts/publish/publish_sheet.py podcast12 --start 2026-10-01 \
    --shorts-time 12:00 --long-weekday 5 --long-time 18:00 --utc-offset 3
```

→ `edit-plans/podcast12_ПУБЛИКАЦИЯ.md`: календарь, QC-сводка и по каждому ролику заголовок,
описание, теги, хэштеги, промпт обложки. Грузите руками в Studio. У шортса в Studio:
выбрать кадр превью и поставить «Связанное видео» на указанный лонг.

## Автозагрузка по расписанию (YouTube Data API, OAuth)

Один раз:

1. https://console.cloud.google.com/ → ваш проект (тот же, что для API-ключа) →
   включить **YouTube Data API v3**.
2. **OAuth consent screen** → External → заполнить имя → в **Test users** добавить
   Google-аккаунт, которому принадлежит канал.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → скачать JSON
   в `secrets/client_secret.json`.
4. Компонент `publish` установщика, затем
   `python scripts/publish/youtube_upload.py --auth` — откроется браузер, войдите в
   аккаунт канала. Токен сохранится в `secrets/token.json`.

Загрузка:

```bash
python scripts/publish/youtube_upload.py --schedule edit-plans/podcast12_ЗАГРУЗКА.json --dry-run
python scripts/publish/youtube_upload.py --schedule edit-plans/podcast12_ЗАГРУЗКА.json [--limit 3]
```

Ролики грузятся **приватными с отложенной публикацией** (`publishAt`) — YouTube сам
откроет их в срок. Загруженные помечаются `video_id` и при повторном запуске пропускаются.

Квота по умолчанию — 10 000 единиц в сутки, загрузка стоит ~1600: около 6 роликов в день.
Превышение — «quotaExceeded», продолжить завтра тем же запуском.

**Важно про аудит.** По правилам YouTube API ролики, загруженные через `videos.insert` из
непроверенного API-проекта, остаются приватными и сами не откроются. Снять ограничение
можно, пройдя аудит проекта (форма YouTube API Services Audit). Пока аудита нет —
грузите через API как черновики и открывайте вручную в Studio, или грузите руками по
листу публикации. Перед первой пачкой проверьте на одном ролике.

## Правка описаний через API

`videos().update(part="snippet")` заменяет snippet **целиком**: передавайте title,
description, categoryId и tags вместе, иначе пропущенное поле обнулится. И берите полное
описание из `videos().list`, а не из инструментов, которые его обрезают.
