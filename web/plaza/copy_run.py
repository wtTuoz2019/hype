"""后台跟单。页面只读结果，下单在这条线程里。"""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

from django.db import close_old_connections
from django.utils import timezone

from .copy_plan import (
    DAY_HALT,
    Decision,
    LotState,
    apply_intent,
    base_coin,
    decide,
    fuse_hit,
)
from .models import CopyLog, CopyLot, ExchangeAccount, Follow, Leader, TradeSignal
from .vault import decrypt

_QUEUE: queue.Queue = queue.Queue()
_STARTED = False
_VENUES: dict[tuple[int, str], object] = {}


def enqueue_signal(signal_id: int) -> None:
    _QUEUE.put(("signal", signal_id))


def enqueue_flatten(follow_id: int) -> None:
    _QUEUE.put(("flatten", follow_id))


def start_copy_worker() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(target=_loop, name="hype-copy", daemon=True).start()


def _loop() -> None:
    while True:
        try:
            kind, item_id = _QUEUE.get(timeout=30)
        except queue.Empty:
            try:
                sweep_fuses()
            except Exception as exc:
                print(f"跟单保护检查失败: {exc}", flush=True)
            continue
        close_old_connections()
        try:
            if kind == "signal":
                process_signal(item_id)
            elif kind == "flatten":
                process_flatten(item_id)
        except Exception as exc:
            print(f"跟单执行失败: {exc}", flush=True)


def _log(follow: Follow, coin: str, status: str, qty: float, detail: str) -> None:
    CopyLog.objects.create(
        follow=follow,
        coin=coin[:40],
        status=status[:16],
        qty=qty,
        detail=detail[:200],
    )


def _lot_state(follow: Follow, coin: str) -> tuple[CopyLot, LotState]:
    row, _ = CopyLot.objects.get_or_create(follow=follow, coin=coin)
    return row, LotState(row.side, row.qty, row.ratio, row.entry, row.pending)


def _save_lot(row: CopyLot, state: LotState) -> None:
    row.side = state.side
    row.qty = state.qty
    row.ratio = state.ratio
    row.entry = state.entry
    row.pending = state.pending
    row.save(update_fields=["side", "qty", "ratio", "entry", "pending", "updated_at"])


def leader_equity(leader: Leader) -> float:
    import json

    try:
        payload = json.loads(leader.snapshot.payload)
    except Exception:
        return 0.0
    periods = payload.get("periods") if isinstance(payload, dict) else None
    if not isinstance(periods, dict):
        return 0.0
    for key in ("all", "7d", "30d", "24h"):
        try:
            equity = float((periods.get(key) or {}).get("equity") or 0)
        except (TypeError, ValueError):
            equity = 0.0
        if equity > 0:
            return equity
    return 0.0


def _halted(account: ExchangeAccount, equity: float) -> bool:
    today = timezone.localdate().isoformat()
    if account.halt_day != today:
        account.halt_day = today
        account.day_equity = equity
        account.halted = False
        account.save(update_fields=["halt_day", "day_equity", "halted"])
        return False
    if account.day_equity > 0 and equity <= account.day_equity * (1 - DAY_HALT):
        if not account.halted:
            account.halted = True
            account.save(update_fields=["halted"])
    return account.halted


def _run_intents(follow: Follow, coin: str, venue, state: LotState, decision: Decision, px: float) -> None:
    current = state
    sent = 0.0
    for intent in decision.intents:
        try:
            if intent.kind == "open":
                venue.ensure(coin)
            venue.order(coin, intent.side, intent.qty, intent.reduce_only)
        except Exception as exc:
            _save_lot(*_reload(follow, coin, current))
            _log(follow, coin, "error", sent, f"{decision.detail}失败: {exc}"[:200])
            return
        current = apply_intent(current, intent, px)
        sent += intent.qty
    row, _ = _lot_state(follow, coin)
    _save_lot(row, current)
    _log(follow, coin, "sent", sent, decision.detail)


def _reload(follow: Follow, coin: str, state: LotState) -> tuple[CopyLot, LotState]:
    row, _ = _lot_state(follow, coin)
    return row, state


def _numbers(signal: TradeSignal) -> tuple[str, float, float, float]:
    coin = base_coin(signal.coin)
    try:
        delta = abs(float(signal.delta_szi or 0))
        now = abs(float(signal.szi or 0))
        px = float(signal.px or 0)
    except (TypeError, ValueError):
        delta, now, px = 0.0, 0.0, 0.0
    return coin, delta, now, px


