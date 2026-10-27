import hashlib
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .copy_plan import RULES
from .copy_run import enqueue_flatten
from .models import CopyLog, ExchangeAccount, Follow, Leader, Profile, TradeSignal
from .vault import encrypt
from .services import ensure_leaders, leader_view

PERIODS = (("24h", "24小时"), ("7d", "7日"), ("30d", "30日"), ("all", "全部"))
LABELS = ("开多", "开空", "加多", "加空", "减多", "减空", "平多", "平空", "翻多", "翻空")


def _profile(user: User) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def _money(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.2f}"


def _pct(value) -> str:
    try:
        number = float(value) * 100
    except (TypeError, ValueError):
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _pack(stats: dict, key: str) -> dict:
    shown = (stats.get("shown") or {}).get(key)
    if shown:
        return shown
    period = (stats.get("periods") or {}).get(key) or {}
    pnl = period.get("pnl") or 0
    from .charts import pnl_svg

    return {
        "pnl": _money(pnl),
        "pnl_up": float(pnl or 0) >= 0,
        "roi": _pct(period.get("roi")),
        "equity": _money(period.get("equity")).lstrip("+"),
        "volume": _money(period.get("volume")).lstrip("+"),
        "chart": pnl_svg(period.get("curve") or [], width=720, height=200),
        "spark": pnl_svg(period.get("curve") or [], width=240, height=72),
    }


def _when_ms(value) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "-"


def _kept(request) -> str:
    data = {}
    for key in ("leader", "coin", "label"):
        value = (request.GET.get(key) or "").strip()
        if value:
            data[key] = value
    return urlencode(data)


def _notional(row: TradeSignal) -> float:
    try:
        return abs(float(row.delta_szi or 0)) * float(row.px or 0)
    except (TypeError, ValueError):
        return 0.0


def _amount(value: float) -> str:
    if value >= 10000:
        return f"{value / 10000:,.1f} 万 USDT"
    return f"{value:,.0f} USDT"


def home(request):
    ensure_leaders()
    rows = list(TradeSignal.objects.select_related("leader")[:40])
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(start.timestamp() * 1000)
    today = TradeSignal.objects.filter(time_ms__gte=start_ms).select_related("leader")
    long_funds = short_funds = 0.0
    long_wallets: set[int] = set()
    short_wallets: set[int] = set()
    opens = []
    for row in today:
        if row.label not in ("开多", "开空", "加多", "加空"):
            continue
        amount = _notional(row)
        if "空" in row.label:
            short_funds += amount
            short_wallets.add(row.leader_id)
        else:
            long_funds += amount
            long_wallets.add(row.leader_id)
        if row.label.startswith("开"):
            opens.append({"amount": amount, "text": _amount(amount), "row": row})
    opens.sort(key=lambda item: item["amount"], reverse=True)
    total = long_funds + short_funds
    ratio = (long_funds / total * 100) if total else 0
    return render(
        request,
        "plaza/home.html",
        {
            "rows": rows,
            "latest": rows[0].id if rows else 0,
            "long_funds": _amount(long_funds),
            "short_funds": _amount(short_funds),
            "long_share": f"{ratio:.1f}",
            "short_share": f"{(100 - ratio) if total else 0:.1f}",
            "long_wallets": len(long_wallets),
            "short_wallets": len(short_wallets),
            "fund_gap": _amount(abs(long_funds - short_funds)),
            "opens": opens[:3],
            "top": opens[0] if opens else None,
        },
    )


def board(request):
    ensure_leaders()
    order = request.GET.get("order") or "roi"
    if order not in ("roi", "win"):
        order = "roi"
    term = (request.GET.get("q") or "").strip().lower()
    cards = []
    for leader in Leader.objects.filter(enabled=True).select_related("snapshot").order_by("id"):
        haystack = f"{leader.name} {leader.address}".lower()
        if term and term not in haystack:
            continue
        stats = leader_view(leader)
        pack = _pack(stats, "30d")
        period = (stats.get("periods") or {}).get("30d") or {}
        try:
            roi_value = float(period.get("roi") or 0)
        except (TypeError, ValueError):
            roi_value = 0.0
        win = stats.get("win_rate")
        try:
            win_value = float(win) if win is not None else -1.0
        except (TypeError, ValueError):
            win_value = -1.0
        cards.append(
            {
                "leader": leader,
                "pack": pack,
                "waiting": bool(stats.get("waiting")),
                "roi_value": roi_value,
                "win_value": win_value,
                "win_rate": f"{win_value * 100:.1f}%" if win_value >= 0 else "-",
                "closes": stats.get("close_count") or 0,
            }
        )
    cards.sort(key=lambda card: card["win_value"] if order == "win" else card["roi_value"], reverse=True)
    return render(request, "plaza/board.html", {"cards": cards, "order": order, "term": request.GET.get("q") or ""})


