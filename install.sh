#!/usr/bin/env bash
# Установщик для macOS / Linux: ./install.sh  или  ./install.sh --auto
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python не найден. macOS: brew install python@3.12; Linux: пакет python3 (3.10+)."
  exit 1
fi
exec "$PY" install.py "$@"
