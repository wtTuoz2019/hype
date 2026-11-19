#!/usr/bin/env bash
# 定时同步 franklabs → 只覆盖 latest.json（不再每 5 分钟堆 events_*.json）
#
# 宝塔计划任务（建议每 5 分钟）:
#   /bin/bash /www/wwwroot/hype/tools/franklabs_sync.sh >>/www/wwwroot/hype/tools/data/franklabs_trader_live/cron.log 2>&1
#
# 服务器凭证（二选一，勿进 git）:
#   1) /www/wwwroot/hype/tools/franklabs.env
#   2) /www/wwwroot/hype/web/.env 增加 FRANKLABS_EMAIL / FRANKLABS_PASSWORD
set -euo pipefail
cd "$(dirname "$0")"
OUT_DIR="data/franklabs_trader_live"
mkdir -p "$OUT_DIR"
export PYTHONUNBUFFERED=1
if [ -x "../web/.venv/bin/python" ]; then
  PY="../web/.venv/bin/python"
else
  PY="python3"
fi

# cron.log 超过 2MB 就截成末尾 2000 行，避免无限涨
LOG="$OUT_DIR/cron.log"
if [ -f "$LOG" ]; then
  size=$(wc -c <"$LOG" | tr -d ' ')
  if [ "${size:-0}" -gt 2097152 ]; then
    tail -n 2000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
fi

# 顺手清掉历史 events_*.json（旧版本留下的）
rm -f "$OUT_DIR"/events_*.json

# keep_snaps=0：只写 latest.json + meta.json
exec "$PY" franklabs_trader_live.py --sync --traders all --keep-snaps 0
