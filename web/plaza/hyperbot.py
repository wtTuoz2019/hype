"""从 Hyperbot 热门地址里同步近一月收益为正的钱包。页面不请求这个接口。"""

from __future__ import annotations

import json
import ssl
import urllib.request

import certifi

from wallet_store import norm_address

from .models import Leader

HOT_URL = "https://hyperbot.network/api/leaderboard/smart/hot?lang=en&pnlList=false"
SOURCE = "hyperbot"


def fetch_hot(timeout: float = 20) -> list[dict]:
    request = urllib.request.Request(HOT_URL, headers={"User-Agent": "hype-sync"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode())
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("热门地址返回异常")
    return [row for row in rows if isinstance(row, dict)]


def profitable_month(rows: list[dict]) -> dict[str, str]:
    """热门接口的 realizedPnl 就是页面上的 Net PnL(1M)。只留大于 0 的。"""
    picked: dict[str, str] = {}
    for row in rows:
        try:
            pnl = float(row.get("realizedPnl") or 0)
        except (TypeError, ValueError):
            continue
        if pnl <= 0:
            continue
        try:
            address = norm_address(str(row.get("address") or ""))
        except ValueError:
            continue
        picked[address] = str(row.get("remark") or "").strip()[:40]
    return picked


def sync_hot(rows: list[dict]) -> dict[str, int]:
    """只增改 source=hyperbot 的地址。手工添加的钱包保持原样，也可以删除。"""
    wanted = profitable_month(rows)
    created = 0
    renamed = 0
    for address, name in wanted.items():
        leader = Leader.objects.filter(address=address).first()
        if leader is None:
            Leader.objects.create(address=address, name=name, enabled=True, source=SOURCE)
            created += 1
            continue
        if leader.source != SOURCE or not leader.enabled:
            continue
        if name and leader.name != name:
            leader.name = name
            leader.save(update_fields=["name"])
            renamed += 1
    disabled = (
        Leader.objects.filter(source=SOURCE, enabled=True)
        .exclude(address__in=wanted.keys())
        .update(enabled=False)
    )
    return {"created": created, "renamed": renamed, "disabled": disabled, "kept": len(wanted)}
