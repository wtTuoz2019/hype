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
    claim_pro_from_group,
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
    "account": "account.html",
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
        ("account.html", "/account/"),
        ("index.html", "/"),
        ('href="style.css"', f'href="{_asset("style.css")}"'),
        ('src="telegram-auth.js"', f'src="{_asset("telegram-auth.js")}"'),
        ('src="app.js"', f'src="{_asset("app.js")}"'),
    )


def _user(request) -> dict | None:
    """前台会员才显示已登录；后台 admin 不算。优先显示昵称。"""
    profile = front_profile(request)
    if profile is None:
        return None
    handle = (profile.telegram_username or "").strip()
    name = (profile.telegram_name or "").strip()
    if not name:
        name = f"{profile.telegram_first_name} {profile.telegram_last_name}".strip()
    if name:
        label = name
    elif handle:
        label = f"@{handle}"
    elif profile.wallet_address:
        label = wallet_auth.short_address(profile.wallet_address)
    else:
        label = request.user.get_username()
    login = "wallet" if profile.wallet_address and not profile.telegram_id else "telegram"
    if profile.telegram_id and profile.wallet_address:
        login = "telegram"
    last = profile.telegram_last_login_at or profile.wallet_last_login_at
    last_text = ""
    if last is not None:
        from django.utils import timezone as dj_tz

        last_text = dj_tz.localtime(last).strftime("%m-%d %H:%M")
    return {
        "label": label,
        "handle": handle,
        "photo": profile.telegram_photo or "",
        "login": login,
        "wallet": wallet_auth.short_address(profile.wallet_address) if profile.wallet_address else "",
        "invite": profile.invite_code or "",
        "last_login": last_text,
        "pro": bool(profile.is_pro),
    }

def _telegram_bot_id() -> str:
    token = settings.TELEGRAM_BOT_TOKEN or ""
    return token.split(":", 1)[0] if token else ""


