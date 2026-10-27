"""把永续信号写进站点数据库，并在启动时补最近一批。"""

from __future__ import annotations

from django.db import close_old_connections

from signals import is_perp_fill, signal_from_fills
from watch_wallet import post_info

from .models import Leader, TradeSignal


def save_signal(signal: dict) -> None:
    close_old_connections()
    address = str(signal.get("leader") or "").lower()
    leader = Leader.objects.filter(address=address).first()
    if leader is None or not signal.get("id"):
        return
    row, created = TradeSignal.objects.get_or_create(
        sig_id=str(signal["id"])[:180],
        defaults={
            "leader": leader,
            "time_ms": int(signal.get("time") or 0),
            "coin": str(signal.get("coin") or "")[:40],
            "action": str(signal.get("action") or "")[:16],
            "side": str(signal.get("side") or "")[:8],
            "label": str(signal.get("label") or "")[:8],
            "prev_szi": str(signal.get("prev_szi") or "")[:40],
            "szi": str(signal.get("szi") or "")[:40],
            "delta_szi": str(signal.get("delta_szi") or "")[:40],
            "px": str(signal.get("px") or "")[:40],
        },
    )
    if created:
        from plaza.copy_run import enqueue_signal

        enqueue_signal(row.id)


def group_fills(fills: list[dict]) -> list[list[dict]]:
    open_groups: dict[str, list[dict]] = {}
    finished: list[list[dict]] = []
    ordered = sorted(
        (fill for fill in fills if isinstance(fill, dict) and is_perp_fill(fill)),
        key=lambda row: (int(row.get("time") or 0), int(row.get("tid") or 0)),
    )
    for fill in ordered:
        coin = str(fill.get("coin") or "")
        current = open_groups.get(coin)
        if current is None:
            open_groups[coin] = [fill]
            continue
        if int(fill.get("time") or 0) - int(current[-1].get("time") or 0) <= 3000:
            current.append(fill)
            continue
        finished.append(current)
        open_groups[coin] = [fill]
    finished.extend(open_groups.values())
    return finished


def backfill_leader(leader: Leader) -> int:
    fills = post_info({"type": "userFills", "user": leader.address}, timeout=20)
    if not isinstance(fills, list):
        return 0
    rows = []
    for group in group_fills(fills):
        signal = signal_from_fills(group)
        if signal is None:
            continue
        signal["leader"] = leader.address
        signal["id"] = (
            f"sig:{leader.address}:{signal['coin']}:{signal['tids'][0]}:{signal['tids'][-1]}:{signal['action']}"
        )
        rows.append(
            TradeSignal(
                leader=leader,
                sig_id=str(signal["id"])[:180],
                time_ms=int(signal.get("time") or 0),
                coin=str(signal.get("coin") or "")[:40],
                action=str(signal.get("action") or "")[:16],
                side=str(signal.get("side") or "")[:8],
                label=str(signal.get("label") or "")[:8],
                prev_szi=str(signal.get("prev_szi") or "")[:40],
                szi=str(signal.get("szi") or "")[:40],
                delta_szi=str(signal.get("delta_szi") or "")[:40],
                px=str(signal.get("px") or "")[:40],
            )
        )
    if not rows:
        return 0
    TradeSignal.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def backfill_signals() -> int:
    close_old_connections()
    if TradeSignal.objects.exists():
        return 0
    created = 0
    for leader in Leader.objects.filter(enabled=True):
        try:
            created += backfill_leader(leader)
        except Exception:
            continue
    return created
