#!/usr/bin/env python
"""Выгрузка и первичный разбор канала YouTube для команды /setup-channel.

Скрипт не принимает редакторских решений — он собирает факты, по которым агент
channel-analyst потом пишет channel/profile.md и channel/brand.json:

  - канал: название, описание, ключевые слова, страна, аватар, баннер;
  - все ролики (до --max): заголовок, описание, теги, длительность, просмотры,
    лайки, комментарии, дата публикации; разбивка на Shorts и лонги;
  - сводка: медианы, лидеры по просмотрам и по просмотрам в день, частота выхода,
    дни недели и часы публикации, паттерны заголовков, ссылки и хэштеги в описаниях;
  - превью: сетки обложек лидеров и последних роликов (одна картинка — удобно
    посмотреть глазами) и доминирующие цвета обложек и аватара;
  - по желанию (--transcripts N, нужен yt-dlp): автосубтитры N лучших роликов —
    как говорит спикер и с чего начинаются удачные ролики.

Нужен только API-ключ YouTube Data API v3 (публичные данные, OAuth не нужен):
--api-key, переменная YOUTUBE_API_KEY или файл secrets/youtube_api_key.txt. Если ключа
нет, но есть OAuth-токен secrets/token.json (docs/publish.md) — используется он.
Как получить ключ — docs/setup-channel.md. Расход квоты — единицы из 10 000 в сутки.

Usage:
    python scripts/channel/analyze_channel.py @mychannel
    python scripts/channel/analyze_channel.py https://www.youtube.com/@mychannel --max 300 --transcripts 5
    python scripts/channel/analyze_channel.py UCxxxxxxxxxxxxxxxxxxxxxx --out channel/analysis

Результат — channel/analysis/: channel.json, videos.json, summary.md, thumbs_top.jpg,
thumbs_recent.jpg, avatar.jpg, transcripts/<video_id>.txt.
"""
import argparse
import collections
import datetime as dt
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = "https://www.googleapis.com/youtube/v3"
# Shorts с октября 2024 бывают до 3 минут; всё, что длиннее, точно лонг.
SHORTS_MAX_SEC = 180

STOPWORDS = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до
вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем
была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним
здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об
другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем
хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это
the a an of to in and for on with is are you your how why what i my we our""".split())


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}][channel] {msg}", flush=True)


# ----------------------------------------------------------------------------- чистые функции

def parse_channel_ref(ref):
    """'@handle' | URL канала | 'UC…' → ('handle'|'id'|'user'|'custom', значение)."""
    ref = ref.strip()
    m = re.search(r"youtube\.com/(@[^/?#]+)", ref)
    if m:
        return "handle", m.group(1)
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", ref)
    if m:
        return "id", m.group(1)
    m = re.search(r"youtube\.com/user/([^/?#]+)", ref)
    if m:
        return "user", m.group(1)
    m = re.search(r"youtube\.com/c/([^/?#]+)", ref)
    if m:
        return "custom", m.group(1)
    if re.fullmatch(r"UC[\w-]{22}", ref):
        return "id", ref
    if ref.startswith("@"):
        return "handle", ref
    return "handle", "@" + ref


def iso_duration_sec(s):
    """ISO 8601 'PT1H2M3S' → 3723. Прямые трансляции без длительности → 0."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def title_features(title):
    words = re.findall(r"[\wёЁ]+", title)
    letters = [c for c in title if c.isalpha()]
    return {
        "len": len(title),
        "words": len(words),
        "question": "?" in title,
        "exclaim": "!" in title,
        "number": bool(re.search(r"\d", title)),
        "caps_share": round(sum(c.isupper() for c in letters) / len(letters), 2) if letters else 0,
        "emoji": bool(re.search("[\U0001F300-\U0001FAFF☀-➿]", title)),
        "separator": bool(re.search(r"\s[|/—–-]\s", title)),
        "hashtag": "#" in title,
        "quote": bool(re.search(r"[«»\"“”]", title)),
    }


def median(xs):
    return statistics.median(xs) if xs else 0


def fmt_int(n):
    return f"{int(n):,}".replace(",", " ")


