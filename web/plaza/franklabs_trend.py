"""把 franklabs 同步 JSON 映射成 /api/trend/ 同款结构，分析侧零改动。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone as dj_tz

# action+side → 分析侧认识的中文 label
_OPEN_MAP = {
    ("open", "long"): "开多",
    ("open", "short"): "开空",
    ("add", "long"): "加多",
    ("add", "short"): "加空",
}
_CLOSE_MAP = {
    ("close", "long"): "平多",
    ("close", "short"): "平空",
    ("reduce", "long"): "减多",
    ("reduce", "short"): "减空",
}


def _f(value: Any, default: float = 0.0) -> float:
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


def franklabs_json_paths() -> list[Path]:
    """候选 latest.json 路径（本机 / 服务器部署）。"""
    env = (getattr(settings, "FRANKLABS_TREND_JSON", "") or "").strip()
    roots: list[Path] = []
    if env:
        roots.append(Path(env))
    base = Path(settings.BASE_DIR)
    # /www/wwwroot/hype/web → ../tools/data/...
    roots.append(base.parent / "tools" / "data" / "franklabs_trader_live" / "latest.json")
    # 本机 future_test/tools/...
    roots.append(base.parents[1] / "tools" / "data" / "franklabs_trader_live" / "latest.json")
    return roots


def load_franklabs_payload(*, max_age_sec: int = 6 * 3600) -> dict | None:
    now = datetime.now(timezone.utc)
    for path in franklabs_json_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            continue
        fetched = str(data.get("fetched_at") or "")
        age_ok = True
        if fetched:
            try:
                ts = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_ok = (now - ts.astimezone(timezone.utc)).total_seconds() <= max_age_sec
            except ValueError:
                age_ok = True
        if not age_ok:
            continue
        data["_path"] = str(path)
        return data
    return None


def _event_label(action: str, side: str) -> str:
    key = (str(action or "").lower(), str(side or "").lower())
    return _OPEN_MAP.get(key) or _CLOSE_MAP.get(key) or ""


def _event_notional(row: dict) -> float:
    for key in ("change_value", "position_value_after", "value"):
        n = abs(_f(row.get(key)))
        if n > 0:
            return n
    # 无名义时用 1，保证等权钱包流仍能计分
    return 1.0


def _parse_ts(row: dict) -> datetime | None:
    raw = row.get("message_ts") or ""
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except ValueError:
        return None


def _pack_open_close(row: dict) -> dict | None:
    action = str(row.get("action") or "").lower()
    side = str(row.get("side") or "").lower()
    label = _event_label(action, side)
    if not label:
        return None
    coin = str(row.get("symbol") or row.get("source_symbol") or "").upper()
    if not coin:
        return None
    ts = _parse_ts(row)
    time_ms = int(ts.timestamp() * 1000) if ts else 0
    when = dj_tz.localtime(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
    leader = str(row.get("trader_name") or row.get("trader_id") or "")
    return {
        "id": str(row.get("event_id") or ""),
        "leader_id": str(row.get("trader_id") or ""),
        "leader": leader,
        "address": str(row.get("trader_id") or ""),
        "coin": coin,
        "label": label,
        "side": side,
        "px": str(row.get("execution_price") or row.get("position_entry_price") or ""),
        "delta_szi": str(row.get("change_value") or ""),
        "notional": round(_event_notional(row), 4),
        "time_ms": time_ms,
        "when": when,
        "source": "franklabs",
    }


def _latest_snapshots(items: list[dict]) -> dict[str, dict]:
    """每位交易员取最新一条 snapshot。"""
    best: dict[str, dict] = {}
    for row in items:
        if str(row.get("action") or "").lower() != "snapshot":
            continue
        tid = str(row.get("trader_id") or "")
        if not tid:
            continue
        ts = _parse_ts(row)
        prev = best.get(tid)
        if prev is None:
            best[tid] = row
            continue
        pts = _parse_ts(prev)
        if ts and (pts is None or ts >= pts):
            best[tid] = row
    return best


def build_trend_from_franklabs(raw: dict, *, top: int = 10) -> dict:
    items = [row for row in (raw.get("items") or []) if isinstance(row, dict)]
    directory = raw.get("directory") or {}
    traders_meta = {
        str(t.get("id")): t
        for t in (directory.get("traders") or [])
        if isinstance(t, dict) and t.get("id")
    }

    snaps = _latest_snapshots(items)
    # 按目录 events_count 排序取 top；无目录则全要
    ranked_ids = sorted(
        snaps.keys(),
        key=lambda tid: int((traders_meta.get(tid) or {}).get("events_count") or 0),
        reverse=True,
    )
    if top > 0:
        ranked_ids = ranked_ids[: max(1, min(int(top), 50))]
    # 没有 snapshot 的交易员：仍可用今日开平仓，不进 wallets 持仓

    wallets = []
    coin_long: dict[str, float] = defaultdict(float)
    coin_short: dict[str, float] = defaultdict(float)
    coin_long_w: dict[str, set[str]] = defaultdict(set)
    coin_short_w: dict[str, set[str]] = defaultdict(set)
    pos_long = pos_short = 0.0

    for tid in ranked_ids:
        snap = snaps[tid]
        meta = traders_meta.get(tid) or {}
        name = str(snap.get("trader_name") or meta.get("name") or tid)
        positions = []
        long_n = short_n = 0.0
        for p in snap.get("positions") or []:
            if not isinstance(p, dict):
                continue
            coin = str(p.get("symbol") or "").upper()
            if not coin:
                continue
            side = str(p.get("side") or "").lower()
            if side not in ("long", "short"):
                continue
            value = abs(_f(p.get("value")))
            if value <= 0:
                value = abs(_f(p.get("entry_price")))
            pos = {
                "coin": coin,
                "side": side,
                "szi": 1.0 if side == "long" else -1.0,
                "entry": str(p.get("entry_price") or ""),
                "mark": "",
                "value": round(value, 4),
                "upnl": round(_f(p.get("unrealized_pnl")), 4),
                "roe": round(_f(p.get("pnl_pct")) / 100.0, 6),
                "lev": str(p.get("leverage") or ""),
            }
            positions.append(pos)
            if side == "long":
                long_n += value
                coin_long[coin] += value
                coin_long_w[coin].add(tid)
            else:
                short_n += value
                coin_short[coin] += value
                coin_short_w[coin].add(tid)
        gross = long_n + short_n
        long_pct = (long_n / gross * 100) if gross else 0.0
        pos_long += long_n
        pos_short += short_n
        wallets.append(
            {
                "id": tid,
                "name": name,
                "address": tid,
                "source": "franklabs",
                "roi_30d": 0.0,
                "roi_pct": 0.0,
                "win_rate": None,
                "long_pct": round(long_pct, 1),
                "bias": _bias_word(_score(long_n, short_n)),
                "position_value": round(gross, 4),
                "positions": positions,
            }
        )

    # 若没有任何 snapshot，用「有今日动作的交易员」占位，持仓空，流仍可用
    if not wallets:
        active = []
        for row in items:
            tid = str(row.get("trader_id") or "")
            if tid and tid not in active:
                active.append(tid)
        for tid in active[: max(1, min(int(top), 50))]:
            meta = traders_meta.get(tid) or {}
            wallets.append(
                {
                    "id": tid,
                    "name": str(meta.get("name") or tid),
                    "address": tid,
                    "source": "franklabs",
                    "roi_30d": 0.0,
                    "roi_pct": 0.0,
                    "win_rate": None,
                    "long_pct": 0.0,
                    "bias": "neutral",
                    "position_value": 0.0,
                    "positions": [],
                }
            )

    coins = []
    for coin in sorted(set(coin_long) | set(coin_short)):
        lv, sv = coin_long[coin], coin_short[coin]
        sc = _score(lv, sv)
        coins.append(
            {
                "coin": coin,
                "long_notional": round(lv, 4),
                "short_notional": round(sv, 4),
                "long_wallets": len(coin_long_w[coin]),
                "short_wallets": len(coin_short_w[coin]),
                "score": round(sc, 4),
                "bias": _bias_word(sc),
            }
        )
    coins.sort(
        key=lambda row: abs(row["score"]) * (row["long_notional"] + row["short_notional"]),
        reverse=True,
    )

    start = dj_tz.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    wallet_ids = {str(w["id"]) for w in wallets}
    opens: list[dict] = []
    closes: list[dict] = []
    flow_long = flow_short = 0.0
    for row in items:
        tid = str(row.get("trader_id") or "")
        if wallet_ids and tid not in wallet_ids and snaps:
            # 有持仓榜时，当日流也限制在榜内；无 snapshot 则用全员流
            continue
        ts = _parse_ts(row)
        if ts is None:
            continue
        if dj_tz.localtime(ts) < start:
            continue
        packed = _pack_open_close(row)
        if not packed:
            continue
        label = packed["label"]
        if label in ("开多", "开空", "加多", "加空"):
            opens.append(packed)
            if "空" in label:
                flow_short += packed["notional"]
            else:
                flow_long += packed["notional"]
        elif label in ("平多", "平空", "减多", "减空"):
            closes.append(packed)

    opens.sort(key=lambda r: r.get("time_ms") or 0, reverse=True)
    closes.sort(key=lambda r: r.get("time_ms") or 0, reverse=True)

    pos_score = _score(pos_long, pos_short)
    flow_score = _score(flow_long, flow_short)
    pos_bias = _bias_word(pos_score)
    flow_bias = _bias_word(flow_score)
    agree = pos_bias == flow_bias and pos_bias != "neutral"
    n = len(wallets)
    if agree:
        hint = f"franklabs前{n}仓位与当日开仓同向偏{'多' if pos_bias == 'long' else '空'}"
    elif pos_bias == "neutral" and flow_bias == "neutral":
        hint = f"franklabs前{n}仓位与当日开仓均不明显"
    else:
        hint = f"franklabs仓位偏{pos_bias}，当日开仓流偏{flow_bias}（方向不一致，谨慎）"

    now = dj_tz.localtime()
    return {
        "as_of": now.isoformat(),
        "day": now.strftime("%Y-%m-%d"),
        "params": {
            "top": top,
            "order": "franklabs",
            "source": "franklabs",
            "franklabs_path": raw.get("_path") or "",
            "franklabs_fetched_at": raw.get("fetched_at") or "",
            "franklabs_count": int(raw.get("count") or len(items)),
        },
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
            "source": "franklabs",
        },
    }
