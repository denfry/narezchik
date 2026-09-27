"""Правила модерации и отбор веток для ответа — без сети."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comments  # noqa: E402

RULES = comments.load_rules(comments.RULES_EXAMPLE)


def test_insult_rejected_joke_and_criticism_published():
    assert comments.classify("ты мразь", RULES)[0] == "rejected"
    assert comments.classify("Канал скатился, монтаж так себе", RULES) == ("published", "")
    assert comments.classify("ну и причёска у неё, конечно", RULES) == ("published", "")


def test_yo_is_normalised_and_allow_list_wins():
    assert comments.classify("Мой сосед так же говорит", RULES) == ("published", "")
    assert comments.classify("Я тебя убью", RULES)[1].startswith("threat")


def test_foreign_link_rejected_own_link_kept():
    assert comments.classify("смотри тут https://spam.example.com/x", RULES)[0] == "rejected"
    assert comments.classify("вторая часть: https://youtu.be/abc", RULES) == ("published", "")


def _row(i, likes, days_ago, now, **kw):
    r = {"id": str(i), "video": "v", "author": "a", "text": "нормальный вопрос", "likes": likes,
         "replies": 0, "published_at": (now - dt.timedelta(days=days_ago)).isoformat(),
         "channel_author": False, "answered": False}
    r.update(kw)
    return r


def test_inbox_skips_answered_old_own_and_toxic_sorts_by_likes():
    now = dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)
    rows = [_row(1, 5, 1, now), _row(2, 50, 2, now), _row(3, 99, 1, now, answered=True),
            _row(4, 99, 30, now), _row(5, 99, 1, now, channel_author=True),
            _row(6, 99, 1, now, text="ты мразь"), _row(7, 1, 1, now, replies=10)]
    got = [r["id"] for r in comments.select_inbox(rows, RULES, days=7, top=10, now=now)]
    assert got == ["2", "7", "1"]
