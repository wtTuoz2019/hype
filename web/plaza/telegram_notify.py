"""把新开单快讯推到 Telegram 群。

配置（.env）：
  TELEGRAM_BOT_TOKEN=...          # 已有登录机器人即可复用
  TELEGRAM_NOTIFY_CHAT_ID=-100…   # 目标群/频道 ID（负数）
  TELEGRAM_SITE_URL=https://p.c7.pro
  TELEGRAM_NOTIFY_LABELS=         # 可选，逗号分隔；空=全部动作都推

建群步骤：
  1. 把机器人拉进群，并给「发消息」权限
  2. 在群里随便发一条消息后，访问
     https://api.telegram.org/bot<TOKEN>/getUpdates
     从 chat.id 抄到 TELEGRAM_NOTIFY_CHAT_ID
  3. ./deploy.sh 或重启监控进程
  4. python manage.py telegram_ping 试发一条
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


def _enabled() -> bool:
    return bool((settings.TELEGRAM_BOT_TOKEN or "").strip() and (settings.TELEGRAM_NOTIFY_CHAT_ID or "").strip())


def _wanted_label(label: str) -> bool:
    raw = (getattr(settings, "TELEGRAM_NOTIFY_LABELS", "") or "").strip()
    if not raw:
        return True
    allow = {part.strip() for part in raw.split(",") if part.strip()}
    return str(label or "") in allow


def _post(method: str, payload: dict) -> dict:
    token = settings.TELEGRAM_BOT_TOKEN.strip()
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "alphascope-notify"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "telegram api failed")
    return data


def format_signal_message(row) -> str:
    name = (row.leader.name or "").strip() or (row.leader.address[:8] + "…" + row.leader.address[-4:])
    coin = row.coin or "—"
    label = row.label or row.action or "—"
    px = row.px or "—"
    site = (getattr(settings, "TELEGRAM_SITE_URL", "") or "https://p.c7.pro").rstrip("/")
    lines = [
        "🔔 开单快讯",
        f"{name}",
        f"{coin}  ·  {label}  ·  均价 {px}",
    ]
    if row.delta_szi:
        lines.append(f"仓位变化 {row.prev_szi or '?'} → {row.szi or '?'}（Δ{row.delta_szi}）")
    lines.append(f"{site}/?wallet={row.leader_id}")
    return "\n".join(lines)


def send_text(text: str, *, chat_id: str | None = None) -> None:
    if not _enabled() and not chat_id:
        raise RuntimeError("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_NOTIFY_CHAT_ID")
    target = (chat_id or settings.TELEGRAM_NOTIFY_CHAT_ID or "").strip()
    _post(
        "sendMessage",
        {
            "chat_id": target,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


def notify_trade_signal(signal_id: int) -> None:
    """默认异步推送，不拖慢 WebSocket 写库。测试可设 TELEGRAM_NOTIFY_SYNC=True。"""
    if not _enabled():
        return

    def _run() -> None:
        from django.db import close_old_connections

        close_old_connections()
        try:
            from .models import TradeSignal

            row = TradeSignal.objects.select_related("leader").filter(pk=signal_id).first()
            if row is None or not row.leader.enabled:
                return
            if not _wanted_label(row.label):
                return
            send_text(format_signal_message(row))
        except Exception as exc:  # noqa: BLE001
            print(f"Telegram 群通知失败: {exc}", flush=True)
        finally:
            close_old_connections()

    if getattr(settings, "TELEGRAM_NOTIFY_SYNC", False):
        _run()
    else:
        threading.Thread(target=_run, name=f"tg-notify-{signal_id}", daemon=True).start()
