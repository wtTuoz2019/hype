import json
from datetime import datetime

from django.utils import timezone

from leader_stats import collect_leader

from .charts import pnl_svg
from .models import Leader, LeaderSnapshot

PERIOD_KEYS = ("24h", "7d", "30d", "all")

DEFAULT_LEADER = ("主号", "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1")


def ensure_leaders() -> None:
    """只在库里还没有钱包时导入一次。之后以页面上的名单为准。"""
    if Leader.objects.exists():
        return
    try:
        from wallet_store import load_wallets

        rows = load_wallets()
    except Exception:
        rows = []
    if not rows:
        rows = [{"name": DEFAULT_LEADER[0], "address": DEFAULT_LEADER[1], "enabled": True}]
    for row in rows:
        Leader.objects.get_or_create(
            address=row["address"],
            defaults={"name": row.get("name") or "", "enabled": bool(row.get("enabled", True))},
        )


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


def _pack_period(period: dict) -> dict:
    pnl = period.get("pnl") or 0
    curve = period.get("curve") or []
    try:
        pnl_up = float(pnl or 0) >= 0
    except (TypeError, ValueError):
        pnl_up = True
    return {
        "pnl": _money(pnl),
        "pnl_up": pnl_up,
        "roi": _pct(period.get("roi")),
        "equity": _money(period.get("equity")).lstrip("+"),
        "volume": _money(period.get("volume")).lstrip("+"),
        "chart": pnl_svg(curve, width=720, height=200),
        "spark": pnl_svg(curve, width=240, height=72),
    }


def _when_text(row: dict) -> str:
    if row.get("when"):
        return str(row["when"])
    try:
        return datetime.fromtimestamp(int(row.get("time") or 0) / 1000).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "-"


def present(payload: dict) -> dict:
    """把曲线和金额画成页面直接能用的结果。只在后台刷新时调用。"""
    periods = payload.get("periods") or {}
    payload["shown"] = {key: _pack_period(periods.get(key) or {}) for key in PERIOD_KEYS}
    closes = []
    for row in payload.get("closes") or []:
        pnl = row.get("pnl") or 0
        try:
            pnl_up = float(pnl or 0) >= 0
        except (TypeError, ValueError):
            pnl_up = True
        closes.append(
            {
                "when": _when_text(row),
                "coin": row.get("coin") or "",
                "dir": row.get("dir") or "",
                "px": row.get("px") or "",
                "sz": row.get("sz") or "",
                "pnl_text": _money(pnl),
                "pnl_up": pnl_up,
            }
        )
    payload["shown_closes"] = closes
    upnl = payload.get("upnl") or 0
    try:
        payload["shown_upnl_up"] = float(upnl or 0) >= 0
    except (TypeError, ValueError):
        payload["shown_upnl_up"] = True
    payload["shown_upnl"] = _money(upnl)
    win = payload.get("win_rate")
    payload["shown_win"] = f"{float(win) * 100:.1f}%" if win is not None else "-"
    return payload


def _snapshot(leader: Leader) -> LeaderSnapshot | None:
    try:
        return leader.snapshot
    except LeaderSnapshot.DoesNotExist:
        return None


def leader_view(leader: Leader, full: bool = False) -> dict:
    """只读缓存。full 留给调用方兼容，打开页面不再打 Hyperliquid。"""
    del full
    snap = _snapshot(leader)
    if snap is None:
        return {"waiting": True, "periods": {}, "positions": [], "closes": [], "error": ""}
    try:
        data = json.loads(snap.payload)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("periods", {})
    data["waiting"] = False
    data["updated"] = timezone.localtime(snap.fetched_at).strftime("%m-%d %H:%M")
    return data


def refresh_leader(leader: Leader, full: bool) -> None:
    snap = LeaderSnapshot.objects.filter(leader=leader).first()
    previous: dict = {}
    if snap:
        try:
            loaded = json.loads(snap.payload)
            if isinstance(loaded, dict):
                previous = loaded
        except json.JSONDecodeError:
            previous = {}
    payload = present(collect_leader(leader.address, full, previous))
    LeaderSnapshot.objects.update_or_create(
        leader=leader,
        defaults={
            "payload": json.dumps(payload, ensure_ascii=False),
            "full": full or bool(snap and snap.full),
        },
    )