def _signal_query(leader_id: str, coin: str, label: str):
    qs = TradeSignal.objects.select_related("leader")
    if leader_id:
        qs = qs.filter(leader_id=leader_id)
    if coin:
        qs = qs.filter(coin__iexact=coin)
    if label in LABELS:
        qs = qs.filter(label=label)
    return qs


def _signal_filters(request):
    leader_id = (request.GET.get("leader") or "").strip()
    coin = (request.GET.get("coin") or "").strip()[:40]
    label = (request.GET.get("label") or "").strip()
    if label not in LABELS:
        label = ""
    return leader_id, coin, label


def signals_page(request):
    leader_id, coin, label = _signal_filters(request)
    try:
        page_num = int(request.GET.get("page") or 1)
    except ValueError:
        page_num = 1
    paginator = Paginator(_signal_query(leader_id, coin, label), 50)
    page = paginator.get_page(page_num)
    rows = list(page.object_list)
    latest = rows[0].id if page.number == 1 and rows else 0
    leader = Leader.objects.filter(pk=leader_id).first() if leader_id else None
    return render(
        request,
        "plaza/signals.html",
        {
            "rows": rows,
            "page": page,
            "latest": latest,
            "leader": leader,
            "coin": coin,
            "label": label,
            "labels": LABELS,
            "kept": _kept(request),
            "total": paginator.count,
        },
    )


def signal_feed(request):
    try:
        after = int(request.GET.get("after") or 0)
    except ValueError:
        after = 0
    leader_id, coin, label = _signal_filters(request)
    rows = list(_signal_query(leader_id, coin, label).filter(id__gt=after).order_by("id")[:50])
    payload = []
    for row in rows:
        payload.append(
            {
                "id": row.id,
                "when": row.when(),
                "name": row.leader.name or row.leader.address[:8],
                "leader_id": row.leader_id,
                "coin": row.coin,
                "label": row.label,
                "prev_szi": row.prev_szi,
                "szi": row.szi,
                "delta_szi": row.delta_szi,
                "px": row.px,
            }
        )
    latest = rows[-1].id if rows else after
    return JsonResponse({"latest": latest, "signals": payload})


def detail(request, leader_id: int):
    ensure_leaders()
    leader = get_object_or_404(
        Leader.objects.filter(enabled=True).select_related("snapshot"), pk=leader_id
    )
    chosen = request.GET.get("range") or "7d"
    if chosen not in {key for key, _label in PERIODS}:
        chosen = "7d"
    stats = leader_view(leader)
    followed = False
    follow_row = None
    accounts = []
    if request.user.is_authenticated:
        follow_row = (
            Follow.objects.filter(user=request.user, leader=leader).select_related("account").first()
        )
        followed = bool(follow_row and follow_row.active)
        accounts = list(
            ExchangeAccount.objects.filter(user=request.user, enabled=True).order_by("id")
        )
    win = stats.get("win_rate")
    closes = stats.get("shown_closes")
    if closes is None:
        closes = []
        for row in stats.get("closes") or []:
            pnl = row.get("pnl") or 0
            closes.append(
                {
                    **row,
                    "when": row.get("when") or _when_ms(row.get("time")),
                    "pnl_text": _money(pnl),
                    "pnl_up": float(pnl or 0) >= 0,
                }
            )
    return render(
        request,
        "plaza/detail.html",
        {
            "leader": leader,
            "pack": _pack(stats, chosen),
            "range": chosen,
            "periods": PERIODS,
            "positions": stats.get("positions") or [],
            "closes": closes,
            "upnl": stats.get("shown_upnl") or _money(stats.get("upnl")),
            "upnl_up": stats.get("shown_upnl_up", float(stats.get("upnl") or 0) >= 0),
            "win_rate": stats.get("shown_win") or (f"{float(win) * 100:.1f}%" if win is not None else "-"),
            "close_count": stats.get("close_count") or 0,
            "followed": followed,
            "follow_row": follow_row,
            "accounts": accounts,
            "waiting": bool(stats.get("waiting")),
            "updated": stats.get("updated") or "",
            "error": stats.get("error") or "",
            "rules": RULES,
            "signals": TradeSignal.objects.filter(leader=leader)[:12],
        },
    )


