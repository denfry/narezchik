#!/usr/bin/env python
"""Запуск тяжёлых стадий пайплайна (транскрипция, рендер) на своём сервере вместо локальной машины.

Схема: локально → ssh/sftp → $NAREZCHIK_REMOTE_ROOT на сервере (зеркало рабочих папок
проекта) → задание в Docker-контейнере `mk-worker` с лимитами 3 CPU / 4 GiB и низким
приоритетом → результат скачивается обратно в те же папки проекта (transcripts/, output/,
edit-plans/). Настройка — docs/remote.md.

Переменные окружения:
    NAREZCHIK_SSH_HOST      алиас из ~/.ssh/config или hostname (обязательно)
    NAREZCHIK_REMOTE_ROOT   рабочая папка на сервере (по умолчанию /srv/mk)

Существующие scripts/pipeline/*.py не меняются — они запускаются на VDS как есть.

Usage (из корня проекта или откуда угодно):
    python scripts/remote/remote.py setup                       # один раз: образ + раннер на VDS
    python scripts/remote/remote.py transcribe input/podcast.mp4 --clip-id podcast [--model small]
    python scripts/remote/remote.py render edit-plans/podcast_short01.json
    python scripts/remote/remote.py run -- python scripts/pipeline/probe_video.py input/podcast.mp4
    python scripts/remote/remote.py status                      # очередь/задания на VDS
    python scripts/remote/remote.py logs <job_id> [-f]
    python scripts/remote/remote.py pull <job_id>               # забрать результат detach-задания

Любая команда с --detach возвращает управление сразу (задание живёт на VDS в фоне),
потом `pull <job_id>` дожидается и скачивает результат. Транскрипция на CPU VDS идёт
медленно (модель small на 3 CPU — порядка реального времени), так что для неё --detach
имеет смысл; при наличии NVIDIA GPU быстрее гонять transcribe.py локально.
"""
import argparse
import json
import os
import posixpath
import re
import secrets
import subprocess
import sys
import time

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REMOTE_ROOT = os.environ.get("NAREZCHIK_REMOTE_ROOT", "/srv/mk")
SSH_HOST = os.environ.get("NAREZCHIK_SSH_HOST", "")
CODE_DIRS = ["scripts/pipeline", "schemas", "assets/fonts"]
CODE_SKIP = ("__pycache__", ".pytest_cache", "tests", "models")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}][remote] {msg}", flush=True)


# ----------------------------------------------------------------------------- pure logic

def new_job_id(kind, name):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:40]
    return f"{kind}-{slug}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def plan_files(plan_rel, plan):
    """Файлы проекта (repo-relative), нужные рендеру этого edit-plan на VDS."""
    files = [plan_rel]
    src = plan.get("source", {}).get("path")
    if src and not src.startswith(("http://", "https://")):
        files.append(src)
    paths = [a.get("path") for a in plan.get("assets", [])]
    # врезка со своим звуком (CTA-зазывалка из cta/) — сегмент монтажа, а не asset: без неё рендер
    # на VDS падает на «внешний сегмент ссылается на отсутствующий файл»
    paths += [s.get("source_path") for s in plan.get("speech_edits", [])]
    # обложка и хвостовая плашка задаются путём в render_settings, а не через assets[] —
    # без них рендер на VDS молча соберётся без CTA-плашки в конце
    rs = plan.get("render_settings", {})
    # LUT задаётся именем (файл в assets/luts/, вне CODE_DIRS) или путём к .cube
    lut = rs.get("lut")
    if lut and str(lut).lower() != "none" and not os.path.isabs(lut):
        paths.append(lut if lut.lower().endswith(".cube") else f"assets/luts/{lut}.cube")
    for key in ("cover_frame", "tail_card"):
        cfg = rs.get(key) or {}
        if cfg.get("enabled"):
            paths.append(cfg.get("image_path"))
    for p in paths:
        if p and not p.startswith(("http://", "https://")) and p not in files:
            files.append(p)
    return files


