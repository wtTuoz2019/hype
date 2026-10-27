"""Telegram Login Widget 回调校验。机器人 token 只留在服务端。"""

import hashlib
import hmac
import time

from django.conf import settings


def telegram_ok(query, token: str | None = None) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN if token is None else token
    if not token:
        return False
    received = str(query.get("hash") or "")
    if not received:
        return False
    pairs = [f"{key}={query.get(key)}" for key in sorted(query) if key != "hash"]
    secret = hashlib.sha256(token.encode()).digest()
    digest = hmac.new(secret, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, received):
        return False
    try:
        auth_date = int(query.get("auth_date") or 0)
    except (TypeError, ValueError):
        return False
    if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
        return False
    return bool(str(query.get("id") or "").isdigit())