def _clean_secret(value: str) -> str:
    text = (value or "").strip()
    if len(text) < 16 or len(text) > 200 or any(ch.isspace() for ch in text):
        raise ValueError("密钥长度或格式不对")
    return text


def _account_fp(venue: str, api_key: str) -> str:
    return hashlib.sha256(f"{venue}\n{api_key}".encode()).hexdigest()


@login_required
def accounts_page(request):
    rows = ExchangeAccount.objects.filter(user=request.user).order_by("id")
    return render(
        request,
        "plaza/accounts.html",
        {"rows": rows, "venues": ExchangeAccount.VENUES, "error": ""},
    )


@login_required
@require_POST
def account_add(request):
    venue = (request.POST.get("venue") or "").strip()
    name = (request.POST.get("name") or "").strip()[:40]
    if venue not in dict(ExchangeAccount.VENUES):
        venue = ""
    try:
        api_key = _clean_secret(request.POST.get("api_key") or "")
        api_secret = _clean_secret(request.POST.get("api_secret") or "")
    except ValueError:
        api_key = ""
        api_secret = ""
    error = ""
    if not venue:
        error = "请选择币安或 Gate"
    elif not api_key or not api_secret:
        error = "API Key 和 Secret 至少 16 位，中间不能有空格"
    else:
        fp = _account_fp(venue, api_key)
        if ExchangeAccount.objects.filter(user=request.user, key_fp=fp).exists():
            error = "这个密钥已经添加过"
        else:
            if not name:
                name = dict(ExchangeAccount.VENUES)[venue]
            ExchangeAccount.objects.create(
                user=request.user,
                venue=venue,
                name=name,
                key_last4=api_key[-4:],
                key_fp=fp,
                key_enc=encrypt(api_key),
                secret_enc=encrypt(api_secret),
            )
            return redirect("accounts")
    return render(
        request,
        "plaza/accounts.html",
        {
            "rows": ExchangeAccount.objects.filter(user=request.user).order_by("id"),
            "venues": ExchangeAccount.VENUES,
            "error": error,
            "name": name,
            "venue": venue,
        },
    )


@login_required
@require_POST
def account_toggle(request, account_id: int):
    row = get_object_or_404(ExchangeAccount, pk=account_id, user=request.user)
    row.enabled = not row.enabled
    row.save(update_fields=["enabled"])
    return redirect("accounts")


@login_required
@require_POST
def account_delete(request, account_id: int):
    row = get_object_or_404(ExchangeAccount, pk=account_id, user=request.user)
    Follow.objects.filter(account=row, active=True).update(active=False)
    row.delete()
    return redirect("accounts")


@login_required
@require_POST
def follow(request, leader_id: int):
    leader = get_object_or_404(Leader.objects.filter(enabled=True), pk=leader_id)
    row = Follow.objects.filter(user=request.user, leader=leader).first()
    if request.POST.get("stop") == "1":
        if row is not None and row.active:
            row.active = False
            row.save(update_fields=["active", "updated_at"])
            enqueue_flatten(row.id)
        return redirect("detail", leader_id=leader.id)
    try:
        account_id = int(request.POST.get("account") or 0)
    except ValueError:
        account_id = 0
    account = ExchangeAccount.objects.filter(
        pk=account_id, user=request.user, enabled=True
    ).first()
    if account is None:
        messages.error(request, "请选择一个可用的币安或 Gate 账户")
        return redirect("detail", leader_id=leader.id)
    armed = int(time.time() * 1000)
    if row is None:
        Follow.objects.create(
            user=request.user, leader=leader, account=account, active=True, armed_ms=armed
        )
    else:
        row.account = account
        row.active = True
        row.armed_ms = armed
        row.save(update_fields=["account", "active", "armed_ms", "updated_at"])
    return redirect("detail", leader_id=leader.id)


@login_required
def me(request):
    rows = (
        Follow.objects.filter(user=request.user, active=True)
        .select_related("leader", "account")
        .order_by("-updated_at")
    )
    logs = CopyLog.objects.filter(follow__user=request.user).select_related("follow__leader")[:20]
    return render(request, "plaza/me.html", {"rows": rows, "logs": logs, "rules": RULES})