def process_signal(signal_id: int, venue_for=None) -> None:
    close_old_connections()
    signal = TradeSignal.objects.select_related("leader").filter(pk=signal_id).first()
    if signal is None:
        return
    follows = Follow.objects.filter(
        leader=signal.leader, active=True, account__enabled=True
    ).select_related("account", "leader")
    equity = leader_equity(signal.leader)
    factory = venue_for or real_venue
    for follow in follows:
        if follow.armed_ms and signal.time_ms < follow.armed_ms:
            _log(follow, base_coin(signal.coin), "skip", 0, "早于开始跟单")
            continue
        if follow.account_id is None:
            continue
        try:
            venue = factory(follow.account)
            _copy_one(follow, signal, equity, venue)
        except Exception as exc:
            _log(follow, base_coin(signal.coin), "error", 0, str(exc)[:200])


def _copy_one(follow: Follow, signal: TradeSignal, leader_eq: float, venue) -> None:
    coin, delta, now, px = _numbers(signal)
    if not coin:
        return
    follower_eq = float(venue.equity() or 0)
    mark = float(venue.mark(coin) or 0)
    halted = _halted(follow.account, follower_eq)
    row, state = _lot_state(follow, coin)
    if fuse_hit(state, mark, follower_eq):
        decision = decide(
            action="close",
            side=state.side,
            leader_delta=state.qty,
            leader_now=0,
            px=px or mark,
            mark=mark,
            follower_equity=follower_eq,
            leader_equity=leader_eq or 1,
            lot=state,
        )
        decision.detail = "触发保护平仓"
        _run_intents(follow, coin, venue, state, decision, px or mark)
        return
    min_qty, min_notional = venue.limits(coin)
    decision = decide(
        action=signal.action,
        side=signal.side,
        leader_delta=delta,
        leader_now=now,
        px=px,
        mark=mark,
        follower_equity=follower_eq,
        leader_equity=leader_eq,
        lot=state,
        min_qty=min_qty,
        min_notional=min_notional,
        halted=halted,
    )
    if not decision.intents:
        if decision.detail:
            _save_lot(row, decision.lot)
            _log(follow, coin, "skip", 0, decision.detail)
        return
    _run_intents(follow, coin, venue, state, decision, px)


def process_flatten(follow_id: int, venue_for=None) -> None:
    close_old_connections()
    follow = Follow.objects.select_related("account").filter(pk=follow_id).first()
    if follow is None or follow.account_id is None:
        return
    factory = venue_for or real_venue
    try:
        venue = factory(follow.account)
    except Exception as exc:
        _log(follow, "", "error", 0, f"取消跟单平仓失败: {exc}"[:200])
        return
    for row in CopyLot.objects.filter(follow=follow, qty__gt=0):
        state = LotState(row.side, row.qty, row.ratio, row.entry, row.pending)
        mark = 0.0
        try:
            mark = float(venue.mark(row.coin) or 0)
        except Exception:
            mark = 0.0
        decision = decide(
            action="close",
            side=state.side,
            leader_delta=state.qty,
            leader_now=0,
            px=mark or state.entry or 1,
            mark=mark or state.entry or 1,
            follower_equity=1,
            leader_equity=1,
            lot=state,
        )
        decision.detail = "取消跟单平仓"
        _run_intents(follow, row.coin, venue, state, decision, mark or state.entry or 1)


def sweep_fuses(venue_for=None) -> None:
    close_old_connections()
    factory = venue_for or real_venue
    lots = CopyLot.objects.filter(qty__gt=0, follow__active=True).select_related("follow__account")
    for row in lots:
        account = row.follow.account
        if account is None or not account.enabled:
            continue
        try:
            venue = factory(account)
            mark = float(venue.mark(row.coin) or 0)
            equity = float(venue.equity() or 0)
        except Exception as exc:
            _log(row.follow, row.coin, "error", 0, f"保护检查失败: {exc}"[:200])
            continue
        state = LotState(row.side, row.qty, row.ratio, row.entry, row.pending)
        if not fuse_hit(state, mark, equity):
            continue
        decision = decide(
            action="close",
            side=state.side,
            leader_delta=state.qty,
            leader_now=0,
            px=mark or state.entry or 1,
            mark=mark or 1,
            follower_equity=equity or 1,
            leader_equity=1,
            lot=state,
        )
        decision.detail = "触发保护平仓"
        _run_intents(row.follow, row.coin, venue, state, decision, mark or state.entry or 1)


