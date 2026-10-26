#!/usr/bin/env python3
"""监控名单里的钱包，只在永续仓位变化时发出信号。

不向币安或 Gate 下单。跟单执行留到后面再接这些信号。

同一钱包、同一币种在几秒内的碎成交会并成一条：
开多 / 开空 / 加多 / 加空 / 减多 / 减空 / 平多 / 平空 / 翻多 / 翻空。

用法:
  python3 hype/wallets.py add 0x地址 --name 主号
  python3 hype/signals.py
  python3 hype/signals.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import certifi
import websocket

from wallet_store import STORE_PATH, load_wallets
from watch_wallet import PING_SEC, WS_URL, fmt_time, log, short_addr

ROOT = Path(__file__).resolve().parent
SIGNAL_PATH = ROOT / "data" / "signals.jsonl"
DEBOUNCE_SEC = 3.0
POLL_SEC = 3.0
EQUITY_SEC = 120.0

_EPS = 1e-9

_LABELS = {
    ("open", "long"): "开多",
    ("open", "short"): "开空",
    ("add", "long"): "加多",
    ("add", "short"): "加空",
    ("reduce", "long"): "减多",
    ("reduce", "short"): "减空",
    ("close", "long"): "平多",
    ("close", "short"): "平空",
    ("flip", "long"): "翻多",
    ("flip", "short"): "翻空",
}


def is_perp_fill(fill: dict) -> bool:
    coin = str(fill.get("coin") or "")
    if not coin or coin.startswith("@") or "/" in coin:
        return False
    direction = str(fill.get("dir") or "")
    if direction in ("Buy", "Sell"):
        return False
    return True


def end_szi(fill: dict) -> float:
    start = float(fill.get("startPosition") or 0)
    size = float(fill.get("sz") or 0)
    if fill.get("side") == "B":
        return start + size
    return start - size


def classify(prev: float, end: float) -> tuple[str, str] | None:
    if abs(prev) <= _EPS and abs(end) <= _EPS:
        return None
    if abs(prev) <= _EPS:
        return "open", "long" if end > 0 else "short"
    if abs(end) <= _EPS:
        return "close", "long" if prev > 0 else "short"
    if prev > 0 > end or prev < 0 < end:
        return "flip", "long" if end > 0 else "short"
    grown = abs(end) > abs(prev) + _EPS
    if grown:
        return "add", "long" if end > 0 else "short"
    if abs(end) + _EPS < abs(prev):
        return "reduce", "long" if end > 0 else "short"
    return None


def _fmt_num(value: float) -> str:
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def signal_from_fills(fills: list[dict]) -> dict | None:
    rows = [fill for fill in fills if is_perp_fill(fill)]
    if not rows:
        return None
    rows.sort(key=lambda fill: (int(fill.get("time") or 0), int(fill.get("tid") or 0)))
    prev = float(rows[0].get("startPosition") or 0)
    end = end_szi(rows[-1])
    kind = classify(prev, end)
    if kind is None:
        return None
    action, side = kind
    notional_sz = 0.0
    notional_px = 0.0
    for fill in rows:
        sz = abs(float(fill.get("sz") or 0))
        px = float(fill.get("px") or 0)
        notional_sz += sz
        notional_px += px * sz
    px = notional_px / notional_sz if notional_sz else float(rows[-1].get("px") or 0)
    coin = str(rows[-1].get("coin") or "")
    first_tid = rows[0].get("tid")
    last_tid = rows[-1].get("tid")
    delta = end - prev
    return {
        "id": f"sig:{coin}:{first_tid}:{last_tid}:{action}",
        "time": int(rows[-1].get("time") or 0),
        "coin": coin,
        "base": coin.split(":")[-1],
        "action": action,
        "side": side,
        "label": _LABELS.get((action, side), action),
        "prev_szi": _fmt_num(prev),
        "szi": _fmt_num(end),
        "delta_szi": _fmt_num(delta),
        "px": _fmt_num(px),
        "n_fills": len(rows),
        "tids": [fill.get("tid") for fill in rows],
    }


def leader_equity(address: str) -> float | None:
    from leader_stats import clearinghouses

    total = 0.0
    ok = False
    for _name, state in clearinghouses(address):
        ok = True
        margin = state.get("marginSummary") or {}
        try:
            total += float(margin.get("accountValue") or 0)
        except (TypeError, ValueError):
            continue
    return total if ok else None


class SignalHub:
    def __init__(self, debounce: float, as_json: bool) -> None:
        self.debounce = debounce
        self.as_json = as_json
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.ws: websocket.WebSocketApp | None = None
        self.subscribed: set[str] = set()
        self.enabled: dict[str, dict] = {}
        self.seen_tids: set[str] = set()
        self.bursts: dict[tuple[str, str], list[dict]] = {}
        self.timers: dict[tuple[str, str], threading.Timer] = {}
        self.equity: dict[str, float | None] = {}
        self._fh = None

    def _wallet_map(self) -> dict[str, dict]:
        try:
            from django.apps import apps

            if apps.ready:
                from plaza.models import Leader

                return {
                    row.address: {"id": row.id, "name": row.name, "address": row.address, "enabled": True}
                    for row in Leader.objects.filter(enabled=True)
                }
        except Exception:
            pass
        return {
            row["address"]: row
            for row in load_wallets()
            if row.get("enabled")
        }

    def reload_wallets(self) -> None:
        current = self._wallet_map()
        with self.lock:
            self.enabled = current
            ws = self.ws
        if ws is None:
            return
        self._sync_subs(ws, current)

    def _sync_subs(self, ws: websocket.WebSocketApp, current: dict[str, dict]) -> None:
        with self.lock:
            have = set(self.subscribed)
        for address in sorted(current):
            if address in have:
                continue
            self._send_sub(ws, address, subscribe=True)
        for address in sorted(have - set(current)):
            self._send_sub(ws, address, subscribe=False)

    def _send_sub(self, ws: websocket.WebSocketApp, address: str, subscribe: bool) -> None:
        method = "subscribe" if subscribe else "unsubscribe"
        payload = {
            "method": method,
            "subscription": {
                "type": "userFills",
                "user": address,
                "aggregateByTime": False,
            },
        }
        try:
            ws.send(json.dumps(payload))
        except Exception as exc:
            log(f"{method} 失败 {short_addr(address)}: {exc}")
            return
        with self.lock:
            if subscribe:
                self.subscribed.add(address)
            else:
                self.subscribed.discard(address)
        name = self.enabled.get(address, {}).get("name") or short_addr(address)
        log(("开始监控 " if subscribe else "停止监控 ") + f"{name}  {address}")

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        with self.lock:
            self.ws = ws
            self.subscribed.clear()
            current = dict(self.enabled)
        if not current:
            log(f"名单是空的。先添加钱包，或确认 {STORE_PATH} 里 enabled 为 true")
        self._sync_subs(ws, current)

        def ping() -> None:
            while not self.stop.is_set():
                if self.stop.wait(PING_SEC):
                    break
                try:
                    ws.send(json.dumps({"method": "ping"}))
                except Exception:
                    break

        threading.Thread(target=ping, name="hl-ping", daemon=True).start()

    def _remember_tid(self, fill: dict) -> bool:
        tid = fill.get("tid")
        if tid is None:
            return True
        key = str(tid)
        with self.lock:
            if key in self.seen_tids:
                return False
            self.seen_tids.add(key)
        return True

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        if msg.get("channel") != "userFills":
            return
        data = msg.get("data")
        if not isinstance(data, dict):
            return
        address = str(data.get("user") or "").lower()
        fills = data.get("fills") or []
        if data.get("isSnapshot"):
            for fill in fills:
                if isinstance(fill, dict):
                    self._remember_tid(fill)
            log(f"跳过快照 {len(fills)} 条  {short_addr(address)}")
            return
        with self.lock:
            known = address in self.enabled
        if not known or not address:
            return
        for fill in fills:
            if isinstance(fill, dict):
                self._push_fill(address, fill)

    def _push_fill(self, address: str, fill: dict) -> None:
        if not is_perp_fill(fill):
            return
        if not self._remember_tid(fill):
            return
        coin = str(fill.get("coin") or "")
        key = (address, coin)
        with self.lock:
            bucket = self.bursts.setdefault(key, [])
            bucket.append(fill)
            old = self.timers.get(key)
            if old is not None:
                old.cancel()
            timer = threading.Timer(self.debounce, self._flush, args=(key,))
            timer.daemon = True
            self.timers[key] = timer
            timer.start()

    def _flush(self, key: tuple[str, str]) -> None:
        address, _coin = key
        with self.lock:
            fills = self.bursts.pop(key, [])
            self.timers.pop(key, None)
            wallet = self.enabled.get(address) or {}
            equity = self.equity.get(address)
        if not fills:
            return
        signal = signal_from_fills(fills)
        if signal is None:
            return
        signal["id"] = f"sig:{address}:{signal['coin']}:{signal['tids'][0]}:{signal['tids'][-1]}:{signal['action']}"
        signal["leader"] = address
        signal["leader_name"] = wallet.get("name") or ""
        signal["leader_equity"] = None if equity is None else _fmt_num(equity)
        signal["received_ms"] = int(time.time() * 1000)
        try:
            from plaza.ingest import save_signal

            save_signal(signal)
        except Exception as exc:
            log(f"信号写入页面失败: {exc}")
        self._emit(signal)

    def _emit(self, signal: dict) -> None:
        line = json.dumps(signal, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()
        if self.as_json:
            print(line, flush=True)
            return
        name = signal.get("leader_name") or short_addr(signal.get("leader") or "")
        print(
            f"{fmt_time(signal.get('time'))}  信号  {name}  {signal.get('coin')}  "
            f"{signal.get('label')}  {signal.get('prev_szi')} → {signal.get('szi')}  "
            f"Δ{signal.get('delta_szi')}  均价 {signal.get('px')}",
            flush=True,
        )

    def flush_all(self) -> None:
        with self.lock:
            keys = list(self.bursts)
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()
        for key in keys:
            self._flush(key)

    def _on_error(self, _ws: websocket.WebSocketApp, error: object) -> None:
        if not self.stop.is_set():
            log(f"连接错误: {error}")

    def _on_close(self, _ws: websocket.WebSocketApp, status, reason) -> None:
        with self.lock:
            self.subscribed.clear()
            self.ws = None
        if not self.stop.is_set():
            log(f"连接断开 status={status} reason={reason}")

    def _connect_once(self) -> None:
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        with self.lock:
            self.ws = ws
        ws.run_forever(sslopt={"ca_certs": certifi.where()}, ping_interval=0)

    def _ws_loop(self) -> None:
        delay = 1.0
        while not self.stop.is_set():
            started = time.time()
            try:
                self._connect_once()
            except Exception as exc:
                if not self.stop.is_set():
                    log(f"连接失败: {exc}")
            if self.stop.is_set():
                break
            if time.time() - started > 30:
                delay = 1.0
            log(f"{delay:.0f}s 后重连")
            if self.stop.wait(delay):
                break
            delay = min(delay * 2, 15.0)

    def _poll_loop(self) -> None:
        while not self.stop.wait(POLL_SEC):
            try:
                self.reload_wallets()
            except Exception as exc:
                log(f"读名单失败: {exc}")

    def _equity_loop(self) -> None:
        while not self.stop.is_set():
            with self.lock:
                addresses = list(self.enabled)
            for address in addresses:
                if self.stop.is_set():
                    return
                try:
                    value = leader_equity(address)
                except Exception as exc:
                    log(f"权益读取失败 {short_addr(address)}: {exc}")
                    value = None
                with self.lock:
                    if value is not None:
                        self.equity[address] = value
            if self.stop.wait(EQUITY_SEC):
                return

    def run(self) -> None:
        SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._fh = SIGNAL_PATH.open("a", encoding="utf-8")
        self.reload_wallets()
        names = [
            f"{row.get('name') or short_addr(addr)} {addr}"
            for addr, row in self.enabled.items()
        ]
        if names:
            log("监控 " + "；".join(names))
        else:
            log(f"没有启用的钱包。python3 hype/wallets.py add 0x地址 --name 名字")
        log(f"信号写入 {SIGNAL_PATH}")
        threading.Thread(target=self._ws_loop, name="hl-ws", daemon=True).start()
        threading.Thread(target=self._poll_loop, name="hl-wallets", daemon=True).start()
        threading.Thread(target=self._equity_loop, name="hl-equity", daemon=True).start()
        try:
            while not self.stop.wait(3600):
                pass
        finally:
            self.flush_all()
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="监控钱包并发出永续交易信号")
    parser.add_argument("--json", action="store_true", help="标准输出用 JSON")
    parser.add_argument(
        "--debounce",
        type=float,
        default=DEBOUNCE_SEC,
        help="同一币种碎成交合并等待秒数，默认 3",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    hub = SignalHub(debounce=max(0.2, args.debounce), as_json=args.json)
    try:
        hub.run()
    except KeyboardInterrupt:
        log("停止")
        hub.shutdown()
        hub.flush_all()


if __name__ == "__main__":
    main()