@login_required
def invite(request):
    profile = _profile(request.user)
    invitees = User.objects.filter(profile__invited_by=request.user).order_by("-date_joined")
    return render(
        request,
        "plaza/invite.html",
        {"profile": profile, "invitees": invitees},
    )


def _staff_only(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("只有管理员可以管理钱包")
    return None


def _warm_leader(leader_id: int) -> None:
    from django.db import close_old_connections

    close_old_connections()
    from plaza.ingest import backfill_leader
    from plaza.services import refresh_leader

    leader = Leader.objects.filter(pk=leader_id).first()
    if leader is None:
        return
    try:
        count = backfill_leader(leader)
        print(f"补写 {leader.address[:10]} 信号 {count} 条", flush=True)
    except Exception as exc:
        print(f"补写信号失败: {exc}", flush=True)
    try:
        refresh_leader(leader, full=True)
    except Exception as exc:
        print(f"刷新收益失败: {exc}", flush=True)


@login_required
def wallets_page(request):
    denied = _staff_only(request)
    if denied:
        return denied
    return render(request, "plaza/wallets.html", {"rows": Leader.objects.order_by("id"), "error": ""})


@login_required
@require_POST
def wallet_add(request):
    denied = _staff_only(request)
    if denied:
        return denied
    name = (request.POST.get("name") or "").strip()[:40]
    raw = (request.POST.get("address") or "").strip()
    try:
        from wallet_store import norm_address

        address = norm_address(raw)
    except ValueError:
        return render(
            request,
            "plaza/wallets.html",
            {"rows": Leader.objects.order_by("id"), "error": "地址格式不对", "name": name, "address": raw},
        )
    leader, created = Leader.objects.get_or_create(
        address=address, defaults={"name": name, "enabled": True, "source": ""}
    )
    if not created:
        leader.name = name or leader.name
        leader.enabled = True
        leader.source = ""
        leader.save(update_fields=["name", "enabled", "source"])
    if created or not TradeSignal.objects.filter(leader=leader).exists():
        threading.Thread(target=_warm_leader, args=(leader.id,), name="hype-warm", daemon=True).start()
    return redirect("wallets")


@login_required
@require_POST
def wallet_delete(request, leader_id: int):
    denied = _staff_only(request)
    if denied:
        return denied
    leader = get_object_or_404(Leader, pk=leader_id)
    leader.delete()
    return redirect("wallets")


@login_required
@require_POST
def wallet_toggle(request, leader_id: int):
    denied = _staff_only(request)
    if denied:
        return denied
    leader = get_object_or_404(Leader, pk=leader_id)
    leader.enabled = not leader.enabled
    leader.save(update_fields=["enabled"])
    return redirect("wallets")


def register_view(request):
    error = ""
    code = (request.GET.get("code") or request.POST.get("code") or "").strip()
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""
        inviter = None
        if code:
            inviter_profile = Profile.objects.filter(invite_code=code).select_related("user").first()
            if inviter_profile is None:
                error = "邀请码不存在"
            elif inviter_profile.user.username == username:
                error = "不能用自己的邀请码"
            else:
                inviter = inviter_profile.user
        if not error and password != password2:
            error = "两次密码不一致"
        if not error and (len(username) < 2 or User.objects.filter(username=username).exists()):
            error = "账号不可用"
        if not error:
            try:
                validate_password(password)
            except ValidationError as exc:
                error = exc.messages[0]
        if not error:
            first = not User.objects.exists()
            user = User.objects.create_user(username=username, password=password)
            if first and settings.DEBUG:
                user.is_staff = True
                user.is_superuser = True
                user.save(update_fields=["is_staff", "is_superuser"])
            Profile.objects.create(user=user, invited_by=inviter)
            login(request, user)
            return redirect("home")
    return render(request, "plaza/register.html", {"error": error, "code": code})


def login_view(request):
    error = ""
    if request.method == "POST":
        user = authenticate(
            request,
            username=(request.POST.get("username") or "").strip(),
            password=request.POST.get("password") or "",
        )
        if user is None:
            error = "账号或密码不对"
        else:
            login(request, user)
            return redirect("home")
    return render(request, "plaza/login.html", {"error": error})


def logout_view(request):
    logout(request)
    return redirect("home")
