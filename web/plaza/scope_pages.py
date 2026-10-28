"""直接使用 alphascope-simple 里的页面，只换成站内地址和当前登录状态。"""

import json

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.templatetags.static import static
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import wallet_auth
from .models import Profile, TelegramAuth
from .tape import (
    FREE_DELAY,
    PRO_DELAY,
    adopt_session,
    event_dict,
    front_profile,
    is_pro,
    set_pro,
    subscriptions,
    today_stats,
    toggle_sub,
    visible_events,
    wallet_cards,
    wallet_detail,
    wallet_rev,
)
from .telegram_auth import telegram_ok

PAGES = {
    "home": "index.html",
    "board": "leaderboard.html",
    "analysis": "analysis.html",
    "pricing": "pricing.html",
    "wallet": "wallet-detail.html",
}
LINKS = (
    ("wallet-detail.html", "/wallet/"),
    ("leaderboard.html", "/leaderboard/"),
    ("analysis.html", "/analysis/"),
    ("pricing.html", "/pricing/"),
    ("index.html", "/"),
    ('href="style.css"', f'href="{static("style.css")}"'),
    ('src="telegram-auth.js"', f'src="{static("telegram-auth.js")}"'),
    ('src="app.js"', f'src="{static("app.js")}"'),
)


def _user(request) -> dict | None:
    """前台会员才显示已登录；后台 admin 不算。"""
    profile = front_profile(request)
    if profile is None:
        return None
    if profile.telegram_username:
        label = f"@{profile.telegram_username}"
    elif profile.telegram_name:
        label = profile.telegram_name
    elif profile.wallet_address:
        label = wallet_auth.short_address(profile.wallet_address)
    else:
        label = request.user.get_username()
    return {"label": label, "photo": profile.telegram_photo or ""}


def boot(request) -> dict:
    rows = visible_events(request)
    pro = is_pro(request)
    return {
        "pro": pro,
        "subs": subscriptions(request),
        "user": _user(request),
        "bot": settings.TELEGRAM_BOT_USERNAME,
        "authUrl": request.build_absolute_uri("/auth/telegram/callback"),
        "wallet_rev": wallet_rev(),
        "wallets": wallet_cards(),
        "events": [event_dict(row) for row in rows],
        "today": today_stats(request),
        "delay_sec": PRO_DELAY if pro else FREE_DELAY,
        "poll_sec": 3,
    }


_HTML_SHELL: dict[str, str] = {}


def _scope_html(name: str) -> str:
    cached = _HTML_SHELL.get(name)
    if cached is not None:
        return cached
    raw = (settings.HYPE_ROOT / "alphascope-simple" / PAGES[name]).read_text(encoding="utf-8")
    for old, new in LINKS:
        raw = raw.replace(old, new)
    _HTML_SHELL[name] = raw
    return raw


@ensure_csrf_cookie
def render_scope(request, name: str) -> HttpResponse:
    raw = _scope_html(name)
    # 会员页不需要塞全量钱包；详情页首屏只要瘦列表，厚数据走 /api/wallet/
    if name == "pricing":
        data = boot(request)
        data["wallets"] = []
        data["events"] = []
        payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    else:
        payload = json.dumps(boot(request), ensure_ascii=False).replace("<", "\\u003c")
    script = f'<script>window.ALPHASCOPE={payload}</script>'
    marker = f'<script src="{static("telegram-auth.js")}"></script>'
    return HttpResponse(raw.replace(marker, script + marker, 1))


