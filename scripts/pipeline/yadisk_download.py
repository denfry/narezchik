#!/usr/bin/env python
"""Надёжная докачка большого файла с публичного Яндекс.Диска.

Зачем: прямые ссылки живут недолго и ведут на storage-хосты, которые рвут соединение
(Recv failure / Could not resolve host). curl -r в 8 потоков склеивал недокачанные
части и отдавал «готовый» файл на 1.7 ГБ вместо 18. Здесь каждая часть докачивается
по Range с текущего размера до полного, ссылка обновляется через API при каждой
повторной попытке, и итог собирается только когда сумма частей равна Content-Length.

Usage:
    python yadisk_download.py "/Подкасты/Выпуск 12.mp4" "input/Выпуск 12.mp4"         --public-key https://disk.yandex.ru/d/XXXX --threads 6
Ключ можно не передавать, если задана переменная окружения YADISK_PUBLIC_KEY или поле
yadisk_public_key в channel/queue.json.
"""
import argparse
import os
import sys
import threading
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CHANNEL_DIR, TEMP_DIR, log  # noqa: E402

PUBLIC_KEY = os.environ.get("YADISK_PUBLIC_KEY", "")
API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"


def queue_public_key():
    import json
    p = os.path.join(CHANNEL_DIR, "queue.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("yadisk_public_key", "")
    return ""


def fresh_href(path):
    for attempt in range(10):
        try:
            r = requests.get(API, params={"public_key": PUBLIC_KEY, "path": path}, timeout=30)
            r.raise_for_status()
            return r.json()["href"]
        except Exception as e:  # noqa: BLE001
            log(f"href retry {attempt}: {e}")
            time.sleep(5)
    raise SystemExit("cannot get download href")


def total_size(href):
    r = requests.head(href, allow_redirects=True, timeout=60)
    return int(r.headers["Content-Length"])


def fetch_part(path, part_path, start, end, state):
    want = end - start + 1
    while True:
        have = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        if have >= want:
            return
        try:
            href = fresh_href(path)
            hdr = {"Range": f"bytes={start + have}-{end}"}
            with requests.get(href, headers=hdr, stream=True, timeout=(30, 120), allow_redirects=True) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"status {r.status_code}")
                with open(part_path, "ab") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if chunk:
                            f.write(chunk)
                            state["done"] += len(chunk)
        except Exception as e:  # noqa: BLE001
            state["errors"] += 1
            time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("disk_path")
    ap.add_argument("out")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--public-key", help="публичная ссылка на папку Яндекс.Диска")
    a = ap.parse_args()
    global PUBLIC_KEY
    PUBLIC_KEY = a.public_key or PUBLIC_KEY or queue_public_key()
    if not PUBLIC_KEY:
        raise SystemExit("нужна публичная ссылка: --public-key, YADISK_PUBLIC_KEY или channel/queue.json")

    size = total_size(fresh_href(a.disk_path))
    log(f"size {size / 1e9:.2f} GB, threads {a.threads}")
    chunk = (size + a.threads - 1) // a.threads
    tag = os.path.splitext(os.path.basename(a.out))[0]
    parts = []
    state = {"done": 0, "errors": 0}
    threads = []
    for i in range(a.threads):
        s, e = i * chunk, min((i + 1) * chunk - 1, size - 1)
        p = os.path.join(TEMP_DIR, f"{tag}.part{i}")
        parts.append((p, s, e))
        if os.path.exists(p):
            state["done"] += os.path.getsize(p)
        t = threading.Thread(target=fetch_part, args=(a.disk_path, p, s, e, state), daemon=True)
        t.start()
        threads.append(t)

    last = state["done"]
    t0 = time.time()
    while any(t.is_alive() for t in threads):
        time.sleep(30)
        rate = (state["done"] - last) / 30 / 1e6
        last = state["done"]
        log(f"{state['done'] / 1e9:.2f}/{size / 1e9:.2f} GB, {rate:.1f} MB/s, errors {state['errors']}, "
            f"eta {((size - state['done']) / max(rate, 0.1) / 1e6) / 60:.0f} min")
    for t in threads:
        t.join()

    got = sum(os.path.getsize(p) for p, _, _ in parts)
    if got != size:
        raise SystemExit(f"parts sum {got} != {size}; rerun to resume")
    # Сборка на месте: части дописываются в первую и удаляются по одной, чтобы пиковое
    # потребление диска было «файл + одна часть», а не «файл × 2» (на 18 ГБ при 33 ГБ
    # свободных копирование в новый файл упало с ENOSPC).
    first = parts[0][0]
    with open(first, "ab") as out:
        for p, _, _ in parts[1:]:
            with open(p, "rb") as f:
                while True:
                    b = f.read(1 << 24)
                    if not b:
                        break
                    out.write(b)
            os.remove(p)
    os.replace(first, a.out)
    log(f"DONE {a.out} {os.path.getsize(a.out)} bytes in {(time.time() - t0) / 60:.0f} min")


if __name__ == "__main__":
    main()
