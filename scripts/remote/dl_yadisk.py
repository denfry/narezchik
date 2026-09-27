#!/usr/bin/env python
"""Скачать файл из публичной папки Яндекс.Диска прямо на сервер, минуя локальный диск.

4K-исходники весят десятки гигабайт, а локально места может не быть. Скрипт запускается
на сервере внутри образа mk-worker (python там уже есть), в фоне:

    ssh <host> 'docker run -d --rm --name mk-dl -e YADISK_PUBLIC_KEY=https://disk.yandex.ru/d/XXXX         -v /srv/mk:/work -w /work mk-worker         python bin/dl_yadisk.py "/Папка/X.mp4" "input/X.mp4" <размер в байтах> temp/dl_X.log'

Прогресс — `tail /srv/mk/temp/dl_X.log`, в конце строка `DL_DONE <байт>` или `DL_FAIL`.
Размер — `source.file_size_bytes` из плана или поле `size` ответа API resources.
Докачка по Range; при обрыве прямая ссылка берётся заново (href живёт ограниченное время).
Скорость ограничена 60 МБ/с, чтобы не забивать диск сервера, если на нём живут другие
сервисы (без лимита Яндекс отдаёт ~120 МБ/с).
"""
import json, os, sys, time, urllib.parse, urllib.request
KEY = os.environ["YADISK_PUBLIC_KEY"]  # публичная ссылка на папку Диска
CAP = 60e6  # байт/с — не забивать диск сервера
dpath, dest, size, logp = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
def log(m):
    with open(logp, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {m}\n")
os.makedirs(os.path.dirname(dest), exist_ok=True)
for attempt in range(1, 9):
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    if have == size:
        break
    try:
        q = urllib.parse.urlencode({"public_key": KEY, "path": dpath})
        href = json.load(urllib.request.urlopen(
            "https://cloud-api.yandex.net/v1/disk/public/resources/download?" + q, timeout=30))["href"]
        req = urllib.request.Request(href, headers={"Range": f"bytes={have}-"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "ab") as f:
            if r.status != 206:
                raise RuntimeError(f"range not honoured, status {r.status}")
            t0, done, last = time.time(), 0, 0.0
            while True:
                b = r.read(4 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                el = time.time() - t0
                if done / max(el, 1e-6) > CAP:
                    time.sleep(done / CAP - el)
                if time.time() - last > 30:
                    last = time.time()
                    log(f"{(have + done) / size * 100:5.1f}%  {done / 1e6 / (last - t0):.0f} MB/s")
    except Exception as e:
        log(f"attempt {attempt} error: {e}")
        time.sleep(10)
have = os.path.getsize(dest) if os.path.exists(dest) else 0
log(f"DL_DONE {have}" if have == size else f"DL_FAIL {have}/{size}")
