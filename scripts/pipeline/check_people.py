"""Проверка готового ролика: где в кадре нет людей.

Идёт по output/<clip>.mp4 с выборкой sample_fps, детектит лица тем же
mediapipe-лэндмаркером, что и autoframe, и печатает отрезки без лиц длиннее
min_gap секунд. Обложка в начале, финальная плашка и партнёрские врезки —
законные отрезки без лица, поэтому их можно исключить через --plan: скрипт
возьмёт cover_frame/tail_card/visual_events с insert_purpose=cta из edit plan.

Usage: python check_people.py output/x.mp4 [--plan edit-plans/x.json] [--json-out ...]
Exit code 1, если есть незаконные отрезки без лиц.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from autoframe import detect_faces  # noqa: E402
from common import log, resolve_repo_path  # noqa: E402


def _gaps(frames, min_gap):
    """Отрезки подряд идущих выборочных кадров без лиц длиннее min_gap."""
    gaps, start = [], None
    for f in frames:
        if not f["faces"]:
            if start is None:
                start = f["t"]
        elif start is not None:
            if f["t"] - start >= min_gap:
                gaps.append((start, f["t"]))
            start = None
    if start is not None and frames and frames[-1]["t"] - start >= min_gap:
        gaps.append((start, frames[-1]["t"]))
    return gaps


def _edge_runs(frames, width, min_gap, edge_frac=0.2):
    """Отрезки, где лица в кадре есть, но ни одно не стоит в средней полосе кадра.

    Проверка «есть ли лицо» не видит полусрезанного человека у края вертикального
    кропа: mediapipe находит его лицо, и отрезок считается нормальным. Именно так
    прошёл QC один из шортсов, где кроп после склеек начинался на пустом столе и
    ступеньками доезжал до говорящего.
    """
    lo, hi = width * edge_frac, width * (1 - edge_frac)
    runs, start = [], None
    for f in frames:
        at_edge = bool(f["faces"]) and all(not (lo <= g["cx"] <= hi) for g in f["faces"])
        if at_edge:
            if start is None:
                start = f["t"]
        elif start is not None:
            if f["t"] - start >= min_gap:
                runs.append((start, f["t"]))
            start = None
    if start is not None and frames and frames[-1]["t"] - start >= min_gap:
        runs.append((start, frames[-1]["t"]))
    return runs


def _allowed_ranges(plan, duration):
    """Отрезки выходного ролика, где лицо не обязано быть."""
    if not plan:
        return []
    rs = plan.get("render_settings", {})
    allowed = []
    cover = rs.get("cover_frame", {})
    cover_sec = float(cover.get("duration_sec", 0)) if cover.get("enabled") else 0.0
    if cover_sec:
        allowed.append((0.0, cover_sec))
    tail = rs.get("tail_card", {})
    if tail.get("enabled"):
        allowed.append((duration - float(tail.get("duration_sec", 3.0)), duration))
    for ev in plan.get("visual_events", []):
        if ev.get("type") == "broll" and ev.get("insert_mode", "fullscreen") == "fullscreen":
            allowed.append((ev["start_sec"] + cover_sec, ev["end_sec"] + cover_sec))
    return allowed


def _covered(gap, allowed):
    s, e = gap
    return any(a <= s + 0.3 and e - 0.3 <= b for a, b in allowed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--plan")
    ap.add_argument("--sample-fps", type=float, default=2.0)
    ap.add_argument("--min-gap", type=float, default=1.0,
                    help="минимальная длина отрезка без лиц, чтобы считать его проблемой")
    ap.add_argument("--json-out")
    a = ap.parse_args()

    video = resolve_repo_path(a.video)
    frames = detect_faces(video, sample_fps=a.sample_fps)
    duration = frames[-1]["t"] if frames else 0.0
    plan = json.load(open(resolve_repo_path(a.plan), encoding="utf-8")) if a.plan else None
    allowed = _allowed_ranges(plan, duration)
    gaps = _gaps(frames, a.min_gap)
    bad = [g for g in gaps if not _covered(g, allowed)]
    no_face_share = sum(1 for f in frames if not f["faces"]) / max(1, len(frames))

    import cv2
    cap = cv2.VideoCapture(video)
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    cap.release()
    edge = [r for r in _edge_runs(frames, width, a.min_gap) if not _covered(r, allowed)]

    report = {
        "video": os.path.basename(video),
        "duration_sec": round(duration, 2),
        "frames_sampled": len(frames),
        "no_face_share": round(no_face_share, 3),
        "gaps": [[round(s, 2), round(e, 2)] for s, e in gaps],
        "allowed": [[round(s, 2), round(e, 2)] for s, e in allowed],
        "bad_gaps": [[round(s, 2), round(e, 2)] for s, e in bad],
        "edge_runs": [[round(s, 2), round(e, 2)] for s, e in edge],
    }
    if a.json_out:
        json.dump(report, open(resolve_repo_path(a.json_out), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    log(f"{report['video']}: кадров без лиц {no_face_share:.0%}, "
        f"отрезков без лиц {len(gaps)}, из них незаконных {len(bad)}: {report['bad_gaps']}; "
        f"человек у края кадра: {report['edge_runs']}")
    sys.exit(1 if bad or edge else 0)


if __name__ == "__main__":
    main()
