"""聪明钱趋势参考接口：前 N 钱包持仓 + 当日开/平仓，给外部 Python 分析用。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from plaza.hyperbot import roi_ok
from plaza.models import Leader, TradeSignal
from plaza.services import ensure_leaders, leader_view

OPEN_LABELS = ("开多", "开空", "加多", "加空")
CLOSE_LABELS = ("平多", "平空", "减多", "减空")
LONG_LABELS = ("开多", "加多", "减空", "平空", "翻多")
SHORT_LABELS = ("开空", "加空", "减多", "平多", "翻空")


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bias_word(score: float) -> str:
    if score >= 0.2:
        return "long"
    if score <= -0.2:
        return "short"
    return "neutral"


def _score(long_v: float, short_v: float) -> float:
    total = long_v + short_v
    if total <= 0:
        return 0.0
    return (long_v - short_v) / total


def _notional(row: TradeSignal) -> float:
    return abs(_f(row.delta_szi)) * _f(row.px)


def _pick_top(*, top: int, order: str) -> list[tuple[Leader, dict, float, float]]:
    """返回 [(leader, stats, roi, win)]，按 order 降序，最多 top 个。"""
    rows: list[tuple[Leader, dict, float, float]] = []
    for leader in Leader.objects.filter(enabled=True).select_related("snapshot").order_by("id"):
        stats = leader_view(leader)
        period = (stats.get("periods") or {}).get("30d") or {}
        roi = _f(period.get("roi"))
        win = _f(stats.get("win_rate"), -1.0)
        manual = not (leader.source or "").strip()
        waiting = bool(stats.get("waiting"))
        if not manual and (waiting or not roi_ok(roi)):
            continue
        rows.append((leader, stats, roi, win))
    key = (lambda item: item[3]) if order == "win" else (lambda item: item[2])
    rows.sort(key=key, reverse=True)
    return rows[:top]


def _pack_position(row: dict) -> dict:
    size = _f(row.get("szi"))
    value = _f(row.get("value"))
    if value <= 0:
        value = abs(size) * _f(row.get("entry"))
    return {
        "coin": str(row.get("coin") or ""),
        "side": "long" if size >= 0 else "short",
        "szi": size,
        "entry": str(row.get("entry") or ""),
        "mark": str(row.get("mark") or ""),
        "value": round(value, 4),
        "upnl": round(_f(row.get("upnl")), 4),
        "roe": round(_f(row.get("roe")), 6),
        "lev": str(row.get("lev") or ""),
    }


def _pack_signal(row: TradeSignal) -> dict:
    label = row.label or ""
    if label in LONG_LABELS or ("多" in label and "空" not in label):
        side = "long"
    elif label in SHORT_LABELS or "空" in label:
        side = "short"
    else:
        side = ""
    return {
        "id": row.id,
        "leader_id": row.leader_id,
        "leader": row.leader.name or row.leader.address[:10],
        "address": row.leader.address,
        "coin": row.coin,
        "label": label,
        "side": side,
        "px": row.px,
        "delta_szi": row.delta_szi,
        "notional": round(_notional(row), 4),
        "time_ms": row.time_ms,
        "when": datetime.fromtimestamp(row.time_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if row.time_ms
        else "",
    }


def build_trend(*, top: int = 5, order: str = "roi") -> dict:
    ensure_leaders()
    top = max(1, min(int(top), 20))
    order = order if order in ("roi", "win") else "roi"
    picked = _pick_top(top=top, order=order)

    wallets = []
    coin_long: dict[str, float] = defaultdict(float)
    coin_short: dict[str, float] = defaultdict(float)
    coin_long_w: dict[str, set[int]] = defaultdict(set)
    coin_short_w: dict[str, set[int]] = defaultdict(set)
    pos_long = pos_short = 0.0

    for leader, stats, roi, win in picked:
        positions = [_pack_position(row) for row in (stats.get("positions") or []) if row.get("coin")]
        long_n = short_n = 0.0
        for pos in positions:
            if pos["side"] == "long":
                long_n += pos["value"]
                coin_long[pos["coin"]] += pos["value"]
                coin_long_w[pos["coin"]].add(leader.id)
            else:
                short_n += pos["value"]
                coin_short[pos["coin"]] += pos["value"]
                coin_short_w[pos["coin"]].add(leader.id)
        gross = long_n + short_n
        long_pct = (long_n / gross * 100) if gross else 0.0
        pos_long += long_n
        pos_short += short_n
        wallets.append(
            {
                "id": leader.id,
                "name": leader.name or leader.address[:10],
                "address": leader.address,
                "source": leader.source or "",
                "roi_30d": round(roi, 6),
                "roi_pct": round(roi * 100, 2),
                "win_rate": round(win, 4) if win >= 0 else None,
                "long_pct": round(long_pct, 1),
                "bias": _bias_word(_score(long_n, short_n)),
                "position_value": round(gross, 4),
                "positions": positions,
            }
        )

    coins = []
    for coin in sorted(set(coin_long) | set(coin_short)):
        lv, sv = coin_long[coin], coin_short[coin]
        score = _score(lv, sv)
        coins.append(
            {
                "coin": coin,
                "long_notional": round(lv, 4),
                "short_notional": round(sv, 4),
                "long_wallets": len(coin_long_w[coin]),
                "short_wallets": len(coin_short_w[coin]),
                "score": round(score, 4),
                "bias": _bias_word(score),
            }
        )
    coins.sort(key=lambda row: abs(row["score"]) * (row["long_notional"] + row["short_notional"]), reverse=True)

    leader_ids = [w["id"] for w in wallets]
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(start.timestamp() * 1000)
    today_qs = (
        TradeSignal.objects.filter(leader_id__in=leader_ids, time_ms__gte=start_ms)
        .select_related("leader")
        .order_by("-time_ms", "-id")
    )
    opens = []
    closes = []
    flow_long = flow_short = 0.0
    for row in today_qs:
        packed = _pack_signal(row)
        if row.label in OPEN_LABELS:
            opens.append(packed)
            if "空" in row.label:
                flow_short += packed["notional"]
            else:
                flow_long += packed["notional"]
        elif row.label in CLOSE_LABELS or str(row.label).startswith("平") or str(row.label).startswith("减"):
            closes.append(packed)

    pos_score = _score(pos_long, pos_short)
    flow_score = _score(flow_long, flow_short)
    pos_bias = _bias_word(pos_score)
    flow_bias = _bias_word(flow_score)
    agree = pos_bias == flow_bias and pos_bias != "neutral"
    if agree:
        hint = f"前{top}仓位与当日开仓同向偏{'多' if pos_bias == 'long' else '空'}"
    elif pos_bias == "neutral" and flow_bias == "neutral":
        hint = f"前{top}仓位与当日开仓均不明显"
    else:
        hint = f"仓位偏{pos_bias}，当日开仓流偏{flow_bias}（方向不一致，谨慎）"

    now = timezone.localtime()
    return {
        "as_of": now.isoformat(),
        "day": now.strftime("%Y-%m-%d"),
        "params": {"top": top, "order": order},
        "wallets": wallets,
        "coins": coins,
        "today": {
            "opens": opens,
            "closes": closes,
            "open_count": len(opens),
            "close_count": len(closes),
            "long_notional": round(flow_long, 4),
            "short_notional": round(flow_short, 4),
            "score": round(flow_score, 4),
            "bias": flow_bias,
        },
        "summary": {
            "position_long": round(pos_long, 4),
            "position_short": round(pos_short, 4),
            "position_score": round(pos_score, 4),
            "position_bias": pos_bias,
            "flow_bias": flow_bias,
            "flow_score": round(flow_score, 4),
            "agree": agree,
            "hint": hint,
        },
    }


@require_GET
def trend_api(request):
    """GET /api/trend/?top=5&order=roi

    - top: 1..20，默认 5
    - order: roi | win，默认 roi（30 日收益率）
    """
    try:
        top = int(request.GET.get("top") or 5)
    except (TypeError, ValueError):
        top = 5
    order = (request.GET.get("order") or "roi").strip().lower()
    return JsonResponse(build_trend(top=top, order=order))
