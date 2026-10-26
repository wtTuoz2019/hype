#!/usr/bin/env python3
"""带单钱包的近期收益、曲线和持仓。页面只读缓存，刷新在后台做。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from wallet_store import DB_PATH, init_db
from watch_wallet import post_info

CACHE_MS = 120_000
DEX_TTL_SEC = 600.0
PERIODS = (
    ("24h", "perpDay", "24小时"),
    ("7d", "perpWeek", "7日"),
    ("30d", "perpMonth", "30日"),
    ("all", "perpAllTime", "全部"),
)


def _connect() -> sqlite3.Connection:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leader_cache (
            address TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_ms INTEGER NOT NULL,
            full INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


_dex_lock = threading.Lock()
_dex_cache: dict = {"at": 0.0, "names": [""]}
_hot_lock = threading.Lock()
_hot_dex: dict[str, tuple[float, list[str]]] = {}


def dex_names(force: bool = False) -> list[str]:
    now = time.time()
    with _dex_lock:
        cached = list(_dex_cache["names"])
        fresh = now - float(_dex_cache["at"]) < DEX_TTL_SEC and cached
    if fresh and not force:
        return cached
    try:
        raw = post_info({"type": "perpDexs"}, timeout=15)
    except Exception:
        return cached or [""]
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not item:
                names.append("")
            elif isinstance(item, dict):
                names.append(str(item.get("name") or ""))
            else:
                names.append(str(item))
    if "" not in names:
        names.insert(0, "")
    with _dex_lock:
        _dex_cache["at"] = time.time()
        _dex_cache["names"] = names
    return list(names)


def _one_state(address: str, name: str) -> tuple[str, dict | None]:
    body: dict = {"type": "clearinghouseState", "user": address}
    if name:
        body["dex"] = name
    try:
        state = post_info(body, timeout=12)
    except Exception:
        return name, None
    if not isinstance(state, dict):
        return name, None
    return name, state


def _hot(state: dict) -> bool:
    margin = state.get("marginSummary") or {}
    try:
        if abs(float(margin.get("accountValue") or 0)) > 1e-8:
            return True
    except (TypeError, ValueError):
        pass
    for item in state.get("assetPositions") or []:
        pos = (item or {}).get("position") or {}
        try:
            if abs(float(pos.get("szi") or 0)) > 1e-12:
                return True
        except (TypeError, ValueError):
            continue
    return False


def clearinghouses(address: str) -> list[tuple[str, dict]]:
    """并发读各分所持仓。没钱也没仓的分所十分钟内不再查。"""
    address = address.lower()
    names = dex_names()
    now = time.time()
    with _hot_lock:
        cached = _hot_dex.get(address)
    full = cached is None or now - cached[0] >= DEX_TTL_SEC
    if full:
        targets = names
    else:
        known = set(names)
        targets = [name for name in cached[1] if name in known or name == ""]
    if "" not in targets:
        targets = ["", *targets]
    workers = min(8, len(targets))
    found: list[tuple[str, dict]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, state in pool.map(lambda name: _one_state(address, name), targets):
            if state is not None:
                found.append((name, state))
    if full and found:
        hot = [name for name, state in found if name == "" or _hot(state)]
        if "" not in hot:
            hot.insert(0, "")
        with _hot_lock:
            _hot_dex[address] = (time.time(), hot)
    return found


def _series(rows) -> list[list[float]]:
    out: list[list[float]] = []
    for item in rows or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            out.append([float(item[0]), float(item[1])])
        except (TypeError, ValueError):
            continue
    return out


def _downsample(points: list[list[float]], limit: int = 80) -> list[list[float]]:
    if len(points) <= limit:
        return points
    picked = [points[int(i * (len(points) - 1) / (limit - 1))] for i in range(limit)]
    return picked


def _period(block: dict) -> dict:
    pnl = _series(block.get("pnlHistory"))
    account = _series(block.get("accountValueHistory"))
    last_pnl = pnl[-1][1] if pnl else 0.0
    start_equity = account[0][1] if account else 0.0
    end_equity = account[-1][1] if account else 0.0
    base = start_equity
    if base <= 1e-9:
        base = end_equity - last_pnl
    roi = last_pnl / base if abs(base) > 1e-9 else 0.0
    try:
        volume = float(block.get("vlm") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return {
        "pnl": last_pnl,
        "roi": roi,
        "equity": end_equity,
        "volume": volume,
        "curve": _downsample(pnl),
    }


def _positions(address: str) -> list[dict]:
    rows: list[dict] = []
    for name, state in clearinghouses(address):
        for item in state.get("assetPositions") or []:
            pos = (item or {}).get("position") or {}
            try:
                size = float(pos.get("szi") or 0)
            except (TypeError, ValueError):
                continue
            if abs(size) <= 1e-12:
                continue
            try:
                upnl = float(pos.get("unrealizedPnl") or 0)
            except (TypeError, ValueError):
                upnl = 0.0
            rows.append(
                {
                    "coin": str(pos.get("coin") or ""),
                    "szi": size,
                    "entry": str(pos.get("entryPx") or ""),
                    "upnl": upnl,
                    "dex": name or "main",
                }
            )
    rows.sort(key=lambda row: abs(row["upnl"]), reverse=True)
    return rows


def _closes(address: str) -> tuple[list[dict], float | None, int]:
    try:
        fills = post_info({"type": "userFills", "user": address}, timeout=20)
    except Exception:
        return [], None, 0
    if not isinstance(fills, list):
        return [], None, 0
    closes: list[dict] = []
    wins = 0
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        coin = str(fill.get("coin") or "")
        direction = str(fill.get("dir") or "")
        if coin.startswith("@") or "/" in coin or direction in ("Buy", "Sell"):
            continue
        if not (direction.startswith("Close") or ">" in direction):
            continue
        try:
            pnl = float(fill.get("closedPnl") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl > 0:
            wins += 1
        when_ms = int(fill.get("time") or 0)
        closes.append(
            {
                "time": when_ms,
                "when": time.strftime("%m-%d %H:%M:%S", time.localtime(when_ms / 1000)) if when_ms else "-",
                "coin": coin,
                "dir": direction,
                "px": str(fill.get("px") or ""),
                "sz": str(fill.get("sz") or ""),
                "pnl": pnl,
            }
        )
    closes.sort(key=lambda row: row["time"], reverse=True)
    rate = (wins / len(closes)) if closes else None
    return closes[:12], rate, len(closes)


def _read(address: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload, fetched_ms, full FROM leader_cache WHERE address = ?",
            (address,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    payload["fetched_ms"] = int(row["fetched_ms"])
    payload["full"] = bool(row["full"])
    return payload


def _write(address: str, payload: dict, full: bool) -> dict:
    now = int(time.time() * 1000)
    payload = dict(payload)
    payload["fetched_ms"] = now
    payload["full"] = full
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO leader_cache (address, payload, fetched_ms, full)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                payload = excluded.payload,
                fetched_ms = excluded.fetched_ms,
                full = excluded.full
            """,
            (address, json.dumps(payload, ensure_ascii=False), now, 1 if full else 0),
        )
        conn.commit()
    finally:
        conn.close()
    return payload


