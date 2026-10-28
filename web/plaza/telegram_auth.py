"""Telegram Login Widget 回调校验。机器人 token 只留在服务端。"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Profile, TelegramAuth
from .tape import adopt_session


def telegram_ok(query: Mapping, token: str | None = None) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN if token is None else token
    if not token:
        return False
    data = {str(key): "" if query.get(key) is None else str(query.get(key)) for key in query}
    received = data.pop("hash", "")
    if not received:
        return False
    pairs = [f"{key}={data[key]}" for key in sorted(data)]
    secret = hashlib.sha256(token.encode()).digest()
    digest = hmac.new(secret, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, received):
        return False
    try:
        auth_date = int(data.get("auth_date") or 0)
    except (TypeError, ValueError):
        return False
    if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
        return False
    return bool(str(data.get("id") or "").isdigit())


def complete_telegram_login(request, payload: Mapping) -> tuple[Profile | None, str]:
    """校验并完成 Telegram 登录。成功返回 (profile, "")，失败返回 (None, 原因)。"""
    if not settings.TELEGRAM_BOT_TOKEN:
        return None, "还没有配置 Telegram 机器人。"
    data = {str(key): "" if payload.get(key) is None else str(payload.get(key)) for key in payload}
    if not telegram_ok(data):
        return None, "Telegram 登录校验没有通过。请关闭后重新点登录。"
    digest = data.get("hash") or ""
    if not digest:
        return None, "缺少 Telegram 签名。"
    if TelegramAuth.objects.filter(digest=digest).exists():
        return None, "这条授权已经用过。请返回重新点一次 Telegram 登录。"

    telegram_id = int(data["id"])
    now = timezone.now()
    first = (data.get("first_name") or "").strip()[:80]
    last = (data.get("last_name") or "").strip()[:80]
    username = (data.get("username") or "").strip().lstrip("@")[:64]
    photo = (data.get("photo_url") or "").strip()[:300]
    auth_raw = data.get("auth_date") or ""
    auth_date = int(auth_raw) if str(auth_raw).isdigit() else None

    try:
        with transaction.atomic():
            profile = (
                Profile.objects.select_related("user").filter(telegram_id=telegram_id).first()
            )
            if profile is None:
                user = User.objects.filter(username=f"tg_{telegram_id}").first()
                if user is None:
                    user = User.objects.create_user(username=f"tg_{telegram_id}")
                profile, created = Profile.objects.get_or_create(
                    user=user,
                    defaults={
                        "telegram_id": telegram_id,
                        "telegram_first_login_at": now,
                    },
                )
                if profile.telegram_id is None:
                    profile.telegram_id = telegram_id
                if profile.telegram_first_login_at is None:
                    profile.telegram_first_login_at = now
            else:
                user = profile.user
                if profile.telegram_first_login_at is None:
                    profile.telegram_first_login_at = now

            if first:
                profile.telegram_first_name = first
            if last or first:
                profile.telegram_last_name = last
            display = f"{profile.telegram_first_name} {profile.telegram_last_name}".strip()
            if display:
                profile.telegram_name = display[:80]
            if username:
                profile.telegram_username = username
            if photo:
                profile.telegram_photo = photo
            if auth_date:
                profile.telegram_auth_date = auth_date
            profile.telegram_last_login_at = now
            profile.telegram_login_count = int(profile.telegram_login_count or 0) + 1
            profile.save()
            TelegramAuth.objects.create(digest=digest)
    except IntegrityError:
        return None, "登录建档冲突，请再试一次。"

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    adopt_session(request)
    return profile, ""
