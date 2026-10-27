#!/usr/bin/env bash
# 服务器上更新：cd /www/wwwroot/hype/web && ./deploy.sh
# 拉代码 → 迁移 → 静态文件 → 热加载网页 → 重启监控
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
set -a
# shellcheck disable=SC1091
[ -f .env ] && source .env
set +a
unset HYPE_MONITOR

cd "$ROOT"
git pull
cd "$ROOT/web"

./.venv/bin/python manage.py migrate --noinput
./.venv/bin/python manage.py collectstatic --noinput
chown -R www:www staticfiles data 2>/dev/null || true

# 网页：优先 HUP 热加载；没有 pid 则依赖 gunicorn --reload 自己感知
if [ -f data/gunicorn.pid ]; then
  pid=$(cat data/gunicorn.pid || true)
  if [ -n "${pid}" ] && kill -0 "$pid" 2>/dev/null; then
    kill -HUP "$pid"
    echo "gunicorn 已热加载 (HUP $pid)"
  else
    echo "gunicorn pid 无效，若已开 --reload，保存/拉代码后会自行加载"
  fi
else
  echo "没有 gunicorn.pid：请确认宝塔启动命令是 start.sh，或等 --reload 生效"
fi

# 监控进程不支持 HUP，拉代码后必须换新进程
mkdir -p data
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
chown www:www data/monitor.pid 2>/dev/null || true
echo "监控已重启 (pid $(cat data/monitor.pid))"
echo "更新完成"