def collect_leader(address: str, full: bool, previous: dict | None = None) -> dict:
    """向 Hyperliquid 拉永续收益。不写数据库，给网页缓存用。"""
    previous = previous or {}
    try:
        raw = post_info({"type": "portfolio", "user": address}, timeout=20)
    except Exception as exc:
        if previous:
            previous["error"] = str(exc)
            return previous
        return {"error": str(exc), "periods": {}, "positions": [], "closes": []}
    by_name = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict):
                by_name[item[0]] = item[1]
    periods = {}
    for key, source, _label in PERIODS:
        periods[key] = _period(by_name.get(source) or {})
    payload = {
        "periods": periods,
        "positions": previous.get("positions") or [],
        "closes": previous.get("closes") or [],
        "win_rate": previous.get("win_rate"),
        "close_count": previous.get("close_count") or 0,
        "upnl": previous.get("upnl") or 0.0,
    }
    if full:
        positions = _positions(address)
        closes, win_rate, close_count = _closes(address)
        payload["positions"] = positions
        payload["closes"] = closes
        payload["win_rate"] = win_rate
        payload["close_count"] = close_count
        payload["upnl"] = sum(row["upnl"] for row in positions)
    return payload


def refresh_leader(address: str, full: bool) -> dict:
    """拉永续收益曲线。full 时连持仓和近期平仓一起拉。"""
    cached = _read(address) or {}
    payload = collect_leader(address, full, cached)
    if payload.get("error") and not payload.get("periods"):
        return payload
    return _write(address, payload, full or bool(cached.get("full")))


def get_leader(address: str, full: bool = False, max_age_ms: int = CACHE_MS) -> dict:
    cached = _read(address)
    fresh = cached is not None and int(time.time() * 1000) - int(cached.get("fetched_ms") or 0) < max_age_ms
    if fresh and (not full or cached.get("full")):
        return cached
    return refresh_leader(address, full)
