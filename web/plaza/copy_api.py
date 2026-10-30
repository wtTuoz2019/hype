"""前台个人中心：交易所密钥与跟单 JSON API。"""

from __future__ import annotations

import hashlib
import time

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .copy_plan import RULES
from .copy_run import enqueue_flatten
from .models import CopyLog, ExchangeAccount, Follow, Leader
from .tape import front_profile, is_pro
from .vault import encrypt

VENUE_HINTS = (
    {
        "id": ExchangeAccount.VENUE_BINANCE,
        "name": "币安 U 本位合约",
        "permissions": [
            "只创建「只读 + 允许合约」类 API，不要开提现",
            "必须勾选「启用期货」/ Futures 交易权限",
            "务必开启「限制仅信任 IP 访问」，把下方服务器 IP 加进白名单",
            "密钥仅用于跟单下单，勿与提现、万能主密钥混用",
        ],
    },
    {
        "id": ExchangeAccount.VENUE_GATE,
        "name": "Gate 永续合约",
        "permissions": [
            "API 权限勾选「期货」读写（或合约交易），不要开提现 / 钱包转出",
            "开启 IP 白名单，填入下方服务器出口 IP",
            "账户需已开通 USDT 永续，并完成相应风控设置",
            "建议单独建一把跟单专用密钥，方便随时作废",
        ],
    },
)


def whitelist_ip() -> str:
    return (getattr(settings, "COPY_API_WHITELIST_IP", "") or "").strip()


def copy_meta() -> dict:
    ip = whitelist_ip()
    return {
        "whitelistIp": ip,
        "whitelistHint": (
            f"请把服务器出口 IP {ip} 加入交易所 API 白名单，否则跟单会报 IP 错误。"
            if ip
            else "服务器出口 IP 尚未在后台配置（COPY_API_WHITELIST_IP）。配置前请向管理员索取再填白名单。"
        ),
        "rules": RULES,
        "venues": list(VENUE_HINTS),
    }


def _need_login():
    return JsonResponse({"message": "请先登录个人中心后再配置跟单。", "need": "login"}, status=401)


def _clean_secret(value: str) -> str:
    text = (value or "").strip()
    if len(text) < 16 or len(text) > 200 or any(ch.isspace() for ch in text):
        raise ValueError("API Key / Secret 至少 16 位，中间不能有空格")
    return text


def _fp(venue: str, api_key: str) -> str:
    return hashlib.sha256(f"{venue}\n{api_key}".encode()).hexdigest()


def _account_row(row: ExchangeAccount) -> dict:
    return {
        "id": row.id,
        "venue": row.venue,
        "venueLabel": row.get_venue_display(),
        "name": row.name,
        "keyLast4": row.key_last4,
        "enabled": bool(row.enabled),
        "halted": bool(row.halted),
        "created": row.created_at.strftime("%m-%d %H:%M") if row.created_at else "",
    }


def _follow_row(row: Follow) -> dict:
    leader = row.leader
    account = row.account
    return {
        "id": row.id,
        "leaderId": str(leader.id),
        "leaderName": leader.name or (leader.address[:10] + "…"),
        "leaderAddress": leader.address,
        "accountId": account.id if account else None,
        "accountLabel": str(account) if account else "—",
        "active": bool(row.active),
        "armed": row.armed_ms,
    }


def _log_row(row: CopyLog) -> dict:
    leader = row.follow.leader if row.follow_id else None
    return {
        "id": row.id,
        "leader": (leader.name or leader.address[:10]) if leader else "—",
        "coin": row.coin,
        "status": row.status,
        "qty": row.qty,
        "detail": row.detail,
        "when": row.created_at.strftime("%m-%d %H:%M:%S") if row.created_at else "",
    }


@require_GET
def copy_overview(request):
    profile = front_profile(request)
    if profile is None:
        return _need_login()
    accounts = [
        _account_row(row)
        for row in ExchangeAccount.objects.filter(user=request.user).order_by("id")
    ]
    follows = [
        _follow_row(row)
        for row in Follow.objects.filter(user=request.user)
        .select_related("leader", "account")
        .order_by("-active", "-updated_at")[:40]
    ]
    logs = [
        _log_row(row)
        for row in CopyLog.objects.filter(follow__user=request.user)
        .select_related("follow__leader")
        .order_by("-id")[:20]
    ]
    leaders = [
        {
            "id": str(row.id),
            "name": row.name or (row.address[:8] + "…" + row.address[-4:]),
            "address": row.address,
        }
        for row in Leader.objects.filter(enabled=True).order_by("id")[:200]
    ]
    return JsonResponse(
        {
            "pro": is_pro(request),
            "meta": copy_meta(),
            "accounts": accounts,
            "follows": follows,
            "logs": logs,
            "leaders": leaders,
        }
    )