def boot(request) -> dict:
    from plaza.telegram_group import pro_invite_url

    rows = visible_events(request)
    pro = is_pro(request)
    return {
        "pro": pro,
        "subs": subscriptions(request),
        "user": _user(request),
        "bot": settings.TELEGRAM_BOT_USERNAME,
        "botId": _telegram_bot_id(),
        "authUrl": request.build_absolute_uri("/auth/telegram/callback"),
        "groupInvite": pro_invite_url(),
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
    # 会员/个人中心不需要塞快讯；个人中心要瘦钱包列表用于展示订阅
    if name in ("pricing", "account"):
        data = boot(request)
        data["events"] = []
        if name == "pricing":
            data["wallets"] = []
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


@require_GET
def tts_audio(request):
    """Pro 试听/播报用的中文语音 mp3。不走浏览器 speechSynthesis。"""
    if not is_pro(request):
        return JsonResponse({"message": "语音播报仅限 Pro"}, status=403)
    text = (request.GET.get("q") or "").strip()
    if not text:
        return JsonResponse({"message": "缺少文本"}, status=400)
    try:
        from .tts import synthesize

        data = synthesize(text)
    except Exception as exc:  # noqa: BLE001 — 前端可降级
        return JsonResponse({"message": f"语音合成失败：{exc}"}, status=502)
    resp = HttpResponse(data, content_type="audio/mpeg")
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


@require_POST
def membership(request):
    body = _body(request)
    want = body.get("pro")
    action = str(body.get("action") or "").strip().lower()
    if want is True or action in ("claim", "upgrade", "join"):
        result = claim_pro_from_group(request)
        status = 200 if result.get("pro") else 403
        return JsonResponse(result, status=status)
    if want is False or action in ("cancel", "downgrade"):
        set_pro(request, False)
        return JsonResponse(
            {
                "pro": False,
                "subs": subscriptions(request),
                "message": "已关闭 Pro。",
            }
        )
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


def _telegram_bridge(ok: bool, message: str = "") -> HttpResponse:
    """授权小窗收尾：用 postMessage 通知原页面，避免 COOP 弄丢 opener 时主页不知情。"""
    import json

    payload = json.dumps({"type": "tg-login", "ok": bool(ok), "message": message or ""}, ensure_ascii=False)
    title = "登录成功" if ok else "登录失败"
    return HttpResponse(
        f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<script>
(function () {{
  var data = {payload};
  try {{
    if (window.opener && !window.opener.closed) {{
      window.opener.postMessage(data, window.location.origin);
    }}
  }} catch (e) {{}}
  try {{ window.close(); }} catch (e) {{}}
  if (data.ok) {{
    document.write('<p style="font:15px/1.6 sans-serif;margin:40px 20px">登录成功。可以关闭此窗口，回到原页面。</p>');
  }} else {{
    document.write('<p style="font:15px/1.6 sans-serif;margin:40px 20px;color:#b91c1c">' + (data.message || '登录失败') + '</p><p style="margin:0 20px"><a href="/">回首页</a></p>');
  }}
}})();
</script>
""",
        content_type="text/html; charset=utf-8",
        status=200 if ok else 403,
    )


@require_GET
def telegram_callback(request):
    profile, err = complete_telegram_login(request, request.GET)
    if err:
        return _telegram_bridge(False, err)
    if front_profile(request) is None:
        return _telegram_bridge(False, "授权过了，但会话没建上。请检查浏览器是否拦截了 Cookie。")
    return redirect("/")


@require_POST
def telegram_login(request):
    """前端 onauth 回调：不跳转，直接建会话并返回会员信息。"""
    profile, err = complete_telegram_login(request, _body(request))
    if err:
        status = 503 if "机器人" in err else 403
        return JsonResponse({"message": err}, status=status)
    if front_profile(request) is None:
        return JsonResponse({"message": "授权过了，但会话没建上。请检查浏览器是否拦截了 Cookie。"}, status=500)
    return JsonResponse({"ok": True, "user": _user(request), "pro": is_pro(request), "subs": subscriptions(request)})


@require_GET
def auth_status(request):
    return JsonResponse({"user": _user(request), "pro": is_pro(request)})


@ensure_csrf_cookie
@require_GET
def telegram_start(request):
    """独立登录页：官方 Widget + onauth，避开 dialog/小窗/长 URL 签名问题。"""
    bot = (settings.TELEGRAM_BOT_USERNAME or "").lstrip("@")
    if not bot or not settings.TELEGRAM_BOT_TOKEN:
        return _telegram_error("还没有配置 Telegram 机器人。", 503)
    # 启动前核对 token 是否真属于这个 bot
    token_hint = ""
    try:
        import json as _json
        import urllib.request

        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe", timeout=6
        ) as resp:
            me = _json.loads(resp.read().decode())
        if not me.get("ok"):
            return _telegram_error("TELEGRAM_BOT_TOKEN 无效，getMe 失败。", 503)
        real = (me.get("result") or {}).get("username") or ""
        if real and real.lower() != bot.lower():
            return _telegram_error(
                f"Token 对应机器人是 @{real}，但配置的用户名是 @{bot}。请改成一致。",
                503,
            )
        token_hint = f"@{real}" if real else f"@{bot}"
    except Exception as exc:  # noqa: BLE001 — 展示给管理员排查
        token_hint = f"@{bot}"
        # 网络不通时仍展示 widget，但把提示写上
        _ = exc

    # 用 data-auth-url：点「Log in as xxx」会整页跳回调，不依赖 onauth（线上常卡死）
    host = request.get_host().split(":")[0]
    auth_url = f"https://{host}/auth/telegram/callback"
    body = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram 登录 · AlphaScope</title>
<style>
body{{margin:0;background:#f5f7fb;color:#0f172a;font:15px/1.6 system-ui,-apple-system,sans-serif}}
main{{max-width:420px;margin:56px auto;padding:28px;background:#fff;border:1px solid #e2e8f0;border-radius:16px;box-shadow:0 10px 40px #0f172a14}}
h1{{margin:0 0 8px;font-size:22px}}
p{{color:#64748b;margin:0 0 14px}}
#tg-widget{{margin:18px 0;min-height:44px}}
a{{color:#2563eb}}
.tip{{font-size:12px;color:#94a3b8}}
</style></head><body><main>
<h1>Telegram 登录</h1>
<p>点下方按钮完成授权，随后自动回到首页。</p>
<div id="tg-widget"></div>
<p class="tip">机器人 {token_hint} · 跳转属正常现象</p>
<p><a href="/">← 返回首页</a> · <a href="/?login=okx">改用 OKX 钱包</a></p>
</main>
<script>
(function(){{
  var s=document.createElement('script');
  s.async=true;
  s.src='https://telegram.org/js/telegram-widget.js?22';
  s.setAttribute('data-telegram-login',{bot!r});
  s.setAttribute('data-size','large');
  s.setAttribute('data-radius','8');
  s.setAttribute('data-auth-url',{auth_url!r});
  s.setAttribute('data-request-access','write');
  document.getElementById('tg-widget').appendChild(s);
}})();
</script>
</body></html>"""
    return HttpResponse(body)


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
