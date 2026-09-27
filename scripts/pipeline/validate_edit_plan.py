#!/usr/bin/env python
"""Валидатор edit plan: JSON Schema + смысловые sanity-checks, которые схема сама
по себе выразить не может (пересечение таймкодов с длительностью исходника, битые
ссылки id между секциями, существование asset-файлов на диске).

Ничего не чинит и не принимает решений — только репортит список проблем. Запускать
после каждой стадии orchestrator-пайплайна перед тем, как передавать план дальше.

Usage:
    python validate_edit_plan.py edit-plans/interview_short03.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCHEMA_PATH, load_json, log, resolve_repo_path


def schema_errors(plan, schema):
    try:
        import jsonschema
    except ImportError:
        log("jsonschema не установлен — пропускаю формальную проверку схемы, "
            "выполняю только smoke-checks обязательных полей (pip install jsonschema для полной проверки)")
        errors = []
        for key in schema.get("required", []):
            if key not in plan:
                errors.append(f"отсутствует обязательное поле верхнего уровня '{key}'")
        return errors

    validator = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in validator.iter_errors(plan)]


def sanity_errors(plan):
    errors = []
    warnings = []

    source = plan.get("source", {})
    src_duration = source.get("duration_sec")

    clip = plan.get("clip", {})
    if clip:
        cs, ce = clip.get("source_start_sec"), clip.get("source_end_sec")
        if cs is not None and ce is not None:
            if cs >= ce:
                errors.append(f"clip.source_start_sec ({cs}) >= clip.source_end_sec ({ce})")
            if src_duration is not None and (cs < 0 or ce > src_duration + 0.5):
                errors.append(f"clip [{cs}, {ce}] выходит за пределы длительности исходника ({src_duration}s)")

    seg_ids = set()
    for i, seg in enumerate(plan.get("speech_edits", [])):
        sid = seg.get("id")
        if not sid:
            errors.append(f"speech_edits[{i}] без 'id'")
            continue
        if sid in seg_ids:
            errors.append(f"speech_edits: дублирующийся id '{sid}'")
        seg_ids.add(sid)
        s, e = seg.get("source_start_sec"), seg.get("source_end_sec")
        if s is not None and e is not None:
            if s >= e:
                errors.append(f"speech_edits[{sid}]: start ({s}) >= end ({e})")
            # сегмент из внешнего файла (source_path) живёт в координатах того файла
            if (src_duration is not None and not seg.get("source_path")
                    and (s < 0 or e > src_duration + 0.5)):
                errors.append(f"speech_edits[{sid}] [{s}, {e}] выходит за пределы исходника ({src_duration}s)")

    if not seg_ids:
        warnings.append("speech_edits пуст — план ещё не прошёл shorts-script-editor")

    asset_ids = set()
    for i, asset in enumerate(plan.get("assets", [])):
        aid = asset.get("id")
        if not aid:
            errors.append(f"assets[{i}] без 'id'")
            continue
        asset_ids.add(aid)
        path = asset.get("path")
        if path:
            if not os.path.isfile(resolve_repo_path(path)):
                errors.append(f"assets[{aid}]: файл не найден '{path}'")
        elif not asset.get("search_query"):
            errors.append(f"assets[{aid}]: нет ни 'path', ни 'search_query'")

    total_target_dur = None
    if seg_ids:
        # target timeline length = сумма длительностей speech_edits после реза
        total_target_dur = sum(
            (s.get("source_end_sec", 0) - s.get("source_start_sec", 0))
            for s in plan.get("speech_edits", [])
        )

    for kind in ("visual_events", "subtitle_events", "audio_events"):
        for i, ev in enumerate(plan.get(kind, [])):
            s, e = ev.get("start_sec"), ev.get("end_sec")
            if s is not None and e is not None and s >= e:
                errors.append(f"{kind}[{i}]: start_sec ({s}) >= end_sec ({e})")
            if total_target_dur is not None and e is not None and e > total_target_dur + 1.0:
                warnings.append(
                    f"{kind}[{i}]: end_sec ({e}) выходит за расчётную длину ролика ({total_target_dur:.1f}s)"
                )
            seg_ref = ev.get("segment_ref")
            if seg_ref and seg_ids and seg_ref not in seg_ids:
                errors.append(f"{kind}[{i}]: segment_ref '{seg_ref}' не найден в speech_edits")
            asset_ref = ev.get("asset_ref")
            if asset_ref and asset_ref not in asset_ids:
                errors.append(f"{kind}[{i}]: asset_ref '{asset_ref}' не найден в assets")

    rs = plan.get("render_settings") or {}
    src_fps, out_fps = source.get("fps"), rs.get("fps")
    if src_fps and out_fps and abs(float(src_fps) - float(out_fps)) > 0.01 \
            and not rs.get("allow_fps_change"):
        errors.append(
            f"render_settings.fps ({out_fps}) не равен fps исходника ({src_fps}): кадры будут "
            f"дублироваться, движение дёргается. Поставьте fps = {src_fps} "
            f"(или allow_fps_change: true, если это осознанно)")
    portrait = rs.get("height", 0) > rs.get("width", 0)
    for key in ("cover_frame", "tail_card"):
        cfg = rs.get(key) or {}
        path = cfg.get("image_path")
        if not (cfg.get("enabled") and path):
            continue
        full = resolve_repo_path(path)
        if not os.path.isfile(full):
            errors.append(f"render_settings.{key}.image_path: файл не найден '{path}'")
            continue
        try:
            from PIL import Image
            with Image.open(full) as im:
                iw, ih = im.size
        except Exception:  # noqa: BLE001
            continue
        if (ih > iw) != portrait:
            errors.append(
                f"render_settings.{key}: картинка {iw}x{ih} не той ориентации, что ролик "
                f"({rs.get('width')}x{rs.get('height')}) — при рендере обрежется по краям вместе "
                f"с текстом. Для Shorts: make_cards.py ... --vertical")
    return errors, warnings


def validate(plan_path, schema_path=SCHEMA_PATH):
    plan = load_json(resolve_repo_path(plan_path))
    schema = load_json(schema_path)

    errors = schema_errors(plan, schema)
    sane_errors, warnings = sanity_errors(plan)
    errors += sane_errors
    return errors, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("edit_plan", help="путь к edit-plans/<clip_id>.json")
    ap.add_argument("--schema", default=SCHEMA_PATH)
    args = ap.parse_args()

    errors, warnings = validate(args.edit_plan, args.schema)

    for w in warnings:
        log(f"WARN: {w}")
    if errors:
        for e in errors:
            log(f"FAIL: {e}")
        log(f"invalid — {len(errors)} error(s)")
        sys.exit(1)

    log("valid")


if __name__ == "__main__":
    main()