def fmt_dur(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


# ----------------------------------------------------------------------------- API

class YT:
    def __init__(self, key=None, bearer=None):
        import requests
        self.key = key
        self.http = requests.Session()
        self.bearer = bearer
        self.units = 0

    def get(self, method, **params):
        headers = {}
        if self.key:
            params["key"] = self.key
        else:
            headers["Authorization"] = f"Bearer {self.bearer}"
        for attempt in range(5):
            r = self.http.get(f"{API}/{method}", params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                self.units += 1
                return r.json()
            if r.status_code in (500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            try:
                err = r.json()["error"]
                reason = err.get("errors", [{}])[0].get("reason", "")
                msg = f"{err.get('code')} {reason}: {err.get('message')}"
            except Exception:  # noqa: BLE001
                msg = f"HTTP {r.status_code}: {r.text[:300]}"
            raise SystemExit(f"YouTube API {method}: {msg}")
        raise SystemExit(f"YouTube API {method}: сервер не отвечает")

    def channel(self, ref):
        kind, value = parse_channel_ref(ref)
        parts = "snippet,statistics,contentDetails,brandingSettings,topicDetails"
        if kind == "id":
            res = self.get("channels", part=parts, id=value)
        elif kind == "handle":
            res = self.get("channels", part=parts, forHandle=value)
        elif kind == "user":
            res = self.get("channels", part=parts, forUsername=value)
        else:
            # /c/<custom> в API не резолвится напрямую — ищем через search (100 единиц квоты)
            s = self.get("search", part="snippet", q=value, type="channel", maxResults=1)
            items = s.get("items") or []
            if not items:
                raise SystemExit(f"канал не найден: {ref}")
            res = self.get("channels", part=parts, id=items[0]["snippet"]["channelId"])
        items = res.get("items") or []
        if not items:
            raise SystemExit(f"канал не найден: {ref} (проверьте @handle или дайте ID вида UC…)")
        return items[0]

    def upload_ids(self, uploads_playlist, limit):
        ids, token = [], None
        while len(ids) < limit:
            res = self.get("playlistItems", part="contentDetails", playlistId=uploads_playlist,
                           maxResults=50, **({"pageToken": token} if token else {}))
            ids += [it["contentDetails"]["videoId"] for it in res.get("items", [])]
            token = res.get("nextPageToken")
            if not token:
                break
        return ids[:limit]

    def videos(self, ids):
        out = []
        for i in range(0, len(ids), 50):
            res = self.get("videos", part="snippet,contentDetails,statistics,liveStreamingDetails",
                           id=",".join(ids[i:i + 50]), maxResults=50)
            out += res.get("items", [])
        return out


def is_short_url(video_id, http):
    """Ролик — Shorts, если /shorts/<id> отдаётся без редиректа на /watch."""
    try:
        r = http.head(f"https://www.youtube.com/shorts/{video_id}", allow_redirects=False, timeout=15)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- картинки

def download(http, url, path):
    try:
        r = http.get(url, timeout=30)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def best_thumb(snippet):
    th = snippet.get("thumbnails", {})
    for k in ("maxres", "standard", "high", "medium", "default"):
        if k in th:
            return th[k]["url"]
    return None


def contact_sheet(paths, out, cols=4, tile_w=480):
    from PIL import Image
    imgs = []
    for p in paths:
        try:
            imgs.append(Image.open(p).convert("RGB"))
        except Exception:  # noqa: BLE001
            continue
    if not imgs:
        return False
    tile_h = int(tile_w * 9 / 16)
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (0, 0, 0))
    for i, im in enumerate(imgs):
        im = im.resize((tile_w, tile_h))
        sheet.paste(im, ((i % cols) * tile_w, (i // cols) * tile_h))
    sheet.save(out, quality=88)
    return True


def dominant_colors(paths, n=6):
    """Самые частые насыщенные цвета по всем картинкам — кандидаты в accent_color."""
    from PIL import Image
    counter = collections.Counter()
    for p in paths:
        try:
            im = Image.open(p).convert("RGB").resize((160, 90))
        except Exception:  # noqa: BLE001
            continue
        q = im.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()
        for count, idx in q.getcolors():
            r, g, b = pal[idx * 3:idx * 3 + 3]
            mx, mn = max(r, g, b), min(r, g, b)
            sat = (mx - mn) / mx if mx else 0
            if sat < 0.35 or mx < 70:
                continue  # серое, чёрное и тёмное акцентом не бывает
            counter[(r // 24 * 24, g // 24 * 24, b // 24 * 24)] += count
    return ["#%02X%02X%02X" % c for c, _ in counter.most_common(n)]


# ----------------------------------------------------------------------------- транскрипты

def fetch_transcript(video_id, lang, out_dir):
    """Автосубтитры через yt-dlp → чистый текст. None, если yt-dlp нет или субтитров нет."""
    exe = shutil.which("yt-dlp")
    if not exe:
        return None
    tmp = os.path.join(out_dir, f"{video_id}.tmp")
    cmd = [exe, "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", f"{lang}.*,{lang}",
           "--sub-format", "vtt", "-o", tmp, f"https://www.youtube.com/watch?v={video_id}"]
    subprocess.run(cmd, capture_output=True, timeout=180)
    vtts = [f for f in os.listdir(out_dir) if f.startswith(f"{video_id}.tmp") and f.endswith(".vtt")]
    if not vtts:
        return None
    lines, last = [], None
    with io.open(os.path.join(out_dir, vtts[0]), encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")) or line.isdigit():
                continue
            line = re.sub(r"<[^>]+>", "", line).strip()
            if line and line != last:
                lines.append(line)
                last = line
    for v in vtts:
        os.remove(os.path.join(out_dir, v))
    text = " ".join(lines)
    path = os.path.join(out_dir, f"{video_id}.txt")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return path


# ----------------------------------------------------------------------------- сводка

def enrich(videos, now, tz):
    rows = []
    for v in videos:
        sn, st, cd = v["snippet"], v.get("statistics", {}), v.get("contentDetails", {})
        pub = dt.datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        age_days = max((now - pub).total_seconds() / 86400, 1.0)
        views = int(st.get("viewCount", 0))
        dur = iso_duration_sec(cd.get("duration"))
        local = pub.astimezone(tz)
        rows.append({
            "id": v["id"],
            "url": f"https://youtu.be/{v['id']}",
            "title": sn["title"],
            "description": sn.get("description", ""),
            "tags": sn.get("tags", []),
            "published_at": sn["publishedAt"],
            "weekday": local.strftime("%a"),
            "hour_local": local.hour,
            "duration_sec": dur,
            "views": views,
            "likes": int(st.get("likeCount", 0)),
            "comments": int(st.get("commentCount", 0)),
            "views_per_day": round(views / age_days, 1),
            "age_days": round(age_days, 1),
            "was_live": "liveStreamingDetails" in v,
            "language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
            "thumbnail": best_thumb(sn),
            "title_features": title_features(sn["title"]),
        })
    return rows


def group_stats(rows):
    if not rows:
        return None
    views = [r["views"] for r in rows]
    gaps = []
    dates = sorted(dt.datetime.fromisoformat(r["published_at"].replace("Z", "+00:00")) for r in rows)
    for a, b in zip(dates, dates[1:]):
        gaps.append((b - a).total_seconds() / 86400)
    return {
        "count": len(rows),
        "median_views": median(views),
        "mean_views": round(sum(views) / len(views)),
        "max_views": max(views),
        "median_duration_sec": median([r["duration_sec"] for r in rows]),
        "median_gap_days": round(median(gaps), 1) if gaps else None,
        "engagement_like_per_1k": round(1000 * sum(r["likes"] for r in rows) / max(1, sum(views)), 1),
        "comments_per_1k": round(1000 * sum(r["comments"] for r in rows) / max(1, sum(views)), 2),
        "weekday": dict(collections.Counter(r["weekday"] for r in rows).most_common()),
        "hour_local": dict(sorted(collections.Counter(r["hour_local"] for r in rows).items())),
    }


def title_stats(rows):
    if not rows:
        return {}
    feats = [r["title_features"] for r in rows]
    share = lambda k: round(sum(1 for f in feats if f[k]) / len(feats), 2)  # noqa: E731
    words = collections.Counter()
    for r in rows:
        for w in re.findall(r"[\wёЁ]+", r["title"].lower()):
            if len(w) > 2 and w not in STOPWORDS and not w.isdigit():
                words[w] += 1
    # общий хвост заголовков («| Имя Фамилия») — это фирменная подпись, её стоит знать
    tails = collections.Counter(re.split(r"\s[|/—–]\s", r["title"])[-1].strip()
                                for r in rows if re.search(r"\s[|/—–]\s", r["title"]))
    return {
        "median_len": median([f["len"] for f in feats]),
        "question_share": share("question"),
        "number_share": share("number"),
        "exclaim_share": share("exclaim"),
        "emoji_share": share("emoji"),
        "separator_share": share("separator"),
        "hashtag_share": share("hashtag"),
        "median_caps_share": median([f["caps_share"] for f in feats]),
        "top_words": words.most_common(25),
        "common_tail": [t for t, c in tails.most_common(3) if c >= max(3, len(rows) // 5)],
    }


def description_stats(rows):
    domains, hashtags, first_lines = collections.Counter(), collections.Counter(), collections.Counter()
    lengths = []
    for r in rows:
        d = r["description"]
        lengths.append(len(d))
        for m in re.findall(r"https?://([^/\s]+)", d):
            domains[m.lower().removeprefix("www.")] += 1
        for h in re.findall(r"#[\wёЁ]+", d):
            hashtags[h.lower()] += 1
        first = d.strip().split("\n", 1)[0].strip()
        if first:
            first_lines[first[:120]] += 1
    tags = collections.Counter(t.lower() for r in rows for t in r["tags"])
    return {
        "median_len": median(lengths),
        "link_domains": domains.most_common(10),
        "hashtags": hashtags.most_common(15),
        "repeated_first_lines": [(l, c) for l, c in first_lines.most_common(5) if c > 1],
        "tags": tags.most_common(30),
        "videos_with_tags_share": round(sum(1 for r in rows if r["tags"]) / max(1, len(rows)), 2),
    }


def write_summary(path, ch, shorts, longs, extra):
    sn, st = ch["snippet"], ch.get("statistics", {})
    L = [f"# Разбор канала: {sn['title']}", "",
         f"Собрано {extra['collected_at']} скриптом analyze_channel.py. Это факты; выводы и профиль "
         f"пишет агент channel-analyst в channel/profile.md.", "",
         "## Канал", "",
         f"- URL: https://www.youtube.com/{sn.get('customUrl') or 'channel/' + ch['id']}",
         f"- ID: `{ch['id']}`, страна: {sn.get('country', '—')}, создан: {sn.get('publishedAt', '')[:10]}",
         f"- Подписчики: {fmt_int(st.get('subscriberCount', 0)) if not st.get('hiddenSubscriberCount') else 'скрыты'}, "
         f"просмотры: {fmt_int(st.get('viewCount', 0))}, роликов: {st.get('videoCount', '—')}",
         f"- Ключевые слова канала: {ch.get('brandingSettings', {}).get('channel', {}).get('keywords', '—')}",
         f"- Разобрано роликов: {len(shorts) + len(longs)} (Shorts {len(shorts)}, лонги {len(longs)})", "",
         "### Описание канала", "", "```", sn.get("description", "").strip() or "—", "```", ""]

    for name, rows, stats in (("Shorts", shorts, extra["shorts_stats"]), ("Лонги", longs, extra["longs_stats"])):
        if not rows:
            L += [f"## {name}", "", "Нет.", ""]
            continue
        L += [f"## {name}", "",
              f"- Всего: {stats['count']}, медиана просмотров: {fmt_int(stats['median_views'])}, "
              f"максимум: {fmt_int(stats['max_views'])}",
              f"- Медианная длительность: {fmt_dur(stats['median_duration_sec'])}, "
              f"медианный интервал между выпусками: {stats['median_gap_days']} дн.",
              f"- Лайков на 1000 просмотров: {stats['engagement_like_per_1k']}, комментариев: {stats['comments_per_1k']}",
              f"- Дни недели: {stats['weekday']}",
              f"- Часы публикации ({extra['tz_label']}): {stats['hour_local']}", "",
              f"### Лидеры по просмотрам в день ({name})", "",
              "| # | Просмотры | В день | Длит. | Дата | Заголовок |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(sorted(rows, key=lambda r: -r["views_per_day"])[:12], 1):
            L.append(f"| {i} | {fmt_int(r['views'])} | {fmt_int(r['views_per_day'])} | {fmt_dur(r['duration_sec'])} | "
                     f"{r['published_at'][:10]} | [{r['title']}]({r['url']}) |")
        L += ["", f"### Аутсайдеры ({name}, старше 14 дней)", ""]
        old = [r for r in rows if r["age_days"] > 14]
        for r in sorted(old, key=lambda r: r["views_per_day"])[:6]:
            L.append(f"- {fmt_int(r['views'])} просм. — {r['title']} ({fmt_dur(r['duration_sec'])})")
        L += ["", f"### Последние 10 ({name})", ""]
        for r in sorted(rows, key=lambda r: r["published_at"], reverse=True)[:10]:
            L.append(f"- {r['published_at'][:10]} — {fmt_int(r['views'])} просм. — {r['title']}")
        L.append("")

    ts = extra["title_stats"]
    L += ["## Заголовки (все ролики)", "",
          f"- Медианная длина: {ts.get('median_len')} символов; доля с вопросом {ts.get('question_share')}, "
          f"с цифрой {ts.get('number_share')}, с «!» {ts.get('exclaim_share')}, с эмодзи {ts.get('emoji_share')}, "
          f"с разделителем «|/—» {ts.get('separator_share')}, с хэштегом {ts.get('hashtag_share')}",
          f"- Медианная доля заглавных букв: {ts.get('median_caps_share')}",
          f"- Повторяющийся хвост заголовка: {ts.get('common_tail') or '—'}",
          f"- Частые слова: {', '.join(f'{w} ({c})' for w, c in ts.get('top_words', []))}", ""]
    ds = extra["description_stats"]
    L += ["## Описания и теги", "",
          f"- Медианная длина описания: {ds['median_len']} символов",
          f"- Домены ссылок: {ds['link_domains'] or '—'}",
          f"- Повторяющиеся первые строки (вероятный шаблон/CTA): {ds['repeated_first_lines'] or '—'}",
          f"- Хэштеги: {ds['hashtags'] or '—'}",
          f"- Доля роликов с тегами: {ds['videos_with_tags_share']}; частые теги: {ds['tags'][:20] or '—'}", "",
          "## Визуал", "",
          f"- Доминирующие насыщенные цвета обложек: {extra['thumb_colors'] or '—'}",
          f"- Цвета аватара: {extra['avatar_colors'] or '—'}",
          "- Сетки обложек: `thumbs_top.jpg` (лидеры), `thumbs_recent.jpg` (последние) — посмотреть глазами.", ""]
    if extra["transcripts"]:
        L += ["## Транскрипты (автосубтитры, yt-dlp)", ""]
        for vid, p in extra["transcripts"]:
            L.append(f"- `{os.path.relpath(p, ROOT)}` — {vid}")
        L.append("")
    L += [f"Квота API за прогон: ~{extra['api_units']} единиц.", ""]
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ----------------------------------------------------------------------------- main

def api_key(arg):
    if arg:
        return arg
    if os.environ.get("YOUTUBE_API_KEY"):
        return os.environ["YOUTUBE_API_KEY"]
    p = os.path.join(ROOT, "secrets", "youtube_api_key.txt")
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    return None


def oauth_bearer():
    """Запасной путь без API-ключа: OAuth-токен из secrets/token.json (docs/publish.md)."""
    p = os.path.join(ROOT, "secrets", "token.json")
    if not os.path.isfile(p):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(p)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        open(p, "w", encoding="utf-8").write(creds.to_json())
    return creds.token


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("channel", help="@handle, ссылка на канал или ID UC…")
    ap.add_argument("--api-key")
    ap.add_argument("--max", type=int, default=250, help="сколько последних роликов разбирать")
    ap.add_argument("--out", default="channel/analysis")
    ap.add_argument("--utc-offset", type=float, default=3.0, help="часовой пояс для часов публикации")
    ap.add_argument("--transcripts", type=int, default=0,
                    help="скачать автосубтитры N лучших роликов через yt-dlp (если установлен)")
    ap.add_argument("--lang", default="ru", help="язык автосубтитров")
    ap.add_argument("--no-shorts-check", action="store_true",
                    help="не проверять ролики 61–180 с через youtube.com/shorts (считать их лонгами)")
    a = ap.parse_args()

    out = a.out if os.path.isabs(a.out) else os.path.join(ROOT, a.out)
    thumbs_dir = os.path.join(out, "thumbs")
    os.makedirs(thumbs_dir, exist_ok=True)
    tz = dt.timezone(dt.timedelta(hours=a.utc_offset))
    now = dt.datetime.now(dt.timezone.utc)

    key = api_key(a.api_key)
    bearer = None if key else oauth_bearer()
    if not key and not bearer:
        raise SystemExit("нет доступа к API: --api-key, YOUTUBE_API_KEY, secrets/youtube_api_key.txt "
                         "или OAuth-токен secrets/token.json (docs/setup-channel.md)")
    yt = YT(key, bearer)
    ch = yt.channel(a.channel)
    log(f"канал: {ch['snippet']['title']} ({ch['id']})")
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids = yt.upload_ids(uploads, a.max)
    log(f"роликов к разбору: {len(ids)}")
    rows = enrich(yt.videos(ids), now, tz)

    for r in rows:
        if r["duration_sec"] <= 60:
            r["is_short"] = True
        elif r["duration_sec"] <= SHORTS_MAX_SEC and not a.no_shorts_check:
            r["is_short"] = bool(is_short_url(r["id"], yt.http))
        else:
            r["is_short"] = False
    shorts = [r for r in rows if r["is_short"]]
    longs = [r for r in rows if not r["is_short"] and r["duration_sec"] > 0]

    # обложки: лидеры каждого формата и последние ролики
    def grab(sel, tag):
        paths = []
        for r in sel:
            if not r["thumbnail"]:
                continue
            p = os.path.join(thumbs_dir, f"{r['id']}.jpg")
            if os.path.isfile(p) or download(yt.http, r["thumbnail"], p):
                paths.append(p)
        return paths
    top = (sorted(longs, key=lambda r: -r["views_per_day"])[:8]
           + sorted(shorts, key=lambda r: -r["views_per_day"])[:4])
    recent = sorted(rows, key=lambda r: r["published_at"], reverse=True)[:12]
    top_paths, recent_paths = grab(top, "top"), grab(recent, "recent")
    contact_sheet(top_paths, os.path.join(out, "thumbs_top.jpg"))
    contact_sheet(recent_paths, os.path.join(out, "thumbs_recent.jpg"))
    thumb_colors = dominant_colors(top_paths + recent_paths)

    avatar = ch["snippet"].get("thumbnails", {}).get("high", {}).get("url")
    avatar_path = os.path.join(out, "avatar.jpg")
    avatar_colors = dominant_colors([avatar_path], 3) if avatar and download(yt.http, avatar, avatar_path) else []

    transcripts = []
    if a.transcripts:
        tdir = os.path.join(out, "transcripts")
        os.makedirs(tdir, exist_ok=True)
        if not shutil.which("yt-dlp"):
            log("yt-dlp не установлен — транскрипты пропущены (pip install yt-dlp)")
        else:
            best = sorted(longs or shorts, key=lambda r: -r["views_per_day"])[:a.transcripts]
            for r in best:
                p = fetch_transcript(r["id"], a.lang, tdir)
                log(f"транскрипт {r['id']}: {'ok' if p else 'нет субтитров'}")
                if p:
                    transcripts.append((r["id"], p))

    extra = {
        "collected_at": now.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "tz_label": f"UTC{a.utc_offset:+g}",
        "shorts_stats": group_stats(shorts),
        "longs_stats": group_stats(longs),
        "title_stats": title_stats(rows),
        "description_stats": description_stats(rows),
        "thumb_colors": thumb_colors,
        "avatar_colors": avatar_colors,
        "transcripts": transcripts,
        "api_units": yt.units,
    }
    with io.open(os.path.join(out, "channel.json"), "w", encoding="utf-8") as f:
        json.dump(ch, f, ensure_ascii=False, indent=2)
    with io.open(os.path.join(out, "videos.json"), "w", encoding="utf-8") as f:
        json.dump({"collected_at": extra["collected_at"], "videos": rows,
                   "stats": {k: v for k, v in extra.items() if k.endswith("stats")}},
                  f, ensure_ascii=False, indent=1)
    write_summary(os.path.join(out, "summary.md"), ch, shorts, longs, extra)
    log(f"готово → {os.path.relpath(out, ROOT)}/summary.md (Shorts {len(shorts)}, лонги {len(longs)}, "
        f"квота ~{yt.units})")


if __name__ == "__main__":
    main()