def parse_md5sum(text):
    """Вывод md5sum («<hash>  <path>», в binary-режиме «<hash> *<path>») → {path: hash}."""
    out = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if len(line) < 35 or line[32] != " ":
            continue
        out[line[34:] if line[33] in " *" else line[33:]] = line[:32]
    return out


def code_upload_plan(local_hashes, remote_hashes):
    """Файлы кода, которые надо перезалить на VDS целиком: их нет на VDS или содержимое другое.

    Докачка хвоста по размеру (upload_mode 'append') годится только для многогигабайтных
    исходников. Изменённый .py обычно длиннее старого, и хвост нового файла дописывался к
    старому — так на сервере однажды получился render_short.py с SyntaxError.
    """
    return sorted(rel for rel, h in local_hashes.items() if remote_hashes.get(rel) != h)


def upload_mode(local_size, remote_size, force=False):
    """'skip' | 'append' (докачка хвоста) | 'write' | 'missing'.

    local_size=None — файла нет локально. Если он уже лежит на VDS (исходник скачан туда
    напрямую с Яндекс.Диска, а с локального диска удалён ради места), берём эту копию."""
    if local_size is None:
        return "skip" if remote_size else "missing"
    if remote_size == local_size and not force:
        return "skip"
    if remote_size and remote_size < local_size and not force:
        return "append"
    return "write"


def transcribe_command(wav_rel, clip_id, model, language):
    return ["python", "scripts/pipeline/transcribe.py", wav_rel, "--clip-id", clip_id,
            "--model", model, "--device", "cpu", "--language", language]


def render_command(plan_rel, keep_work):
    cmd = ["python", "scripts/pipeline/render_short.py", plan_rel]
    if keep_work:
        cmd.append("--keep-work")
    return cmd


def runner_env(cpus=None, mem=None, pids=None):
    """Префикс окружения для mk-job.sh — лимиты контейнера на одно задание.

    None — действуют лимиты mk-job.sh (3 CPU / 4 GiB). Поднимать осознанно: 4K-лонг с
    двумя десятками субклипов в одном графе в 4 GiB не влезает (ffmpeg убивается
    OOM-killer'ом в первые секунды прохода сборки)."""
    parts = []
    if cpus is not None:
        if not re.fullmatch(r"\d+(\.\d+)?", str(cpus)):
            raise ValueError(f"--cpus: ожидается число, получено {cpus!r}")
        parts.append(f"MK_CPUS={cpus}")
    if mem is not None:
        if not re.fullmatch(r"\d+[mg]", str(mem)):
            raise ValueError(f"--mem: ожидается вида 8g или 512m, получено {mem!r}")
        parts.append(f"MK_MEM={mem}")
    if pids is not None:
        # 4K-лонг на ~90 входов: у каждого scale свой пул потоков, 512 не хватает
        # (симптом — «Resource temporarily unavailable» на старте графа).
        if not re.fullmatch(r"\d+", str(pids)):
            raise ValueError(f"--pids: ожидается целое число, получено {pids!r}")
        parts.append(f"MK_PIDS={pids}")
    return "".join(p + " " for p in parts)


