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
_TODAY_LOCK = threading.Lock()
_TODAY_CACHE: dict = {}
_TODAY_TTL = 20.0


def front_profile(request):
    """前台会员：Telegram 或 OKX 钱包登录。后台 admin 不算前台会员。"""
    if not request.user.is_authenticated:
        return None
    profile = getattr(request.user, "profile", None)
    if profile is None or not profile.is_front_member:
        return None
    return profile


def is_pro(request) -> bool:
    profile = front_profile(request)
    if profile is not None and profile.is_pro:
        return True
    return bool(request.session.get("tape_pro"))


def subscriptions(request) -> list[str]:
    profile = front_profile(request)
    if profile is not None:
        return [
            str(item)
            for item in AlertSub.objects.filter(user=request.user).values_list("leader_id", flat=True)
        ]
    return [str(item) for item in (request.session.get("tape_subs") or [])]


def set_pro(request, enabled: bool) -> None:
    request.session["tape_pro"] = bool(enabled)
    if not enabled:
        request.session["tape_subs"] = []
    request.session.modified = True
    profile = front_profile(request)
    if profile is not None:
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
    if front_profile(request) is not None:
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
    """Telegram 登录后把这次浏览里的会员和订阅记到账号上。"""
    profile = front_profile(request)
    if profile is None:
        return
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


def _delay_sec(request) -> int:
    return PRO_DELAY if is_pro(request) else FREE_DELAY


def _cutoff_ms(request) -> int:
    return int((timezone.now() - timedelta(seconds=_delay_sec(request))).timestamp() * 1000)


def next_visible_at(request) -> int | None:
    """最新一条仍在延迟窗里的信号，何时可见（unix 秒）。

    必须用最新而非最早：活跃行情下最早一条几乎总贴着 cutoff，
    倒计时会一直停在几秒，无法体现 Pro 1 分钟 / 免费 30 分钟的真实延迟。
    没有排队则 None。
    """
    delay = _delay_sec(request)
    now = timezone.now()
    cutoff_ms = int((now - timedelta(seconds=delay)).timestamp() * 1000)
    row = (
        TradeSignal.objects.filter(leader__enabled=True, time_ms__gt=cutoff_ms)
        .order_by("-time_ms", "-id")
        .only("time_ms")
        .first()
    )
    if row is None:
        return None
    visible = int(row.time_ms / 1000) + delay
    # 已经到期的话前端显示 0，下一轮轮询就会进列表
    return max(visible, int(now.timestamp()))


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


