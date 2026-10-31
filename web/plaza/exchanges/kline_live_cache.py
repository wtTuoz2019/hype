# -*- coding: utf-8 -*-
"""实盘 K 线增量缓存（Binance / Gate / OKX 共用）。

首拉全量后每分钟只补最近几根，按当前策略窗口裁旧，长时间未访问的流整段丢掉。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

HARD_CAP = 3000
MAX_IDLE_SEC = 30 * 60
MIN_WARM_BARS = 200
TAIL_BARS = 10

_rows: Dict[str, List[Dict[str, Any]]] = {}
_need: Dict[str, int] = {}
_at: Dict[str, float] = {}


def make_key(exchange: str, symbol: str, interval: str) -> str:
    return f"{str(exchange).strip().lower()}:{str(symbol).strip().upper()}:{str(interval).strip()}"


def trim_klines(rows: List[Dict[str, Any]], keep: int) -> List[Dict[str, Any]]:
    keep = max(1, int(keep))
    if len(rows) <= keep:
        return rows
    return rows[-keep:]


def merge_klines(*parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_ts: Dict[Any, Dict[str, Any]] = {}
    for part in parts:
        for r in part:
            by_ts[r["timestamp"]] = r
    return [by_ts[k] for k in sorted(by_ts)]


def prune_kline_live_cache(
    *,
    prefix: Optional[str] = None,
    max_bars_per_key: Optional[int] = None,
    max_idle_sec: Optional[float] = None,
    drop_empty: bool = True,
    now: Optional[float] = None,
) -> int:
    """裁掉超出窗口的旧 K；丢掉空流 / 闲置流。返回删除根数。"""
    hard = int(max_bars_per_key) if max_bars_per_key is not None else HARD_CAP
    hard = max(1, hard)
    idle = float(MAX_IDLE_SEC if max_idle_sec is None else max_idle_sec)
    ts_now = float(time.time() if now is None else now)
    pre = str(prefix or "")
    removed = 0
    for key in list(_rows.keys()):
        if pre and not key.startswith(pre):
            continue
        last_at = float(_at.get(key) or 0.0)
        if idle > 0 and last_at > 0 and (ts_now - last_at) >= idle:
            removed += len(_rows.get(key) or [])
            _rows.pop(key, None)
            _need.pop(key, None)
            _at.pop(key, None)
            continue
        rows = _rows.get(key) or []
        if not rows:
            if drop_empty:
                _rows.pop(key, None)
                _need.pop(key, None)
                _at.pop(key, None)
            continue
        need = int(_need.get(key) or hard)
        keep = min(hard, max(need, 1))
        if len(rows) > keep:
            removed += len(rows) - keep
            _rows[key] = rows[-keep:]
    return removed


def try_incremental(
    key: str,
    need: int,
    *,
    fetch_tail: Callable[[], List[Dict[str, Any]]],
    fetch_full: Callable[[], List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """缓存够暖时只补最近若干根；不够则返回 None，由调用方全量拉取后再 store。"""
    need_n = max(1, int(need))
    _need[key] = need_n
    _at[key] = time.time()
    cached = list(_rows.get(key) or [])
    if cached:
        cached = trim_klines(cached, min(need_n, HARD_CAP))
        _rows[key] = cached
    if len(cached) < min(need_n, MIN_WARM_BARS):
        return None
    fresh = fetch_tail()
    merged = trim_klines(merge_klines(cached, fresh), need_n)
    if len(merged) < int(need_n * 0.85):
        merged = trim_klines(fetch_full(), need_n)
    _rows[key] = merged
    prune_kline_live_cache()
    return list(merged)


def store(key: str, rows: List[Dict[str, Any]], need: int) -> List[Dict[str, Any]]:
    need_n = max(1, int(need))
    kept = trim_klines(list(rows), need_n)
    _rows[key] = kept
    _need[key] = need_n
    _at[key] = time.time()
    prune_kline_live_cache()
    return list(kept)