def _sh_quote(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


# ----------------------------------------------------------------------------- ssh/sftp

class Remote:
    def __init__(self, host=SSH_HOST):
        if not host:
            raise SystemExit("задайте NAREZCHIK_SSH_HOST (алиас из ~/.ssh/config) — см. docs/remote.md")
        import paramiko
        cfg = paramiko.SSHConfig()
        cfg_path = os.path.expanduser("~/.ssh/config")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg.parse(f)
        h = cfg.lookup(host)
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        keys = [os.path.expanduser(k) for k in h.get("identityfile", [])] or None
        self.client.connect(h.get("hostname", host), port=int(h.get("port", 22)),
                            username=h.get("user"), key_filename=keys, timeout=20)
        self.sftp = self.client.open_sftp()

    def sh(self, cmd, check=True):
        _, out, err = self.client.exec_command(cmd)
        o, e = out.read().decode(errors="replace"), err.read().decode(errors="replace")
        rc = out.channel.recv_exit_status()
        if check and rc != 0:
            raise RuntimeError(f"remote command failed ({rc}): {cmd}\n{e[-2000:]}")
        return rc, o, e

    def rpath(self, rel):
        return posixpath.join(REMOTE_ROOT, rel.replace("\\", "/"))

    def mkdirs(self, rdir):
        self.sh(f"mkdir -p {_sh_quote(rdir)}")

    def rsize(self, rpath):
        try:
            return self.sftp.stat(rpath).st_size
        except FileNotFoundError:
            return None

    def upload(self, rel, force=False):
        """Загрузка с докачкой: если на VDS файл того же размера — пропуск, если меньше —
        дописываем хвост (важно для многогигабайтных 4K-исходников)."""
        lp = os.path.join(ROOT, rel)
        rp = self.rpath(rel)
        size = os.path.getsize(lp) if os.path.isfile(lp) else None
        have = self.rsize(rp)
        action = upload_mode(size, have, force)
        if action == "missing":
            raise FileNotFoundError(f"{rel}: файла нет ни локально, ни на VDS")
        if action == "skip":
            if size is None:
                log(f"{rel}: локально нет, беру копию на VDS ({have / 1e6:.1f} MB)")
            return
        self.mkdirs(posixpath.dirname(rp))
        mode, offset = ("ab", have) if action == "append" else ("wb", 0)
        big = size > (8 << 20)
        if big:
            log(f"upload {rel} ({size / 1e6:.1f} MB{f', докачка с {offset / 1e6:.1f} MB' if offset else ''})")
        t0, done = time.time(), offset
        with open(lp, "rb") as f, self.sftp.open(rp, mode) as r:
            r.set_pipelined(True)
            f.seek(offset)
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                r.write(chunk)
                done += len(chunk)
                if big and (done % (64 << 20) < (1 << 20) or done == size):
                    rate = (done - offset) / max(time.time() - t0, 1e-6) / 1e6
                    print(f"\r  {done / size * 100:5.1f}%  {rate:.1f} MB/s", end="", flush=True)
        print()

    def download(self, rel):
        rp, lp = self.rpath(rel), os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        self.sftp.get(rp, lp)
        log(f"pulled {rel}")

    def upload_code(self):
        """Код сверяется по md5 и перезаливается целиком (см. code_upload_plan)."""
        import hashlib
        local = {}
        for d in CODE_DIRS:
            base = os.path.join(ROOT, d)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [x for x in dirnames if x not in CODE_SKIP]
                for fn in filenames:
                    if fn.endswith((".pyc", ".log")):
                        continue
                    full = os.path.join(dirpath, fn)
                    with open(full, "rb") as f:
                        local[os.path.relpath(full, ROOT).replace("\\", "/")] = hashlib.md5(f.read()).hexdigest()
        dirs = " ".join(_sh_quote(d) for d in CODE_DIRS)
        _, out, _ = self.sh(f"cd {REMOTE_ROOT} && find {dirs} -type f -exec md5sum {{}} + 2>/dev/null", check=False)
        todo = code_upload_plan(local, parse_md5sum(out))
        for rel in todo:
            self.upload(rel, force=True)
        if todo:
            log(f"код: перезалито {len(todo)} файл(ов)")

    # -- jobs
    def submit(self, kind, name, cmd, pulls, env=""):
        job = new_job_id(kind, name)
        jd = self.rpath(f"jobs/{job}")
        self.mkdirs(jd)
        with self.sftp.open(f"{jd}/pull.json", "w") as f:
            f.write(json.dumps(pulls))
        args = " ".join(_sh_quote(c) for c in cmd)
        self.sh(f"NAREZCHIK_REMOTE_ROOT={_sh_quote(REMOTE_ROOT)} {env}nohup setsid bash {REMOTE_ROOT}/bin/mk-job.sh {_sh_quote(job)} {args} >/dev/null 2>&1 &")
        log(f"job {job} поставлен в очередь")
        return job

    def job_exit(self, job):
        rc, o, _ = self.sh(f"cat {self.rpath(f'jobs/{job}/exit')} 2>/dev/null", check=False)
        return int(o.strip()) if rc == 0 and o.strip() else None

    def wait(self, job, poll=15):
        logp = self.rpath(f"jobs/{job}/log")
        shown = 0
        while True:
            _, o, _ = self.sh(f"tail -c +{shown + 1} {logp} 2>/dev/null", check=False)
            if o:
                sys.stdout.write(o)
                sys.stdout.flush()
                shown += len(o.encode())
            rc = self.job_exit(job)
            if rc is not None:
                return rc
            time.sleep(poll)

    def pull(self, job):
        rc = self.wait(job)
        with self.sftp.open(self.rpath(f"jobs/{job}/pull.json")) as f:
            pulls = json.load(f)
        if rc != 0:
            log(f"job {job} завершился с кодом {rc}, результат не скачиваю")
            return rc
        for rel in pulls:
            if self.rsize(self.rpath(rel)) is None:
                log(f"WARNING: на VDS нет ожидаемого файла {rel}")
                continue
            self.download(rel)
        return 0


# ----------------------------------------------------------------------------- commands

def cmd_setup(r, args):
    r.mkdirs(f"{REMOTE_ROOT}/bin")
    r.mkdirs(f"{REMOTE_ROOT}/jobs")
    for d in ("input", "transcripts", "edit-plans", "assets", "temp", "output", ".hf-cache"):
        r.mkdirs(r.rpath(d))
    r.sftp.put(os.path.join(HERE, "mk-job.sh"), f"{REMOTE_ROOT}/bin/mk-job.sh")
    r.sftp.put(os.path.join(HERE, "Dockerfile"), f"{REMOTE_ROOT}/bin/Dockerfile")
    r.sftp.put(os.path.join(HERE, "dl_yadisk.py"), f"{REMOTE_ROOT}/bin/dl_yadisk.py")
    r.sh(f"sed -i 's/\\r$//' {REMOTE_ROOT}/bin/mk-job.sh && chmod +x {REMOTE_ROOT}/bin/mk-job.sh")
    log("собираю образ mk-worker на VDS (первый раз несколько минут)")
    rc, o, e = r.sh(f"cd {REMOTE_ROOT}/bin && docker build -q -t mk-worker . 2>&1", check=False)
    if rc != 0:
        log(o[-3000:])
        return rc
    r.upload_code()
    log("готово: образ mk-worker собран, код пайплайна залит")
    return 0


def _finish(r, job, detach):
    if detach:
        log(f"detach: позже `python scripts/remote/remote.py pull {job}`")
        return 0
    return r.pull(job)


def cmd_transcribe(r, args):
    env = runner_env(args.cpus, args.mem, args.pids)
    src = args.input if os.path.isabs(args.input) else os.path.join(ROOT, args.input)
    wav_rel = f"temp/{args.clip_id}_audio.wav"
    wav = os.path.join(ROOT, wav_rel)
    if not (os.path.exists(wav) and os.path.getsize(wav) > 0):
        log("извлекаю аудио локально (16 kHz mono) — на VDS уедет ~115 MB/час вместо всего видео")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", wav], check=True)
    r.upload_code()
    r.upload(wav_rel)
    job = r.submit("transcribe", args.clip_id,
                   transcribe_command(wav_rel, args.clip_id, args.model, args.language),
                   [f"transcripts/{args.clip_id}.json", f"transcripts/{args.clip_id}.txt"], env=env)
    return _finish(r, job, args.detach)


def cmd_render(r, args):
    env = runner_env(args.cpus, args.mem, args.pids)
    plan_rel = os.path.relpath(os.path.abspath(args.edit_plan), ROOT).replace("\\", "/")
    with open(os.path.join(ROOT, plan_rel), encoding="utf-8") as f:
        plan = json.load(f)
    clip_id = plan.get("clip_id") or os.path.splitext(os.path.basename(plan_rel))[0]
    r.upload_code()
    for rel in plan_files(plan_rel, plan):
        r.upload(rel, force=(rel == plan_rel))
    job = r.submit("render", clip_id, render_command(plan_rel, args.keep_work),
                   [f"output/{clip_id}.mp4", plan_rel], env=env)
    return _finish(r, job, args.detach)


def cmd_run(r, args):
    env = runner_env(args.cpus, args.mem, args.pids)
    if not args.argv:
        log("нужна команда после --")
        return 2
    r.upload_code()
    job = r.submit("run", args.argv[-1].split("/")[-1], args.argv, args.pull or [], env=env)
    return _finish(r, job, args.detach)


def cmd_status(r, args):
    _, o, _ = r.sh(
        f"uptime; echo; docker ps --filter name=mk- --format '  running: {{{{.Names}}}} {{{{.RunningFor}}}}'; echo; "
        f"cd {REMOTE_ROOT}/jobs 2>/dev/null && for j in $(ls -t | head -15); do "
        f"if [ -f $j/exit ]; then s=\"exit $(cat $j/exit)\"; elif [ -f $j/started ]; then s=running; else s=queued; fi; "
        f"echo \"  $j  $s\"; done", check=False)
    print(o)
    return 0


def cmd_logs(r, args):
    if args.follow:
        return r.wait(args.job)
    _, o, _ = r.sh(f"tail -n 100 {r.rpath(f'jobs/{args.job}/log')}", check=False)
    print(o)
    return 0


def cmd_pull(r, args):
    return r.pull(args.job)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="command", required=True)
    sp.add_parser("setup")
    p = sp.add_parser("transcribe")
    p.add_argument("input")
    p.add_argument("--clip-id", required=True)
    p.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large-v3"],
                   help="на CPU VDS medium в ~3 раза медленнее small; по умолчанию small")
    p.add_argument("--language", default="ru")
    p.add_argument("--detach", action="store_true")
    p.add_argument("--cpus", help="лимит CPU контейнера на это задание (по умолчанию 3)")
    p.add_argument("--mem", help="лимит памяти контейнера на это задание, например 8g (по умолчанию 4g)")
    p.add_argument("--pids", help="лимит потоков/процессов контейнера (по умолчанию 512)")
    p = sp.add_parser("render")
    p.add_argument("edit_plan")
    p.add_argument("--keep-work", action="store_true")
    p.add_argument("--detach", action="store_true")
    p.add_argument("--cpus", help="лимит CPU контейнера на это задание (по умолчанию 3)")
    p.add_argument("--mem", help="лимит памяти контейнера на это задание, например 8g (по умолчанию 4g)")
    p.add_argument("--pids", help="лимит потоков/процессов контейнера (по умолчанию 512)")
    p = sp.add_parser("run")
    p.add_argument("--pull", action="append", help="repo-relative файл, который забрать после")
    p.add_argument("--detach", action="store_true")
    p.add_argument("--cpus", help="лимит CPU контейнера на это задание (по умолчанию 3)")
    p.add_argument("--mem", help="лимит памяти контейнера на это задание, например 8g (по умолчанию 4g)")
    p.add_argument("--pids", help="лимит потоков/процессов контейнера (по умолчанию 512)")
    p.add_argument("argv", nargs=argparse.REMAINDER)
    sp.add_parser("status")
    p = sp.add_parser("logs")
    p.add_argument("job")
    p.add_argument("-f", "--follow", action="store_true")
    p = sp.add_parser("pull")
    p.add_argument("job")
    args = ap.parse_args()
    if getattr(args, "argv", None) and args.argv[:1] == ["--"]:
        args.argv = args.argv[1:]
    r = Remote()
    fn = {"setup": cmd_setup, "transcribe": cmd_transcribe, "render": cmd_render, "run": cmd_run,
          "status": cmd_status, "logs": cmd_logs, "pull": cmd_pull}[args.command]
    sys.exit(fn(r, args) or 0)


if __name__ == "__main__":
    main()
