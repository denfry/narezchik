#!/usr/bin/env python
"""Комментарии канала: выгрузка для ответов, автомодерация по правилам, публикация ответов.

    python scripts/community/comments.py fetch [--days 7] [--top 40]
        Ветки без ответа канала, самые залайканные сверху → community/inbox_<дата>.json и .md.
        Хватает API-ключа (как для /setup-channel). По этому файлу агент community-manager
        пишет черновики ответов в community/replies_<дата>.json.

    python scripts/community/comments.py moderate [--apply] [--recent 100]
        Комментарии, придержанные YouTube на проверку, и последние опубликованные: скрыть
        то, что попадает под правила channel/comment-rules.json, остальное опубликовать.
        Без --apply — только показать решения. Нужен OAuth (docs/comments.md).

    python scripts/community/comments.py post community/replies_<дата>.json [--apply]
        Опубликовать ответы, у которых "approved": true. Без --apply — только показать.
        Нужен OAuth. Уже отправленные помечаются и повторно не уходят.

    python scripts/community/comments.py test
        Сверить правила с ручными решениями в журнале (записи с "by": "manual").

Журнал решений — community/moderation_log.jsonl. Квота: list — 1 единица,
setModerationStatus и comments.insert — по 50; суточный лимит 10 000.
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
COMMUNITY = os.path.join(ROOT, "community")
LOG = os.path.join(COMMUNITY, "moderation_log.jsonl")
RULES_PATH = os.path.join(ROOT, "channel", "comment-rules.json")
RULES_EXAMPLE = os.path.join(ROOT, "channel", "comment-rules.example.json")
SECRETS = os.path.join(ROOT, "secrets")
TOKEN = os.path.join(SECRETS, "token.json")
API = "https://www.googleapis.com/youtube/v3"
LINK_RE = re.compile(r"https?://\S+|\b[\w-]+\.(?:ru|com|net|org|io|me|cc|xyz)/\S*", re.I)


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}][comments] {msg}", flush=True)


# ----------------------------------------------------------------------------- правила

def load_rules(path=None):
    path = path or (RULES_PATH if os.path.isfile(RULES_PATH) else RULES_EXAMPLE)
    with io.open(path, encoding="utf-8") as f:
        raw = json.load(f)
    norm = lambda p: p.replace("[её]", "е").replace("ё", "е")  # noqa: E731
    return {
        "reject": {cat: [re.compile(norm(p), re.I) for p in pats]
                   for cat, pats in raw.get("reject", {}).items()},
        "allow": [re.compile(norm(p), re.I) for p in raw.get("allow", [])],
        "own_links": [s.lower() for s in raw.get("own_links", [])],
        "reject_foreign_links": raw.get("reject_foreign_links", True),
    }


def classify(text, rules):
    """(решение, причина): ('rejected', 'insult: …') или ('published', '')."""
    t = text.replace("ё", "е").replace("Ё", "Е")
    for a in rules["allow"]:
        t = a.sub(" ", t)
    for cat, pats in rules["reject"].items():
        for p in pats:
            m = p.search(t)
            if m:
                return "rejected", f"{cat}: {m.group(0)}"
    if rules["reject_foreign_links"]:
        for m in LINK_RE.finditer(text):
            link = m.group(0).lower()
            if not any(o in link for o in rules["own_links"]):
                return "rejected", f"link: {m.group(0)[:40]}"
    return "published", ""


# ----------------------------------------------------------------------------- доступ

def api_key():
    if os.environ.get("YOUTUBE_API_KEY"):
        return os.environ["YOUTUBE_API_KEY"]
    p = os.path.join(SECRETS, "youtube_api_key.txt")
    return open(p, encoding="utf-8").read().strip() if os.path.isfile(p) else None


def oauth_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not os.path.isfile(TOKEN):
        sys.exit("нет secrets/token.json — сначала python scripts/publish/youtube_upload.py --auth "
                 "(docs/comments.md)")
    creds = Credentials.from_authorized_user_file(TOKEN)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(TOKEN, "w", encoding="utf-8").write(creds.to_json())
    if creds.scopes and not any("force-ssl" in s for s in creds.scopes):
        sys.exit("токен выдан без права на комментарии — пройдите заново: "
                 "python scripts/publish/youtube_upload.py --auth")
    # www.googleapis.com отвечает и тогда, когда youtube.googleapis.com висит по таймауту
    return build("youtube", "v3", credentials=creds, cache_discovery=False,
                 client_options={"api_endpoint": "https://www.googleapis.com"})


def channel_id_from_analysis():
    p = os.path.join(ROOT, "channel", "analysis", "channel.json")
    if os.path.isfile(p):
        with io.open(p, encoding="utf-8") as f:
            return json.load(f).get("id")
    return None


def thread_row(t, channel):
    top = t["snippet"]["topLevelComment"]
    s = top["snippet"]
    replies = (t.get("replies") or {}).get("comments", [])
    return {
        "id": top["id"], "video": s.get("videoId"), "author": s.get("authorDisplayName", ""),
        "text": s.get("textDisplay", ""), "likes": int(s.get("likeCount", 0)),
        "replies": int(t["snippet"].get("totalReplyCount", 0)),
        "published_at": s.get("publishedAt"),
        "channel_author": s.get("authorChannelId", {}).get("value") == channel,
        "answered": any(r["snippet"].get("authorChannelId", {}).get("value") == channel
                        for r in replies),
    }


def read_auth():
    """({параметры}, {заголовки}) для чтения: API-ключ, а без него — OAuth-токен."""
    key = api_key()
    if key:
        return {"key": key}, {}
    if os.path.isfile(TOKEN):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(TOKEN)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            open(TOKEN, "w", encoding="utf-8").write(creds.to_json())
        return {}, {"Authorization": f"Bearer {creds.token}"}
    sys.exit("нет доступа к API: YOUTUBE_API_KEY, secrets/youtube_api_key.txt или OAuth-токен "
             "secrets/token.json (docs/setup-channel.md)")


def fetch_public(channel, auth, limit):
    """Опубликованные ветки канала, новые сверху."""
    import requests
    out, tok = [], None
    while len(out) < limit:
        params = {"part": "snippet,replies", "allThreadsRelatedToChannelId": channel,
                  "maxResults": 100, "textFormat": "plainText", "order": "time", **auth[0]}
        if tok:
            params["pageToken"] = tok
        r = requests.get(f"{API}/commentThreads", params=params, headers=auth[1], timeout=30)
        if r.status_code != 200:
            sys.exit(f"YouTube API: {r.status_code} {r.text[:300]}")
        data = r.json()
        out += [thread_row(t, channel) for t in data.get("items", [])]
        tok = data.get("nextPageToken")
        if not tok:
            break
    return out


def fetch_oauth(yt, channel, status, limit):
    out, tok = [], None
    while len(out) < limit:
        kw = dict(part="snippet,replies", allThreadsRelatedToChannelId=channel, maxResults=100,
                  textFormat="plainText", pageToken=tok)
        if status:
            kw["moderationStatus"] = status
        r = yt.commentThreads().list(**kw).execute()
        out += [thread_row(t, channel) for t in r.get("items", [])]
        tok = r.get("nextPageToken")
        if not tok:
            break
    return out


def video_titles(ids, auth):
    import requests
    titles = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    for i in range(0, len(ids), 50):
        r = requests.get(f"{API}/videos", params={"part": "snippet", "id": ",".join(ids[i:i + 50]),
                                                  **auth[0]}, headers=auth[1], timeout=30)
        for v in r.json().get("items", []):
            titles[v["id"]] = v["snippet"]["title"]
    return titles


def logged_ids():
    done = set()
    if os.path.isfile(LOG):
        for line in io.open(LOG, encoding="utf-8"):
            try:
                done.add(json.loads(line).get("id"))
            except ValueError:
                continue
    return done


def append_log(rows):
    os.makedirs(COMMUNITY, exist_ok=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------- команды

def select_inbox(rows, rules, days, top, now):
    """Что стоит ответа: без ответа канала, не от канала, не под правилами скрытия, свежее
    days дней; сортировка — по лайкам и числу ответов в ветке (ответ там видят чаще)."""
    cutoff = now - dt.timedelta(days=days)
    picked = []
    for r in rows:
        if r["channel_author"] or r["answered"]:
            continue
        when = dt.datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
        if when < cutoff:
            continue
        if classify(r["text"], rules)[0] == "rejected":
            continue
        picked.append(r)
    picked.sort(key=lambda r: (r["likes"] + 2 * r["replies"], r["published_at"]), reverse=True)
    return picked[:top]


def cmd_fetch(a):
    auth = read_auth()
    channel = a.channel or channel_id_from_analysis()
    if not channel:
        sys.exit("не знаю ID канала: сначала /setup-channel или передайте --channel UC…")
    rules = load_rules()
    rows = fetch_public(channel, auth, a.scan)
    now = dt.datetime.now(dt.timezone.utc)
    inbox = select_inbox(rows, rules, a.days, a.top, now)
    titles = video_titles([r["video"] for r in inbox], auth)
    for r in inbox:
        r["video_title"] = titles.get(r["video"], "")
    os.makedirs(COMMUNITY, exist_ok=True)
    stamp = f"{now:%Y-%m-%d}"
    jpath = os.path.join(COMMUNITY, f"inbox_{stamp}.json")
    with io.open(jpath, "w", encoding="utf-8") as f:
        json.dump({"channel": channel, "collected_at": now.isoformat(timespec="seconds"),
                   "comments": inbox}, f, ensure_ascii=False, indent=1)
    lines = [f"# Комментарии без ответа — {stamp}", ""]
    by_video = {}
    for r in inbox:
        by_video.setdefault(r["video"], []).append(r)
    for vid, items in by_video.items():
        lines += [f"## {items[0]['video_title']} — youtu.be/{vid}", ""]
        for r in items:
            text = " ".join(r["text"].split())[:400]
            lines += [f"- `{r['id']}` **{r['author']}** ({r['likes']} лайков, {r['replies']} ответов): {text}"]
        lines.append("")
    with io.open(jpath.replace(".json", ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"просмотрено {len(rows)}, к ответу {len(inbox)} → {os.path.relpath(jpath, ROOT)} (+ .md)")


def cmd_moderate(a):
    rules = load_rules()
    yt = oauth_service()
    channel = yt.channels().list(part="id", mine=True).execute()["items"][0]["id"]
    # Уже решённые не трогаем: статус после setModerationStatus доезжает до list не сразу,
    # и без этого каждый запуск заново тратил бы квоту на те же комментарии.
    done = logged_ids()
    held = [c for c in fetch_oauth(yt, channel, "heldForReview", 500) if c["id"] not in done]
    visible = [c for c in fetch_oauth(yt, channel, None, a.recent)
               if not c["channel_author"] and c["id"] not in done]
    decisions = []
    for c in held:
        d, why = classify(c["text"], rules)
        decisions.append((c, d, why, "held"))
    for c in visible:
        d, why = classify(c["text"], rules)
        if d == "rejected":
            decisions.append((c, d, why, "visible"))
    for c, d, why, src in decisions:
        print(f"{d:9} {src:7} {c['video']} {c['author']}: {c['text'][:90]!r} {why}")
    if not a.apply or not decisions:
        print(f"итого: {len(decisions)} решений" + ("" if a.apply else " (показ; применить — --apply)"))
        return
    for status in ("published", "rejected"):
        ids = [c["id"] for c, d, _, _ in decisions if d == status]
        for i in range(0, len(ids), 50):
            yt.comments().setModerationStatus(id=",".join(ids[i:i + 50]),
                                              moderationStatus=status).execute()
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    append_log([{"ts": stamp, "by": "rules", "decision": d, "reason": why, "source": src,
                 "id": c["id"], "video": c["video"], "author": c["author"], "text": c["text"]}
                for c, d, why, src in decisions])
    pub = sum(d == "published" for _, d, _, _ in decisions)
    print(f"итого: опубликовано {pub}, скрыто {len(decisions) - pub}")


def cmd_post(a):
    path = a.replies if os.path.isabs(a.replies) else os.path.join(ROOT, a.replies)
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    todo = [r for r in data["replies"] if r.get("approved") and not r.get("posted_id")
            and r.get("reply", "").strip()]
    skipped = sum(1 for r in data["replies"] if not r.get("approved"))
    for r in todo:
        print(f"→ {r.get('author', '')} [{r['comment_id']}]: {r['reply'][:120]!r}")
    print(f"к отправке {len(todo)}, не одобрено {skipped}"
          + ("" if a.apply else " (показ; отправить — --apply)"))
    if not a.apply or not todo:
        return
    yt = oauth_service()
    for r in todo:
        try:
            res = yt.comments().insert(part="snippet", body={"snippet": {
                "parentId": r["comment_id"], "textOriginal": r["reply"].strip()}}).execute()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            log(f"ошибка на {r['comment_id']}: {msg[:200]}")
            if "quotaExceeded" in msg:
                log("квота исчерпана — продолжить завтра тем же запуском")
                break
            continue
        r["posted_id"] = res["id"]
        r["posted_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    log(f"отправлено {sum(1 for r in todo if r.get('posted_id'))} из {len(todo)}")


def cmd_test(_a):
    rules = load_rules()
    if not os.path.isfile(LOG):
        sys.exit("журнала нет — сверять не с чем")
    rows = [json.loads(l) for l in io.open(LOG, encoding="utf-8") if '"by": "manual"' in l]
    bad = 0
    for r in rows:
        d, why = classify(r["text"], rules)
        if d != r["decision"]:
            bad += 1
            print(f"РАСХОЖДЕНИЕ: вручную {r['decision']}, правила {d} {why}: {r['text'][:100]!r}")
    print(f"совпало {len(rows) - bad} из {len(rows)}")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("fetch")
    p.add_argument("--channel", help="ID канала UC… (по умолчанию из channel/analysis)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--top", type=int, default=40, help="сколько веток отдать на ответы")
    p.add_argument("--scan", type=int, default=500, help="сколько последних веток просмотреть")
    p = sp.add_parser("moderate")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--recent", type=int, default=100)
    p = sp.add_parser("post")
    p.add_argument("replies")
    p.add_argument("--apply", action="store_true")
    sp.add_parser("test")
    a = ap.parse_args()
    {"fetch": cmd_fetch, "moderate": cmd_moderate, "post": cmd_post, "test": cmd_test}[a.cmd](a)


if __name__ == "__main__":
    main()
