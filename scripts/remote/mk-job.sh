#!/usr/bin/env bash
# Раннер задания на VDS. Вызывается remote.py: mk-job.sh <job_id> <cmd...>
# Одно тяжёлое задание за раз (flock), контейнер ограничен 3 CPU / 4 GiB и низким
# cpu-shares, чтобы остальные сервисы сервера (сайты, базы) всегда были в приоритете.
set -u
ROOT="${NAREZCHIK_REMOTE_ROOT:-/srv/mk}"
JOB="$1"; shift
JD="$ROOT/jobs/$JOB"
mkdir -p "$JD"
CPUS="${MK_CPUS:-3}"; MEM="${MK_MEM:-4g}"; PIDS="${MK_PIDS:-512}"
{
  echo "[mk-job] queued $(date -Is) cmd: $*"
  flock -x 9
  echo "[mk-job] started $(date -Is)"
  date -Is > "$JD/started"
  nice -n 19 ionice -c 3 docker run --rm --name "mk-$JOB" \
    --cpus="$CPUS" --memory="$MEM" --memory-swap="$MEM" --cpu-shares=128 --pids-limit="$PIDS" \
    -v "$ROOT:/work" -w /work mk-worker "$@"
  rc=$?
  echo "[mk-job] finished $(date -Is) rc=$rc"
  echo "$rc" > "$JD/exit"
} 9>"$ROOT/queue.lock" >>"$JD/log" 2>&1
