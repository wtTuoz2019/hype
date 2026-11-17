#!/usr/bin/env bash
# 定时同步 franklabs 全部交易员开单 → tools/data/franklabs_trader_live/latest.json
#
# 宝塔计划任务（建议每 5 分钟）:
#   /bin/bash /www/wwwroot/hype/tools/franklabs_sync.sh >>/www/wwwroot/hype/tools/data/franklabs_trader_live/cron.log 2>&1
#
# 服务器凭证（二选一，勿进 git）:
#   1) /www/wwwroot/hype/tools/franklabs.env
#   2) /www/wwwroot/hype/web/.env 增加 FRANKLABS_EMAIL / FRANKLABS_PASSWORD
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/franklabs_trader_live
export PYTHONUNBUFFERED=1
if [ -x "../web/.venv/bin/python" ]; then
  PY="../web/.venv/bin/python"
else
  PY="python3"
fi
exec "$PY" franklabs_trader_live.py --sync --traders all --no-snapshots