def real_venue(account: ExchangeAccount):
    key = (account.id, account.key_fp)
    cached = _VENUES.get(key)
    if cached is not None:
        return cached
    future = Path(__file__).resolve().parents[3] / "wjfuture"
    if str(future) not in sys.path:
        sys.path.insert(0, str(future))
    api_key = decrypt(account.key_enc)
    api_secret = decrypt(account.secret_enc)
    if account.venue == ExchangeAccount.VENUE_GATE:
        venue = GateVenue(api_key, api_secret)
    else:
        venue = BinanceVenue(api_key, api_secret)
    _VENUES[key] = venue
    return venue


class BinanceVenue:
    def __init__(self, api_key: str, api_secret: str):
        from binance_futures_client import BinanceFuturesClient

        self.client = BinanceFuturesClient(api_key, api_secret)
        self._ready: set[str] = set()

    def equity(self) -> float:
        data = self.client._signed("GET", "/fapi/v2/account")
        return float(data.get("totalMarginBalance") or 0)

    def mark(self, base: str) -> float:
        return float(self.client.mark_price(f"{base}USDT") or 0)

    def limits(self, base: str) -> tuple[float, float]:
        symbol = f"{base}USDT"
        lot = self.client._load_symbol_filters(symbol)
        return float(lot["min_qty"]), float(self.client.min_open_notional_usdt(symbol))

    def ensure(self, base: str) -> None:
        symbol = f"{base}USDT"
        if symbol in self._ready:
            return
        try:
            self.client.set_dual_side_position(dual=True)
        except Exception as exc:
            if "No need to change" not in str(exc):
                raise
        try:
            self.client._signed("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
        except Exception as exc:
            if "No need to change" not in str(exc) and "-4046" not in str(exc):
                raise
        self.client.set_leverage(symbol, 5)
        self._ready.add(symbol)

    def order(self, base: str, side: str, qty: float, reduce_only: bool) -> None:
        symbol = f"{base}USDT"
        position = "LONG" if side == "long" else "SHORT"
        trade = "BUY" if (side == "long") != reduce_only else "SELL"
        try:
            quantity = self.client.quantize_qty(symbol, qty) if reduce_only else self.client.quantize_qty_for_min_open(
                symbol, qty, self.mark(base)
            )
        except ValueError:
            quantity = self.client.quantize_qty_for_min_open(symbol, qty, self.mark(base))
        self.client.market_order(symbol, side=trade, quantity=quantity, position_side=position, reduce_only=reduce_only)


class GateVenue:
    def __init__(self, api_key: str, api_secret: str):
        from gate_futures_client import GateFuturesClient

        self.client = GateFuturesClient(api_key, api_secret)
        self._ready: set[str] = set()

    def _contract(self, base: str) -> str:
        from gate_futures_client import symbol_to_contract

        return symbol_to_contract(f"{base}USDT")

    def equity(self) -> float:
        acc = self.client._futures.list_futures_accounts(self.client._settle)
        total = float(getattr(acc, "total", 0) or 0)
        upnl = float(getattr(acc, "unrealised_pnl", 0) or 0)
        return total + upnl

    def mark(self, base: str) -> float:
        import requests

        contract = self._contract(base)
        response = requests.get(
            "https://api.gateio.ws/api/v4/futures/usdt/tickers",
            params={"contract": contract},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return 0.0
        return float(rows[0].get("mark_price") or 0)

    def limits(self, base: str) -> tuple[float, float]:
        contract = self._contract(base)
        info = self.client.get_contract(contract)
        quanto = float(info.get("quanto_multiplier") or 1)
        lots = float(self.client.effective_min_open_lots(contract))
        return lots * quanto, 0.0

    def ensure(self, base: str) -> None:
        contract = self._contract(base)
        if contract in self._ready:
            return
        if not self.client.get_dual_mode():
            self.client.set_dual_mode(dual=True)
        self.client.set_leverage(contract, 5)
        self._ready.add(contract)

    def order(self, base: str, side: str, qty: float, reduce_only: bool) -> None:
        contract = self._contract(base)
        try:
            lots = (
                self.client.quantize_qty_str(contract, qty)
                if reduce_only
                else self.client.quantize_qty_str_open(contract, qty)
            )
        except ValueError:
            lots = self.client.quantize_qty_str_open(contract, qty)
        if side == "long":
            size = f"-{lots}" if reduce_only else lots
        else:
            size = lots if reduce_only else f"-{lots}"
        self.client.market_order(contract, size=size, reduce_only=reduce_only)
