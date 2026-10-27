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

from .models import Profile, TelegramAuth
from .tape import (
    adopt_session,
    event_dict,
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
    if not request.user.is_authenticated:
        return None
    profile = getattr(request.user, "profile", None)
    name = (profile.telegram_name if profile else "") or request.user.get_username()
    handle = profile.telegram_username if profile else ""
    label = f"@{handle}" if handle else name
    return {"label": label, "photo": (profile.telegram_photo if profile else "") or ""}


def boot(request) -> dict:
    rows = visible_events(request)
    return {
        "pro": is_pro(request),
        "subs": subscriptions(request),
        "user": _user(request),
        "bot": settings.TELEGRAM_BOT_USERNAME,
        "authUrl": request.build_absolute_uri("/auth/telegram/callback"),
        "wallet_rev": wallet_rev(),
        "wallets": wallet_cards(),
        "events": [event_dict(row) for row in rows],
        "today": today_stats(request),
    }


@ensure_csrf_cookie
def render_scope(request, name: str) -> HttpResponse:
    raw = (settings.HYPE_ROOT / "alphascope-simple" / PAGES[name]).read_text(encoding="utf-8")
    for old, new in LINKS:
        raw = raw.replace(old, new)
    payload = json.dumps(boot(request), ensure_ascii=False).replace("<", "\\u003c")
    script = f'<script>window.ALPHASCOPE={payload}</script>'
    marker = f'<script src="{static("telegram-auth.js")}"></script>'
    raw = raw.replace(marker, script + marker, 1)
    return HttpResponse(raw)


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
    if profile is None:
        user = User.objects.create_user(username=f"tg_{telegram_id}")
        profile = Profile.objects.create(user=user, telegram_id=telegram_id)
    else:
        user = profile.user
    profile.telegram_name = (request.GET.get("first_name") or "")[:80]
    last = (request.GET.get("last_name") or "").strip()
    if last:
        profile.telegram_name = f"{profile.telegram_name} {last}".strip()[:80]
    profile.telegram_username = (request.GET.get("username") or "")[:64]
    profile.telegram_photo = (request.GET.get("photo_url") or "")[:300]
    profile.save(update_fields=["telegram_name", "telegram_username", "telegram_photo"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    adopt_session(request)
    return redirect("home")
