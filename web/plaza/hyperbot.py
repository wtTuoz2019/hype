"""从 Hyperbot 热门地址里同步近一月收益为正的钱包。页面不请求这个接口。"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request

import certifi

from wallet_store import norm_address

from .models import Leader

HOT_URL = "https://hyperbot.network/api/leaderboard/smart/hot?lang=en&pnlList=false"
SOURCE = "hyperbot"
# 热门自动启用上限，避免一次同步把监控打满
MAX_HOT = max(10, int(os.environ.get("HYPE_HOT_MAX", "80")))


def fetch_hot(timeout: float = 20) -> list[dict]:
    request = urllib.request.Request(HOT_URL, headers={"User-Agent": "hype-sync"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode())
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("热门地址返回异常")
    return [row for row in rows if isinstance(row, dict)]


def profitable_ranked(rows: list[dict]) -> list[tuple[float, str, str]]:
    """按近一月盈利从高到低。"""
    ranked: list[tuple[float, str, str]] = []
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
        name = str(row.get("remark") or "").strip()[:40]
        ranked.append((pnl, address, name))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def sync_hot(rows: list[dict]) -> dict[str, int]:
    """只增改 source=hyperbot 的地址。手工添加的钱包保持原样，也可以删除。"""
    ranked = profitable_ranked(rows)
    keep = {address for _pnl, address, _name in ranked[:MAX_HOT]}
    created = 0
    renamed = 0
    for pnl, address, name in ranked:
        enable = address in keep
        leader = Leader.objects.filter(address=address).first()
        if leader is None:
            if not enable:
                continue
            Leader.objects.create(address=address, name=name, enabled=True, source=SOURCE)
            created += 1
            continue
        if leader.source != SOURCE:
            continue
        # 已停用的热门地址不自动重新打开
        if not leader.enabled:
            continue
        changed = []
        if name and leader.name != name:
            leader.name = name
            changed.append("name")
            renamed += 1
        if not enable:
            leader.enabled = False
            changed.append("enabled")
        if changed:
            leader.save(update_fields=changed)
    disabled = (
        Leader.objects.filter(source=SOURCE, enabled=True)
        .exclude(address__in=keep)
        .update(enabled=False)
    )
    return {
        "created": created,
        "renamed": renamed,
        "disabled": disabled,
        "kept": len(keep),
        "cap": MAX_HOT,
    }
