#!/usr/bin/env python3
"""实时监控 Hyperliquid 钱包成交、转账和订单。

HypurrScan 的 #txs 页看的是同一条链，但页面要刷新。
这里直连官方推送 wss://api.hyperliquid.xyz/ws，撮合一完成就落盘。

用法:
  python3 hype/watch_wallet.py
  python3 hype/watch_wallet.py 0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1
  python3 hype/watch_wallet.py 0x... --no-orders
  python3 hype/watch_wallet.py 0x... --json
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import certifi
import websocket

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
DEFAULT_ADDRESS = "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PING_SEC = 20
TAIL_ON_START = 8

_ssl_ctx = ssl.create_default_context(cafile=certifi.where())


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fmt_time(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "-"


def short_addr(addr: str) -> str:
    if not addr or len(addr) < 12:
        return addr or ""
    return addr[:6] + "…" + addr[-4:]


def post_info(body: dict, timeout: float = 20) -> object:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        INFO_URL, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
        return json.loads(resp.read().decode())


def fill_id(fill: dict) -> str:
    tid = fill.get("tid")
    if tid is not None:
        return f"fill:{tid}"
    return f"fill:{fill.get('hash')}:{fill.get('oid')}:{fill.get('time')}"


def ledger_id(item: dict) -> str:
    return f"ledger:{item.get('hash')}:{item.get('time')}"


def order_id(ev: dict) -> str:
    order = ev.get("order") or {}
    return f"order:{order.get('oid')}:{ev.get('status')}:{ev.get('statusTimestamp')}"


def side_cn(side: str) -> str:
    if side == "B":
        return "买"
    if side == "A":
        return "卖"
    return side or "-"


def fmt_fill(fill: dict) -> str:
    return (
        f"{fmt_time(fill.get('time'))}  成交  {fill.get('coin')}  "
        f"{fill.get('dir') or '-'}  {side_cn(fill.get('side'))}  "
        f"{fill.get('sz')} @ {fill.get('px')}  "
        f"已实现 {fill.get('closedPnl')}  手续费 {fill.get('fee')} {fill.get('feeToken') or ''}  "
        f"{fill.get('hash')}"
    )


def fmt_ledger(item: dict) -> str:
    delta = item.get("delta") or {}
    parts = [fmt_time(item.get("time")), "账本", str(delta.get("type") or "-")]
    for key in ("token", "coin", "amount", "usdcValue", "fee"):
        val = delta.get(key)
        if val not in (None, ""):
            parts.append(f"{key}={val}")
    src = delta.get("user")
    dst = delta.get("destination")
    if src:
        parts.append(f"from={short_addr(src)}")
    if dst:
        parts.append(f"to={short_addr(dst)}")
    if item.get("hash"):
        parts.append(item["hash"])
    return "  ".join(parts)


def fmt_order(ev: dict) -> str:
    order = ev.get("order") or {}
    ts = ev.get("statusTimestamp") or order.get("timestamp")
    return (
        f"{fmt_time(ts)}  订单  {order.get('coin')}  {side_cn(order.get('side'))}  "
        f"{order.get('sz')}/{order.get('origSz')} @ {order.get('limitPx')}  "
        f"{ev.get('status')}  oid={order.get('oid')}"
    )


class WalletWatch:
    def __init__(self, address: str, with_orders: bool, as_json: bool) -> None:
        self.address = address.lower()
        self.with_orders = with_orders
        self.as_json = as_json
        self.path = DATA_DIR / f"{self.address}.jsonl"
        self.seen: set[str] = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.ws: websocket.WebSocketApp | None = None
        self._fh = None

    def load_seen(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec_id = rec.get("id")
                if rec_id:
                    self.seen.add(rec_id)
        log(f"已有记录 {len(self.seen)} 条  {self.path}")

    def open_log(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def close_log(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def emit(
        self, rec_id: str, kind: str, payload: dict, line: str, live: bool, echo: bool
    ) -> bool:
        record = {
            "id": rec_id,
            "kind": kind,
            "user": self.address,
            "live": live,
            "received_ms": int(time.time() * 1000),
            "data": payload,
        }
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            if rec_id in self.seen:
                return False
            self.seen.add(rec_id)
            if self._fh is not None:
                self._fh.write(text + "\n")
                self._fh.flush()
        if echo:
            print(text if self.as_json else line, flush=True)
        return True

    def ingest_fill(self, fill: dict, live: bool, echo: bool | None = None) -> bool:
        if echo is None:
            echo = live
        return self.emit(fill_id(fill), "fill", fill, fmt_fill(fill), live, echo)

    def ingest_ledger(self, item: dict, live: bool, echo: bool | None = None) -> bool:
        if echo is None:
            echo = live
        return self.emit(ledger_id(item), "ledger", item, fmt_ledger(item), live, echo)

    def ingest_order(self, ev: dict, live: bool, echo: bool | None = None) -> bool:
        if echo is None:
            echo = live
        return self.emit(order_id(ev), "order", ev, fmt_order(ev), live, echo)

    def backfill(self) -> None:
        try:
            fills = post_info({"type": "userFills", "user": self.address})
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log(f"成交回补失败: {exc}")
            fills = []
        try:
            ledger = post_info(
                {"type": "userNonFundingLedgerUpdates", "user": self.address}
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log(f"账本回补失败: {exc}")
            ledger = []

        pending: list[tuple[int, str, dict]] = []
        if isinstance(fills, list):
            for fill in fills:
                pending.append((int(fill.get("time") or 0), "fill", fill))
        if isinstance(ledger, list):
            for item in ledger:
                pending.append((int(item.get("time") or 0), "ledger", item))
        pending.sort(key=lambda row: row[0])

        new_lines: list[str] = []
        n_fill = n_ledger = 0
        for _, kind, payload in pending:
            if kind == "fill":
                if self.ingest_fill(payload, live=False, echo=False):
                    n_fill += 1
                    new_lines.append(fmt_fill(payload))
            elif self.ingest_ledger(payload, live=False, echo=False):
                n_ledger += 1
                new_lines.append(fmt_ledger(payload))
        log(f"回补新增 成交 {n_fill}  账本 {n_ledger}")
        if new_lines:
            log("回补最新:")
            for line in new_lines[-TAIL_ON_START:]:
                log("  " + line)

    def _subscribe(self, ws: websocket.WebSocketApp) -> None:
        subs = [
            {"type": "userFills", "user": self.address, "aggregateByTime": False},
            {"type": "userNonFundingLedgerUpdates", "user": self.address},
        ]
        if self.with_orders:
            subs.append({"type": "orderUpdates", "user": self.address})
        for sub in subs:
            ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
        log(f"已订阅 {', '.join(s['type'] for s in subs)}  {self.address}")

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._subscribe(ws)

        def ping() -> None:
            while not self.stop.is_set():
                if self.stop.wait(PING_SEC):
                    break
                try:
                    ws.send(json.dumps({"method": "ping"}))
                except Exception:
                    break

        threading.Thread(target=ping, name="hl-ping", daemon=True).start()

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        channel = msg.get("channel")
        if channel in (None, "subscriptionResponse", "pong"):
            return
        data = msg.get("data")
        if channel == "userFills" and isinstance(data, dict):
            live = not bool(data.get("isSnapshot"))
            fills = data.get("fills") or []
            # 快照里没见过的也要打出来，避免回补和推送之间漏一条。
            added = sum(1 for fill in fills if self.ingest_fill(fill, live=live, echo=True))
            if data.get("isSnapshot"):
                log(f"成交快照 {len(fills)} 条，新写入 {added}")
            return
        if channel == "userNonFundingLedgerUpdates" and isinstance(data, dict):
            live = not bool(data.get("isSnapshot"))
            rows = data.get("nonFundingLedgerUpdates") or data.get("ledgerUpdates") or []
            added = sum(
                1 for item in rows if self.ingest_ledger(item, live=live, echo=True)
            )
            if data.get("isSnapshot"):
                log(f"账本快照 {len(rows)} 条，新写入 {added}")
            return
        if channel == "orderUpdates" and isinstance(data, list):
            for ev in data:
                if isinstance(ev, dict):
                    self.ingest_order(ev, live=True)
            return
        if channel == "user":
            # userEvents 与 userFills 重复，不订阅。若服务端仍推来，忽略。
            return

    def _on_error(self, _ws: websocket.WebSocketApp, error: object) -> None:
        if self.stop.is_set():
            return
        log(f"连接错误: {error}")

    def _on_close(self, _ws: websocket.WebSocketApp, status, reason) -> None:
        if self.stop.is_set():
            return
        log(f"连接断开 status={status} reason={reason}")

    def _connect_once(self) -> None:
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws = ws
        ws.run_forever(
            sslopt={"ca_certs": certifi.where()},
            ping_interval=0,
        )

    def run(self) -> None:
        self.load_seen()
        self.open_log()
        # 先连上推送，回补和订阅并行，中间成交不会漏。
        threading.Thread(target=self._ws_loop, name="hl-ws", daemon=True).start()
        try:
            self.backfill()
            log(f"实时监控中，新记录打在屏幕上，同时写入 {self.path}")
            while not self.stop.wait(3600):
                pass
        finally:
            self.close_log()

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

    def shutdown(self) -> None:
        self.stop.set()
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实时监控 Hyperliquid 钱包交易")
    parser.add_argument("address", nargs="?", default=DEFAULT_ADDRESS, help="0x 钱包地址")
    parser.add_argument("--no-orders", action="store_true", help="不推送下单/撤单，只看成交和转账")
    parser.add_argument("--json", action="store_true", help="标准输出用 JSON，方便别的程序接")
    args = parser.parse_args()
    addr = args.address.strip().lower()
    if not (addr.startswith("0x") and len(addr) == 42):
        parser.error(f"地址格式不对: {args.address}")
    args.address = addr
    return args


def main() -> None:
    args = parse_args()
    watch = WalletWatch(args.address, with_orders=not args.no_orders, as_json=args.json)
    try:
        watch.run()
    except KeyboardInterrupt:
        log("停止")
        watch.shutdown()


if __name__ == "__main__":
    main()
