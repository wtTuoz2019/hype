#!/usr/bin/env bash
# 服务器上更新：cd /www/wwwroot/hype/web && ./deploy.sh
# 拉代码 → 迁移 → 静态文件 → 热加载网页 → 重启监控（保证只留一个）
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

# 网页：优先 HUP 热加载
if [ -f data/gunicorn.pid ]; then
  pid=$(cat data/gunicorn.pid || true)
  if [ -n "${pid}" ] && kill -0 "$pid" 2>/dev/null; then
    kill -HUP "$pid"
    echo "gunicorn 已热加载 (HUP $pid)"
  else
    echo "gunicorn pid 无效：请在宝塔重启一次 Python 项目（启动命令用 start.sh）"
  fi
else
  echo "没有 gunicorn.pid：请在宝塔重启一次 Python 项目（启动命令用 start.sh）"
fi

# 监控：按名字全清，避免 deploy + 宝塔重启叠出多个
mkdir -p data
pkill -f 'manage.py run_monitor' 2>/dev/null || true
sleep 1
rm -f data/monitor.pid
./.venv/bin/python manage.py run_monitor >>data/monitor.log 2>&1 &
echo $! > data/monitor.pid
chown www:www data/monitor.pid 2>/dev/null || true
echo "监控已重启 (pid $(cat data/monitor.pid)，应只剩这一个)"
echo "更新完成"
echo
echo "自检："
echo "  pgrep -af run_monitor"
echo "  curl -s -H 'Host: p.c7.pro' http://127.0.0.1:8766/api/tape/ | head -c 120"
