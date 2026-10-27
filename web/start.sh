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

if [ -f data/monitor.pid ]; then
  old=$(cat data/monitor.pid || true)
  if [ -n "${old}" ] && kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null || true
    sleep 1
  fi
  rm -f data/monitor.pid
fi

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
