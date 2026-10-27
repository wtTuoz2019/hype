#!/usr/bin/env bash
# 宝塔启动命令填：/www/wwwroot/hype/web/start.sh
set -euo pipefail
cd "$(dirname "$0")"
set -a
# shellcheck disable=SC1091
[ -f .env ] && source .env
set +a
# 网页进程不内嵌监控
unset HYPE_MONITOR

mkdir -p data
./.venv/bin/python manage.py collectstatic --noinput >/dev/null
./.venv/bin/python manage.py migrate --noinput >/dev/null

# 监控是后台进程，宝塔停 gunicorn 时不会一起杀掉；每次启动先清干净
pkill -f 'manage.py run_monitor' 2>/dev/null || true
if [ -f data/monitor.pid ]; then
  old=$(cat data/monitor.pid || true)
  if [ -n "${old}" ]; then
    kill "$old" 2>/dev/null || true
  fi
  rm -f data/monitor.pid
fi
sleep 1

# 旧网页占着 8766 时，新 gunicorn 起不来，宝塔却以为重启成功
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8766/tcp 2>/dev/null || true
else
  pkill -f 'gunicorn config.wsgi' 2>/dev/null || true
fi
if [ -f data/gunicorn.pid ]; then
  gpid=$(cat data/gunicorn.pid || true)
  if [ -n "${gpid}" ]; then
    kill "$gpid" 2>/dev/null || true
  fi
  rm -f data/gunicorn.pid
fi
sleep 1

./.venv/bin/python manage.py run_monitor >>data/monitor.log 2>&1 &
echo $! > data/monitor.pid

# 默认热加载：改了 .py 自动换工人。设 HYPE_RELOAD=0 可关掉。
EXTRA=()
if [ "${HYPE_RELOAD:-1}" != "0" ]; then
  EXTRA+=(--reload)
fi

exec ./.venv/bin/python -m gunicorn config.wsgi:application \
  --bind 127.0.0.1:8766 \
  --pid data/gunicorn.pid \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  "${EXTRA[@]}"
