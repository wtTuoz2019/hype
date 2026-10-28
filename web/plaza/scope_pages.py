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
from .models import Profile
from .tape import (
    FREE_DELAY,
    PRO_DELAY,
    adopt_session,
    event_dict,
    front_profile,
    is_pro,
    next_visible_at,
    set_pro,
    subscriptions,
    today_focus,
    today_stats,
    toggle_sub,
    visible_events,
    wallet_cards,
    wallet_detail,
    wallet_rev,
)
from .telegram_auth import complete_telegram_login

PAGES = {
    "home": "index.html",
    "board": "leaderboard.html",
    "analysis": "analysis.html",
    "pricing": "pricing.html",
    "wallet": "wallet-detail.html",
}


def _asset(name: str) -> str:
    """静态资源带 mtime，避免浏览器/CDN 继续用旧的登录脚本。"""
    path = settings.HYPE_ROOT / "alphascope-simple" / name
    ver = int(path.stat().st_mtime) if path.exists() else 0
    return f"{static(name)}?v={ver}"


def _links() -> tuple[tuple[str, str], ...]:
    return (
        ("wallet-detail.html", "/wallet/"),
        ("leaderboard.html", "/leaderboard/"),
        ("analysis.html", "/analysis/"),
        ("pricing.html", "/pricing/"),
        ("index.html", "/"),
        ('href="style.css"', f'href="{_asset("style.css")}"'),
        ('src="telegram-auth.js"', f'src="{_asset("telegram-auth.js")}"'),
        ('src="app.js"', f'src="{_asset("app.js")}"'),
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


def _telegram_bot_id() -> str:
    token = settings.TELEGRAM_BOT_TOKEN or ""
    return token.split(":", 1)[0] if token else ""


def boot(request) -> dict:
    rows = visible_events(request)
    pro = is_pro(request)
    return {
        "pro": pro,
        "subs": subscriptions(request),
        "user": _user(request),
        "bot": settings.TELEGRAM_BOT_USERNAME,
        "botId": _telegram_bot_id(),
        "authUrl": request.build_absolute_uri("/auth/telegram/callback"),
        "wallet_rev": wallet_rev(),
        "wallets": wallet_cards(),
        "events": [event_dict(row) for row in rows],
        "today": today_stats(request),
        "today_focus": today_focus(request),
        "delay_sec": PRO_DELAY if pro else FREE_DELAY,
        "next_visible_at": next_visible_at(request),
        "poll_sec": 3,
    }


_HTML_SHELL: dict[str, str] = {}


def _scope_html(name: str) -> str:
    cached = _HTML_SHELL.get(name)
    if cached is not None:
        return cached
    raw = (settings.HYPE_ROOT / "alphascope-simple" / PAGES[name]).read_text(encoding="utf-8")
    for old, new in _links():
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
    auth_js = _asset("telegram-auth.js")
    marker = f'<script src="{auth_js}"></script>'
    # 兼容旧壳子里还没带 ?v= 的引用
    if marker not in raw:
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
        "today_focus": today_focus(request),
        "wallet_rev": rev,
        "incremental": False,
        "events": [],
        "delay_sec": PRO_DELAY if is_pro(request) else FREE_DELAY,
        "next_visible_at": next_visible_at(request),
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


def _telegram_error(message: str, status: int = 403) -> HttpResponse:
    body = (
        "<!doctype html><meta charset=utf-8><title>Telegram 登录</title>"
        f"<p style='font:15px/1.6 sans-serif;margin:48px 20px'>{message}</p>"
        "<p style='font:14px sans-serif;margin:0 20px'><a href='/'>返回首页重试</a></p>"
    )
    return HttpResponse(body, status=status, content_type="text/html; charset=utf-8")


@require_GET
def telegram_callback(request):
    profile, err = complete_telegram_login(request, request.GET)
    if err:
        status = 503 if "机器人" in err else 403
        return _telegram_error(err, status)
    # 授权小窗：刷新原页面并关闭自己；普通跳转则回首页
    return HttpResponse(
        """<!doctype html><meta charset="utf-8"><title>登录成功</title>
<script>
(function () {
  try {
    if (window.opener && !window.opener.closed) {
      window.opener.location.href = "/";
      window.close();
      return;
    }
  } catch (e) {}
  location.replace("/");
})();
</script>
<p style="font:15px/1.6 sans-serif;margin:40px 20px">登录成功，正在返回… <a href="/">点这里回首页</a></p>
""",
        content_type="text/html; charset=utf-8",
    )


@require_POST
def telegram_login(request):
    """前端 onauth 回调：不跳转，直接建会话并返回会员信息。"""
    profile, err = complete_telegram_login(request, _body(request))
    if err:
        status = 503 if "机器人" in err else 403
        return JsonResponse({"message": err}, status=status)
    return JsonResponse({"ok": True, "user": _user(request), "pro": is_pro(request), "subs": subscriptions(request)})


@require_GET
def telegram_start(request):
    """整页跳转 Telegram 官方授权，避免嵌在 dialog/iframe 里回调丢失。"""
    bot_id = _telegram_bot_id()
    if not bot_id:
        return _telegram_error("还没有配置 Telegram 机器人。", 503)
    from urllib.parse import urlencode

    # 生产站一律按 https 域名拼 origin，避免反代识别成 http 导致 Telegram 拒授
    host = request.get_host().split(":")[0]
    origin = f"https://{host}"
    auth_url = f"https://{host}/auth/telegram/callback"
    oauth = "https://oauth.telegram.org/auth?" + urlencode(
        {
            "bot_id": bot_id,
            "origin": origin,
            "request_access": "write",
            "return_to": auth_url,
        }
    )
    return redirect(oauth)


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