def _today_start_ms() -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def today_stats(request) -> dict:
    cutoff = _cutoff_ms(request)
    # 按分钟桶缓存，避免每 3 秒扫当日全表
    key = (is_pro(request), cutoff // 60_000)
    now = time.monotonic()
    with _TODAY_LOCK:
        hit = _TODAY_CACHE.get(key)
        if hit and now - hit[0] < _TODAY_TTL:
            return dict(hit[1])
    start_ms = _today_start_ms()
    labels = TradeSignal.objects.filter(
        leader__enabled=True,
        time_ms__gte=start_ms,
        time_ms__lte=cutoff,
    ).values_list("label", flat=True)
    long_count = short_count = close_count = 0
    for label in labels:
        if label in ("开多", "加多"):
            long_count += 1
        elif label in ("开空", "加空"):
            short_count += 1
        elif str(label).startswith("平"):
            close_count += 1
    result = {"long": long_count, "short": short_count, "close": close_count}
    with _TODAY_LOCK:
        _TODAY_CACHE[key] = (now, result)
        if len(_TODAY_CACHE) > 8:
            oldest = min(_TODAY_CACHE, key=lambda item: _TODAY_CACHE[item][0])
            _TODAY_CACHE.pop(oldest, None)
    return dict(result)


def today_focus(request, *, limit: int = 3) -> list[dict]:
    """今日已可见的开/加仓里，按名义金额取 TOP。不依赖快讯窗口（避免被加仓刷屏淹没）。"""
    cutoff = _cutoff_ms(request)
    start_ms = _today_start_ms()
    rows = list(
        TradeSignal.objects.select_related("leader")
        .filter(
            leader__enabled=True,
            time_ms__gte=start_ms,
            time_ms__lte=cutoff,
            label__in=("开多", "开空", "加多", "加空"),
        )
        .order_by("-time_ms", "-id")[:400]
    )
    rows.sort(key=_notional, reverse=True)
    out = []
    for row in rows[:limit]:
        item = event_dict(row)
        item["wallet_name"] = row.leader.name or row.leader.address
        out.append(item)
    return out


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


def _when_text(row: dict) -> str:
    if row.get("when"):
        return str(row["when"])
    try:
        when_ms = int(row.get("time") or 0)
    except (TypeError, ValueError):
        return "—"
    if when_ms <= 0:
        return "—"
    try:
        return datetime.fromtimestamp(when_ms / 1000, tz=timezone.get_current_timezone()).strftime(
            "%m-%d %H:%M:%S"
        )
    except (TypeError, ValueError, OSError):
        return "—"


def _trade_row(
    coin: str, side: str, px: str, pnl: float | None, when: str = "—", lev: str = "—"
) -> dict:
    if pnl is None:
        pnl_text, up = "—", True
    else:
        pnl_text, up = _money(pnl), pnl >= 0
    return {
        "coin": coin,
        "side": side,
        "lev": lev or "—",
        "px": px or "—",
        "pnl": pnl_text,
        "up": up,
        "when": when or "—",
    }


_CLOSE_LABELS = ("平多", "平空", "减多", "减空", "翻多", "翻空")


def _when_from_ms(when_ms: int) -> str:
    if when_ms <= 0:
        return "—"
    try:
        return datetime.fromtimestamp(when_ms / 1000, tz=timezone.get_current_timezone()).strftime(
            "%m-%d %H:%M:%S"
        )
    except (TypeError, ValueError, OSError):
        return "—"


def _history_from_signals(leader: Leader, *, limit: int = 40) -> list[dict]:
    """快讯 WebSocket 已入库的平/减/翻，接近实时。"""
    rows = []
    qs = TradeSignal.objects.filter(leader=leader, label__in=_CLOSE_LABELS).order_by("-time_ms", "-id")[:limit]
    for sig in qs:
        rows.append(
            _trade_row(
                str(sig.coin or ""),
                str(sig.label or "—"),
                str(sig.px or "—"),
                None,
                _when_from_ms(int(sig.time_ms or 0)),
            )
        )
    return rows


def _positions_from_signals(leader: Leader, *, scan: int = 300) -> list[dict]:
    """按币种取最新一条信号的 szi，还原当前是否仍持仓。"""
    seen: set[str] = set()
    rows: list[dict] = []
    for sig in TradeSignal.objects.filter(leader=leader).order_by("-time_ms", "-id")[:scan]:
        coin = str(sig.coin or "")
        if not coin or coin in seen:
            continue
        seen.add(coin)
        try:
            szi = float(sig.szi or 0)
        except (TypeError, ValueError):
            continue
        if abs(szi) < 1e-12:
            continue
        rows.append(
            _trade_row(
                coin,
                "开多" if szi >= 0 else "开空",
                str(sig.px or "—"),
                None,
                _when_from_ms(int(sig.time_ms or 0)),
            )
        )
    return rows


def _merge_history(signal_rows: list[dict], snap_rows: list[dict], *, limit: int = 40) -> list[dict]:
    keys = {(row["coin"], row["when"]) for row in signal_rows}
    merged = list(signal_rows)
    for row in snap_rows:
        key = (row["coin"], row["when"])
        if key in keys:
            continue
        keys.add(key)
        merged.append(row)
        if len(merged) >= limit:
            break
    return merged[:limit]


def _merge_positions(signal_rows: list[dict], snap_rows: list[dict]) -> list[dict]:
    """信号决定还持有哪些币；快照有同币种时带上浮动盈亏/开仓价。"""
    if not signal_rows:
        return snap_rows
    by_coin = {row["coin"]: row for row in snap_rows}
    out = []
    for row in signal_rows:
        hit = by_coin.get(row["coin"])
        if hit:
            out.append(
                {
                    **row,
                    "px": hit["px"] if hit.get("px") not in ("", "—") else row["px"],
                    "pnl": hit["pnl"],
                    "up": hit["up"],
                }
            )
        else:
            out.append(row)
    return out


def _card(leader: Leader, *, detail: bool = False) -> dict:
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
            _trade_row(
                str(row.get("coin") or ""),
                "开多" if size >= 0 else "开空",
                str(row.get("entry") or ""),
                _float(row.get("upnl")),
                _when_text(row),
                str(row.get("lev") or "—"),
            )
        )
    history = []
    for row in stats.get("closes") or []:
        history.append(
            _trade_row(
                str(row.get("coin") or ""),
                _side_from_dir(str(row.get("dir") or "")),
                str(row.get("px") or ""),
                _float(row.get("pnl")),
                _when_text(row),
            )
        )
    if detail:
        # 平仓记录可用本地快讯补新；当前持仓必须用链上 clearinghouse，不能用信号反推
        signal_history = _history_from_signals(leader)
        history = _merge_history(signal_history, history)
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
    card = {
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
        "longPct": round(long_pct, 1),
        "coins": " · ".join(coins) if coins else "—",
        "bias": bias,
        "risk": risk,
    }
    if detail:
        card["positions"] = positions
        card["history"] = history
        card["curve"] = [point[1] for point in curve if isinstance(point, (list, tuple)) and len(point) > 1]
    return card


def wallet_cards(*, force: bool = False) -> list[dict]:
    """列表/排行榜用瘦卡片，不含持仓曲线。"""
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
    rows = [
        _card(leader, detail=False)
        for leader in Leader.objects.filter(enabled=True).select_related("snapshot").order_by("id")
    ]
    with _CARD_LOCK:
        _CARD_CACHE["rev"] = rev
        _CARD_CACHE["at"] = now
        _CARD_CACHE["rows"] = rows
    return list(rows)


def wallet_detail(leader_id: str | int) -> dict | None:
    ensure_leaders()
    try:
        pk = int(leader_id)
    except (TypeError, ValueError):
        return None
    leader = Leader.objects.filter(pk=pk, enabled=True).select_related("snapshot").first()
    if leader is None:
        return None
    # 持仓以链上为准：超过约 45 秒就同步全量刷新，避免用旧快照或信号反推出错。
    snap = None
    try:
        snap = leader.snapshot
    except Exception:  # noqa: BLE001
        snap = None
    stale = snap is None or (timezone.now() - snap.fetched_at).total_seconds() >= 45
    if stale:
        try:
            from plaza.services import refresh_leader

            refresh_leader(leader, full=True)
            leader = Leader.objects.filter(pk=pk, enabled=True).select_related("snapshot").first()
            if leader is None:
                return None
            try:
                snap = leader.snapshot
            except Exception:  # noqa: BLE001
                snap = None
        except Exception as exc:  # noqa: BLE001
            print(f"详情同步刷新 {pk} 失败: {exc}", flush=True)
    card = _card(leader, detail=True)
    stamp = ""
    if snap is not None:
        try:
            stamp = timezone.localtime(snap.fetched_at).strftime("%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            stamp = ""
    card["updated"] = stamp
    return card
