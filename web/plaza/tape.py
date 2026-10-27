"""开单快讯和排行榜。只读已经记下的永续信号和收益缓存。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from django.utils import timezone

from .models import AlertSub, Leader, LeaderSnapshot, TradeSignal
from .services import _money, ensure_leaders, leader_view

FREE_DELAY = 30 * 60
PRO_DELAY = 60

_CARD_LOCK = threading.Lock()
_CARD_CACHE: dict = {"rev": "", "at": 0.0, "rows": []}
_CARD_TTL = 45.0


def is_pro(request) -> bool:
    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile is not None and profile.is_pro:
            return True
    return bool(request.session.get("tape_pro"))


def subscriptions(request) -> list[str]:
    if request.user.is_authenticated:
        return [str(item) for item in AlertSub.objects.filter(user=request.user).values_list("leader_id", flat=True)]
    return [str(item) for item in (request.session.get("tape_subs") or [])]


def set_pro(request, enabled: bool) -> None:
    request.session["tape_pro"] = bool(enabled)
    if not enabled:
        request.session["tape_subs"] = []
    request.session.modified = True
    if request.user.is_authenticated:
        from .models import Profile

        profile, _created = Profile.objects.get_or_create(user=request.user)
        profile.is_pro = bool(enabled)
        profile.save(update_fields=["is_pro"])
        if not enabled:
            AlertSub.objects.filter(user=request.user).delete()


def toggle_sub(request, code: str) -> list[str]:
    try:
        leader_id = int(code)
    except (TypeError, ValueError):
        return subscriptions(request)
    leader = Leader.objects.filter(pk=leader_id, enabled=True).first()
    if leader is None:
        return subscriptions(request)
    if request.user.is_authenticated:
        row = AlertSub.objects.filter(user=request.user, leader=leader).first()
        if row:
            row.delete()
        else:
            AlertSub.objects.create(user=request.user, leader=leader)
        return subscriptions(request)
    current = subscriptions(request)
    key = str(leader.id)
    if key in current:
        current = [item for item in current if item != key]
    else:
        current = [*current, key]
    request.session["tape_subs"] = current
    request.session.modified = True
    return current


def adopt_session(request) -> None:
    """登录后把这次浏览里的会员和订阅记到账号上。"""
    if not request.user.is_authenticated:
        return
    from .models import Profile

    profile, _created = Profile.objects.get_or_create(user=request.user)
    if request.session.get("tape_pro"):
        profile.is_pro = True
        profile.save(update_fields=["is_pro"])
    if profile.is_pro:
        for code in request.session.get("tape_subs") or []:
            leader = Leader.objects.filter(pk=code, enabled=True).first()
            if leader:
                AlertSub.objects.get_or_create(user=request.user, leader=leader)
    request.session["tape_pro"] = profile.is_pro
    request.session["tape_subs"] = subscriptions(request)
    request.session.modified = True


def _cutoff_ms(request) -> int:
    delay = PRO_DELAY if is_pro(request) else FREE_DELAY
    return int((timezone.now() - timedelta(seconds=delay)).timestamp() * 1000)


def visible_events(request, *, after_id: int | None = None, limit: int = 80):
    qs = TradeSignal.objects.select_related("leader").filter(
        leader__enabled=True,
        time_ms__gt=0,
        time_ms__lte=_cutoff_ms(request),
    )
    if after_id:
        qs = qs.filter(id__gt=after_id).order_by("time_ms", "id")
        return list(qs[:limit])
    return list(qs.order_by("-time_ms", "-id")[:limit])


def _notional(row: TradeSignal) -> float:
    try:
        return abs(float(row.delta_szi or 0)) * float(row.px or 0)
    except (TypeError, ValueError):
        return 0.0


def event_dict(row: TradeSignal) -> dict:
    try:
        clock = datetime.fromtimestamp(row.time_ms / 1000, tz=timezone.get_current_timezone()).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        clock = "-"
    return {
        "id": row.id,
        "time": clock,
        "symbol": row.coin,
        "act": row.label,
        "wallet": str(row.leader_id),
        "amount": _notional(row),
    }


def today_stats(request) -> dict:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(start.timestamp() * 1000)
    labels = TradeSignal.objects.filter(
        leader__enabled=True,
        time_ms__gte=start_ms,
        time_ms__lte=_cutoff_ms(request),
    ).values_list("label", flat=True)
    long_count = short_count = close_count = 0
    for label in labels:
        if label in ("开多", "加多"):
            long_count += 1
        elif label in ("开空", "加空"):
            short_count += 1
        elif str(label).startswith("平"):
            close_count += 1
    return {"long": long_count, "short": short_count, "close": close_count}


def wallet_rev() -> str:
    """快照有没有更新：前端拿这个决定要不要拉钱包卡片。"""
    latest = (
        LeaderSnapshot.objects.filter(leader__enabled=True)
        .order_by("-fetched_at")
        .values_list("fetched_at", flat=True)
        .first()
    )
    count = Leader.objects.filter(enabled=True).count()
    stamp = str(int(latest.timestamp() * 1000)) if latest else "0"
    return f"{count}:{stamp}"


def _float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _drawdown(curve, end_equity: float, last_pnl: float) -> float:
    start = end_equity - last_pnl
    peak = None
    worst = 0.0
    for point in curve or []:
        try:
            equity = start + float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if equity <= 0:
            continue
        peak = equity if peak is None else max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst * 100


def _side_from_dir(direction: str) -> str:
    text = direction or ""
    closing = text.startswith("Close") or ">" in text
    if "Short" in text and "Long" in text:
        return "翻空" if text.endswith("Short") else "翻多"
    if "Short" in text:
        return "平空" if closing else "开空"
    if "Long" in text:
        return "平多" if closing else "开多"
    return text or "—"


def _trade_row(coin: str, side: str, px: str, pnl: float) -> dict:
    return {
        "coin": coin,
        "side": side,
        "lev": "—",
        "px": px or "—",
        "pnl": _money(pnl),
        "up": pnl >= 0,
    }


def _card(leader: Leader) -> dict:
    stats = leader_view(leader)
    name = leader.name or (leader.address[:8] + "…" + leader.address[-4:])
    period = (stats.get("periods") or {}).get("30d") or {}
    roi = _float(period.get("roi")) * 100
    pnl = _float(period.get("pnl"))
    equity = _float(period.get("equity"))
    curve = period.get("curve") or []
    dd = _drawdown(curve, equity, pnl)
    win_rate = stats.get("win_rate")
    total = int(stats.get("close_count") or 0)
    win = _float(win_rate) * 100 if win_rate is not None else 0.0
    wins = int(round(win / 100 * total)) if total and win_rate is not None else 0
    positions = []
    long_notional = short_notional = 0.0
    for row in stats.get("positions") or []:
        size = _float(row.get("szi"))
        entry = _float(row.get("entry"))
        notional = abs(size) * entry
        if size >= 0:
            long_notional += notional
        else:
            short_notional += notional
        positions.append(
            _trade_row(str(row.get("coin") or ""), "开多" if size >= 0 else "开空", str(row.get("entry") or ""), _float(row.get("upnl")))
        )
    history = []
    for row in stats.get("closes") or []:
        history.append(
            _trade_row(
                str(row.get("coin") or ""),
                _side_from_dir(str(row.get("dir") or "")),
                str(row.get("px") or ""),
                _float(row.get("pnl")),
            )
        )
    gross = long_notional + short_notional
    long_pct = (long_notional / gross * 100) if gross else 0.0
    if gross <= 0:
        bias = "空仓"
    elif long_pct >= 60:
        bias = "偏多"
    elif long_pct <= 40:
        bias = "偏空"
    else:
        bias = "均衡"
    if stats.get("waiting"):
        risk = "—"
    elif dd < 10:
        risk = "低"
    elif dd < 25:
        risk = "中"
    else:
        risk = "高"
    coins = []
    for row in positions + history:
        if row["coin"] and row["coin"] not in coins:
            coins.append(row["coin"])
        if len(coins) == 3:
            break
    return {
        "id": str(leader.id),
        "name": name,
        "address": leader.address,
        "win": round(win, 1),
        "wins": wins,
        "total": total,
        "roi": round(roi, 1),
        "pnl": round(pnl, 2),
        "dd": round(dd, 1),
        "waiting": bool(stats.get("waiting")),
        "positions": positions,
        "history": history,
        "curve": [point[1] for point in curve if isinstance(point, (list, tuple)) and len(point) > 1],
        "longPct": round(long_pct, 1),
        "coins": " · ".join(coins) if coins else "—",
        "bias": bias,
        "risk": risk,
    }


def wallet_cards(*, force: bool = False) -> list[dict]:
    ensure_leaders()
    rev = wallet_rev()
    now = time.monotonic()
    with _CARD_LOCK:
        hit = (
            not force
            and _CARD_CACHE["rows"]
            and _CARD_CACHE["rev"] == rev
            and now - float(_CARD_CACHE["at"]) < _CARD_TTL
        )
        if hit:
            return list(_CARD_CACHE["rows"])
    rows = [_card(leader) for leader in Leader.objects.filter(enabled=True).select_related("snapshot").order_by("id")]
    with _CARD_LOCK:
        _CARD_CACHE["rev"] = rev
        _CARD_CACHE["at"] = now
        _CARD_CACHE["rows"] = rows
    return list(rows)
