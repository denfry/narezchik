"""Чистые функции разбора канала — без сети и без API-ключа."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_channel as ac  # noqa: E402


def test_parse_channel_ref_variants():
    assert ac.parse_channel_ref("@mychan") == ("handle", "@mychan")
    assert ac.parse_channel_ref("mychan") == ("handle", "@mychan")
    assert ac.parse_channel_ref("https://www.youtube.com/@mychan/videos") == ("handle", "@mychan")
    cid = "UC" + "a" * 22
    assert ac.parse_channel_ref(cid) == ("id", cid)
    assert ac.parse_channel_ref(f"https://youtube.com/channel/{cid}") == ("id", cid)
    assert ac.parse_channel_ref("https://www.youtube.com/user/oldname") == ("user", "oldname")
    assert ac.parse_channel_ref("https://www.youtube.com/c/custom") == ("custom", "custom")


def test_iso_duration():
    assert ac.iso_duration_sec("PT1H2M3S") == 3723
    assert ac.iso_duration_sec("PT45S") == 45
    assert ac.iso_duration_sec("P1DT1S") == 86401
    assert ac.iso_duration_sec("P0D") == 0
    assert ac.iso_duration_sec(None) == 0


def test_title_features():
    f = ac.title_features("ПОЧЕМУ 95% мужчин ошибаются? | Иван Петров")
    assert f["question"] and f["number"] and f["separator"]
    assert not f["emoji"]


def _video(vid, title, views, published, duration="PT10M"):
    return {"id": vid, "snippet": {"title": title, "publishedAt": published, "description": "https://t.me/x #тег"},
            "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "1"},
            "contentDetails": {"duration": duration}}


def test_enrich_and_stats():
    now = dt.datetime(2026, 9, 27, 9, tzinfo=dt.timezone.utc)
    tz = dt.timezone(dt.timedelta(hours=3))
    rows = ac.enrich([_video("a", "Раз | Канал", 1000, "2026-09-17T09:00:00Z"),
                      _video("b", "Два | Канал", 3000, "2026-09-07T09:00:00Z"),
                      _video("c", "Три | Канал", 500, "2026-09-26T09:00:00Z", "PT40S")], now, tz)
    assert rows[0]["views_per_day"] == 100.0
    assert rows[0]["hour_local"] == 12
    st = ac.group_stats(rows)
    assert st["count"] == 3 and st["median_views"] == 1000
    ts = ac.title_stats(rows)
    assert ts["common_tail"] == ["Канал"]
    ds = ac.description_stats(rows)
    assert ds["link_domains"][0][0] == "t.me"
    assert ds["hashtags"][0][0] == "#тег"
