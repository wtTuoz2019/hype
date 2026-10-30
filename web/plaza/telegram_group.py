"""用 getChatMember 校验用户是否在 Pro 资格群里。

机器人必须已在该群（建议给管理员），否则查不到成员。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings

# left / kicked 不算在群；restricted 仍在群内（只是被限制发言）
_IN_GROUP = frozenset({"creator", "administrator", "member", "restricted"})


def pro_chat_id() -> str:
    return (
        (getattr(settings, "TELEGRAM_PRO_CHAT_ID", "") or "").strip()
        or (settings.TELEGRAM_NOTIFY_CHAT_ID or "").strip()
    )


def pro_invite_url() -> str:
    url = (getattr(settings, "TELEGRAM_PRO_INVITE_URL", "") or "").strip()
    if url:
        return url
    chat = pro_chat_id()
    # 公开群可用 @username；私密群必须配 TELEGRAM_PRO_INVITE_URL
    if chat.startswith("@"):
        return f"https://t.me/{chat.lstrip('@')}"
    return ""


def _post(method: str, payload: dict) -> dict:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        raise RuntimeError("未配置 TELEGRAM_BOT_TOKEN")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "alphascope-pro"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "telegram api failed")
    return data


def chat_member_status(telegram_user_id: int, chat_id: str | None = None) -> str:
    """返回 Telegram status 字符串；查不到时抛错。"""
    target = (chat_id or pro_chat_id()).strip()
    if not target:
        raise RuntimeError("未配置 TELEGRAM_PRO_CHAT_ID / TELEGRAM_NOTIFY_CHAT_ID")
    data = _post(
        "getChatMember",
        {"chat_id": int(target) if target.lstrip("-").isdigit() else target, "user_id": int(telegram_user_id)},
    )
    result = data.get("result") or {}
    return str(result.get("status") or "")


def user_in_pro_group(telegram_user_id: int) -> tuple[bool, str]:
    """(是否在群, status 或错误说明)。"""
    try:
        status = chat_member_status(telegram_user_id)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}").get("description") or str(exc)
        except Exception:  # noqa: BLE001
            detail = str(exc)
        return False, detail
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return status in _IN_GROUP, status
