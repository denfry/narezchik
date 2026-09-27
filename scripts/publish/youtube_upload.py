"""Загрузка роликов на YouTube с отложенной публикацией через YouTube Data API v3.

Нужно один раз:
  1. secrets/client_secret.json — OAuth client (Desktop app) из Google Cloud Console,
     проект с включённым YouTube Data API v3, аккаунт канала добавлен в Test users.
  2. python scripts/publish/youtube_upload.py --auth   -> откроется браузер, логин под аккаунтом
     канала, токен сохранится в secrets/token.json.

Дальше:
  python scripts/publish/youtube_upload.py --schedule edit-plans/<x>_ЗАГРУЗКА.json [--dry-run]

Схему загрузки собирает scripts/publish/publish_sheet.py. Настройка OAuth — docs/publish.md.

Формат schedule JSON: {"items": [{"file": "output/x.mp4", "title": "...",
"description": "...", "tags": ["..."], "publish_at": "2026-09-04T12:00:00+03:00",
"thumbnail": "temp/x_cover.png" (опц.), "playlist_id": "..." (опц.)}]}.
Ролик грузится как private с publishAt — YouTube сам переключит его в public в срок
(только если API-проект прошёл аудит YouTube; иначе ролик останется приватным — docs/publish.md).
После успешной загрузки в item дописываются video_id и uploaded_at, повторный запуск
такие пропускает. Квота: 1600 единиц на загрузку, 10 000 в сутки по умолчанию.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

# консоль Windows в cp1251 падает на кириллице/спецсимволах в выводе
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECRETS = os.path.join(ROOT, "secrets")
CLIENT_SECRET = os.path.join(SECRETS, "client_secret.json")
TOKEN = os.path.join(SECRETS, "token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl",  # комментарии: модерация и ответы
          "https://www.googleapis.com/auth/youtube"]
CATEGORY_PEOPLE_BLOGS = "22"


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}][yt] {msg}", flush=True)


def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not os.path.exists(TOKEN):
        sys.exit("нет secrets/token.json — сначала python scripts/publish/youtube_upload.py --auth")
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(TOKEN, "w", encoding="utf-8").write(creds.to_json())
    return creds


def auth():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT_SECRET):
        sys.exit(f"нет {CLIENT_SECRET} — скачай OAuth client (Desktop app) из Google Cloud Console")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    os.makedirs(SECRETS, exist_ok=True)
    open(TOKEN, "w", encoding="utf-8").write(creds.to_json())
    log(f"токен сохранён в {TOKEN}")


def _service():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_creds(), cache_discovery=False)


def _rfc3339_utc(s):
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError(f"publish_at без таймзоны: {s}")
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upload_one(yt, item):
    from googleapiclient.http import MediaFileUpload
    path = os.path.join(ROOT, item["file"]) if not os.path.isabs(item["file"]) else item["file"]
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    body = {
        "snippet": {
            "title": item["title"][:100],
            "description": item.get("description", "")[:5000],
            "tags": item.get("tags", [])[:60],
            "categoryId": item.get("category_id", CATEGORY_PEOPLE_BLOGS),
            "defaultLanguage": "ru",
            "defaultAudioLanguage": "ru",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": _rfc3339_utc(item["publish_at"]),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, backoff = None, 2
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                log(f"  {os.path.basename(path)}: {int(status.progress() * 100)}%")
        except Exception as e:  # сетевые/5xx — повторяем с паузой
            if backoff > 64:
                raise
            log(f"  ошибка чанка ({e}), повтор через {backoff}с")
            time.sleep(backoff)
            backoff *= 2
    vid = resp["id"]
    thumb = item.get("thumbnail")
    if thumb:
        tp = os.path.join(ROOT, thumb) if not os.path.isabs(thumb) else thumb
        if os.path.exists(tp):
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(tp)).execute()
        else:
            log(f"  обложка не найдена, пропускаю: {tp}")
    if item.get("playlist_id"):
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": item["playlist_id"],
            "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    return vid


def run_schedule(schedule_path, dry_run=False, limit=None):
    sched = json.load(open(schedule_path, encoding="utf-8"))
    items = sched["items"]
    pending = [i for i in items if not i.get("video_id")]
    log(f"в плане {len(items)}, к загрузке {len(pending)}" + (f", лимит {limit}" if limit else ""))
    for it in pending:
        log(f"{it['file']} -> '{it['title']}' @ {it['publish_at']}")
    if dry_run:
        return
    yt = _service()
    done = 0
    for it in pending:
        if limit and done >= limit:
            break
        try:
            vid = upload_one(yt, it)
        except Exception as e:
            msg = str(e)
            log(f"ОШИБКА на {it['file']}: {msg[:300]}")
            if "quotaExceeded" in msg or "uploadLimitExceeded" in msg:
                log("квота исчерпана — продолжить завтра тем же запуском")
                break
            continue
        it["video_id"] = vid
        it["uploaded_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        json.dump(sched, open(schedule_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        log(f"OK {it['file']} -> https://youtu.be/{vid}")
        done += 1
    left = sum(1 for i in items if not i.get("video_id"))
    log(f"загружено за прогон: {done}, осталось: {left}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auth", action="store_true", help="пройти OAuth и сохранить токен")
    ap.add_argument("--schedule", help="JSON с планом загрузки")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="сколько роликов загрузить за прогон")
    a = ap.parse_args()
    if a.auth:
        auth()
    elif a.schedule:
        run_schedule(a.schedule, a.dry_run, a.limit)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