def _body(request) -> dict:
    try:
        data = json.loads(request.body.decode() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def tape_feed(request):
    after_raw = request.GET.get("after") or ""
    after_id = None
    if after_raw.isdigit():
        after_id = int(after_raw)
    want_wallets = request.GET.get("wallets") == "1"
    skip_events = request.GET.get("events") == "0"
    rev = wallet_rev()
    payload = {
        "pro": is_pro(request),
        "subs": subscriptions(request),
        "today": today_stats(request),
        "wallet_rev": rev,
        "incremental": False,
        "events": [],
    }
    if not skip_events:
        if after_id is not None:
            rows = visible_events(request, after_id=after_id, limit=40)
            payload["incremental"] = True
        else:
            rows = visible_events(request)
        payload["events"] = [event_dict(row) for row in rows]
    # 钱包只在明确要求时返回，避免 3 秒轮询拖大包
    if want_wallets:
        payload["wallets"] = wallet_cards()
    return JsonResponse(payload)


@require_GET
def wallet_api(request):
    card = wallet_detail(request.GET.get("id") or "")
    if card is None:
        return JsonResponse({"message": "没有这个监控钱包"}, status=404)
    return JsonResponse({"wallet": card, "pro": is_pro(request), "subs": subscriptions(request)})


@require_POST
def tape_test(request):
    if not is_pro(request):
        return JsonResponse({"message": "免费用户不会即时收到新开单推送。升级 Pro 后，新信号大约晚 1 分钟播报。"})
    rows = visible_events(request)
    if not rows:
        return JsonResponse({"message": "还没有可播报的真实开单。后台记到新信号后会出现在这里。"})
    return JsonResponse({"event": event_dict(rows[0]), "pro": True, "subs": subscriptions(request)})


@require_POST
def membership(request):
    set_pro(request, bool(_body(request).get("pro")))
    return JsonResponse({"pro": is_pro(request), "subs": subscriptions(request)})


@require_POST
def subscribe(request):
    if not is_pro(request):
        return JsonResponse({"pro": False, "need": "alert"}, status=403)
    code = str(_body(request).get("id") or "")
    return JsonResponse({"pro": True, "subs": toggle_sub(request, code)})


@require_GET
def telegram_callback(request):
    from django.utils import timezone

    if not settings.TELEGRAM_BOT_TOKEN:
        return HttpResponse("还没有配置 Telegram 机器人。", status=503)
    if not telegram_ok(request.GET):
        return HttpResponse("Telegram 登录校验没有通过。", status=403)
    digest = request.GET.get("hash") or ""
    if TelegramAuth.objects.filter(digest=digest).exists():
        return HttpResponse("这条 Telegram 登录已经用过。", status=403)
    TelegramAuth.objects.create(digest=digest)
    telegram_id = int(request.GET.get("id"))
    profile = Profile.objects.filter(telegram_id=telegram_id).select_related("user").first()
    now = timezone.now()
    if profile is None:
        user = User.objects.create_user(username=f"tg_{telegram_id}")
        profile = Profile.objects.create(
            user=user,
            telegram_id=telegram_id,
            telegram_first_login_at=now,
        )
    else:
        user = profile.user
        if profile.telegram_first_login_at is None:
            profile.telegram_first_login_at = now

    first = (request.GET.get("first_name") or "").strip()[:80]
    last = (request.GET.get("last_name") or "").strip()[:80]
    username = (request.GET.get("username") or "").strip().lstrip("@")[:64]
    photo = (request.GET.get("photo_url") or "").strip()[:300]
    auth_raw = request.GET.get("auth_date") or ""
    auth_date = int(auth_raw) if str(auth_raw).isdigit() else None

    # 有值才覆盖，避免用户隐藏用户名/头像后把库里旧资料冲掉
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
    profile.save(
        update_fields=[
            "telegram_first_name",
            "telegram_last_name",
            "telegram_name",
            "telegram_username",
            "telegram_photo",
            "telegram_auth_date",
            "telegram_first_login_at",
            "telegram_last_login_at",
            "telegram_login_count",
        ]
    )
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    adopt_session(request)
    return redirect("home")


@require_POST
def wallet_nonce(request):
    from django.utils import timezone

    raw = str(_body(request).get("address") or "")
    try:
        address = wallet_auth.norm_address(raw)
    except ValueError:
        return JsonResponse({"message": "钱包地址无效"}, status=400)
    row = wallet_auth.issue_nonce(address)
    host = request.get_host().split(":")[0]
    issued_at = timezone.now().isoformat(timespec="seconds")
    message = wallet_auth.build_login_message(
        host=host, address=address, nonce=row.nonce, issued_at=issued_at
    )
    return JsonResponse(
        {
            "address": address,
            "nonce": row.nonce,
            "message": message,
            "expires_at": row.expires_at.isoformat(timespec="seconds"),
        }
    )


@require_POST
def wallet_verify(request):
    from django.utils import timezone

    body = _body(request)
    raw_address = str(body.get("address") or "")
    nonce = str(body.get("nonce") or "").strip()
    signature = str(body.get("signature") or "").strip()
    message = str(body.get("message") or "")
    try:
        address = wallet_auth.norm_address(raw_address)
    except ValueError:
        return JsonResponse({"message": "钱包地址无效"}, status=400)
    if not nonce or not signature or not message:
        return JsonResponse({"message": "缺少签名参数"}, status=400)
    if address not in message or nonce not in message:
        return JsonResponse({"message": "签名消息不匹配"}, status=400)
    if "AlphaScope 登录" not in message:
        return JsonResponse({"message": "签名消息不匹配"}, status=400)
    host = request.get_host().split(":")[0]
    if f"域名: {host}" not in message:
        return JsonResponse({"message": "登录域名不匹配"}, status=400)
    try:
        recovered = wallet_auth.recover_address(message, signature)
    except Exception:
        return JsonResponse({"message": "签名无效"}, status=403)
    if recovered != address:
        return JsonResponse({"message": "签名地址不匹配"}, status=403)
    if wallet_auth.consume_nonce(address, nonce) is None:
        return JsonResponse({"message": "登录码无效或已过期"}, status=403)

    now = timezone.now()
    profile = Profile.objects.filter(wallet_address=address).select_related("user").first()
    if profile is None:
        base = f"okx_{address[2:10]}"
        username = base
        n = 0
        while User.objects.filter(username=username).exists():
            n += 1
            username = f"{base}_{n}"
        user = User.objects.create_user(username=username)
        profile = Profile.objects.create(
            user=user,
            wallet_address=address,
            wallet_first_login_at=now,
        )
    else:
        user = profile.user
        if profile.wallet_first_login_at is None:
            profile.wallet_first_login_at = now
    profile.wallet_last_login_at = now
    profile.wallet_login_count = int(profile.wallet_login_count or 0) + 1
    profile.save(
        update_fields=["wallet_first_login_at", "wallet_last_login_at", "wallet_login_count"]
    )
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    adopt_session(request)
    return JsonResponse({"ok": True, "user": _user(request)})