@require_http_methods(["GET", "POST"])
def copy_accounts(request):
    profile = front_profile(request)
    if profile is None:
        return _need_login()
    if request.method == "GET":
        rows = ExchangeAccount.objects.filter(user=request.user).order_by("id")
        return JsonResponse({"accounts": [_account_row(row) for row in rows], "meta": copy_meta()})

    from .scope_pages import _body

    body = _body(request)
    venue = str(body.get("venue") or "").strip()
    name = str(body.get("name") or "").strip()[:40]
    if venue not in dict(ExchangeAccount.VENUES):
        return JsonResponse({"message": "请选择币安或 Gate"}, status=400)
    try:
        api_key = _clean_secret(str(body.get("apiKey") or body.get("api_key") or ""))
        api_secret = _clean_secret(str(body.get("apiSecret") or body.get("api_secret") or ""))
    except ValueError as exc:
        return JsonResponse({"message": str(exc)}, status=400)
    fp = _fp(venue, api_key)
    if ExchangeAccount.objects.filter(user=request.user, key_fp=fp).exists():
        return JsonResponse({"message": "这个密钥已经添加过"}, status=400)
    if not name:
        name = dict(ExchangeAccount.VENUES)[venue]
    row = ExchangeAccount.objects.create(
        user=request.user,
        venue=venue,
        name=name,
        key_last4=api_key[-4:],
        key_fp=fp,
        key_enc=encrypt(api_key),
        secret_enc=encrypt(api_secret),
    )
    return JsonResponse({"ok": True, "account": _account_row(row), "meta": copy_meta()})


@require_POST
def copy_account_toggle(request, account_id: int):
    if front_profile(request) is None:
        return _need_login()
    row = ExchangeAccount.objects.filter(pk=account_id, user=request.user).first()
    if row is None:
        return JsonResponse({"message": "账户不存在"}, status=404)
    row.enabled = not row.enabled
    row.save(update_fields=["enabled"])
    if not row.enabled:
        Follow.objects.filter(account=row, active=True).update(active=False)
    return JsonResponse({"ok": True, "account": _account_row(row)})


@require_POST
def copy_account_delete(request, account_id: int):
    if front_profile(request) is None:
        return _need_login()
    row = ExchangeAccount.objects.filter(pk=account_id, user=request.user).first()
    if row is None:
        return JsonResponse({"message": "账户不存在"}, status=404)
    Follow.objects.filter(account=row, active=True).update(active=False)
    row.delete()
    return JsonResponse({"ok": True})


@require_POST
def copy_follow_start(request):
    if front_profile(request) is None:
        return _need_login()
    from .scope_pages import _body

    body = _body(request)
    try:
        leader_id = int(body.get("leaderId") or body.get("leader_id") or 0)
        account_id = int(body.get("accountId") or body.get("account_id") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"message": "参数无效"}, status=400)
    leader = Leader.objects.filter(pk=leader_id, enabled=True).first()
    account = ExchangeAccount.objects.filter(pk=account_id, user=request.user, enabled=True).first()
    if leader is None:
        return JsonResponse({"message": "钱包不存在或已停用"}, status=404)
    if account is None:
        return JsonResponse({"message": "请选择一个已启用的币安或 Gate 账户"}, status=400)
    armed = int(time.time() * 1000)
    row = Follow.objects.filter(user=request.user, leader=leader).first()
    if row is None:
        row = Follow.objects.create(
            user=request.user, leader=leader, account=account, active=True, armed_ms=armed
        )
    else:
        row.account = account
        row.active = True
        row.armed_ms = armed
        row.save(update_fields=["account", "active", "armed_ms", "updated_at"])
    return JsonResponse({"ok": True, "follow": _follow_row(row)})


@require_POST
def copy_follow_stop(request):
    if front_profile(request) is None:
        return _need_login()
    from .scope_pages import _body

    body = _body(request)
    try:
        follow_id = int(body.get("id") or body.get("followId") or 0)
        leader_id = int(body.get("leaderId") or 0)
    except (TypeError, ValueError):
        follow_id = 0
        leader_id = 0
    row = None
    if follow_id:
        row = Follow.objects.filter(pk=follow_id, user=request.user).first()
    elif leader_id:
        row = Follow.objects.filter(user=request.user, leader_id=leader_id).first()
    if row is None:
        return JsonResponse({"message": "跟单关系不存在"}, status=404)
    if row.active:
        row.active = False
        row.save(update_fields=["active", "updated_at"])
        enqueue_flatten(row.id)
    return JsonResponse({"ok": True, "follow": _follow_row(row)})
