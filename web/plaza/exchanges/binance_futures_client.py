# -*- coding: utf-8 -*-
"""Binance USDT-M 合约签名 REST（市价单）。需在网页端开启双向持仓(对冲模式)。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlencode

import requests

FAPI_BASE = "https://fapi.binance.com"


def _qty_decimal_places_for_step(step: Decimal) -> int:
    """LOT_SIZE step 隐含的小数位（与 quantityPrecision 不一致时以 step 为准，避免 -1111 Precision）。"""
    s = step.normalize()
    exp = int(s.as_tuple().exponent)
    if exp >= 0:
        return 0
    return -exp


def _sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


class BinanceFuturesClient:
    def __init__(self, api_key: str, api_secret: str, *, base: str = FAPI_BASE, recv_window: int = 5000):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = base.rstrip("/")
        self.recv_window = int(recv_window)
        self._session = requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": self.api_key})
        self._symbol_filters: Dict[str, Dict[str, Any]] = {}

    def _signed(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = self.recv_window
        query = urlencode(p)
        sig = _sign(query, self.api_secret)
        url = f"{self.base}{path}?{query}&signature={sig}"
        r = self._session.request(method.upper(), url, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:800]}")
        try:
            data = r.json()
        except json.JSONDecodeError:
            raise RuntimeError(r.text[:800])
        if isinstance(data, dict) and isinstance(data.get("code"), int) and data["code"] < 0:
            raise RuntimeError(f"Binance API {data.get('code')}: {data.get('msg')}")
        return data

    def exchange_info(self, symbol: str) -> Dict[str, Any]:
        url = f"{self.base}/fapi/v1/exchangeInfo"
        r = self._session.get(url, params={"symbol": symbol.upper()}, timeout=15)
        r.raise_for_status()
        return r.json()

    def _load_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper()
        cached = self._symbol_filters.get(sym)
        if cached is not None:
            return cached
        info = self.exchange_info(sym)
        syms = info.get("symbols") or []
        if not syms:
            raise RuntimeError("exchangeInfo 无 symbols")
        s0 = syms[0]
        lot_step = lot_min = mkt_step = mkt_min = None
        min_notional = Decimal("20")
        for f in s0.get("filters") or []:
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                lot_step = Decimal(str(f["stepSize"]))
                lot_min = Decimal(str(f["minQty"]))
            elif ft == "MARKET_LOT_SIZE":
                mkt_step = Decimal(str(f["stepSize"]))
                mkt_min = Decimal(str(f["minQty"]))
            elif ft == "MIN_NOTIONAL":
                try:
                    min_notional = Decimal(str(f.get("notional", "20")))
                except Exception:
                    pass
        if lot_step is None:
            raise RuntimeError("未找到 LOT_SIZE")
        if mkt_step is None:
            mkt_step, mkt_min = lot_step, lot_min
        if mkt_min is None:
            mkt_min = lot_min
        qp = s0.get("quantityPrecision")
        try:
            qpi = int(qp)
        except Exception:
            qpi = -1
        # 与 myfuture 一致：LOT_SIZE / MARKET_LOT_SIZE 取更粗步长；quantityPrecision 可能更严（XRP 仅 1 位小数）
        steps = [x for x in (lot_step, mkt_step) if x is not None and x > 0]
        step = max(steps) if steps else Decimal("0.00000001")
        if qpi >= 0:
            precision_step = Decimal("1").scaleb(-qpi)
            if precision_step > step:
                step = precision_step
        min_q = max(lot_min or Decimal("0"), mkt_min or Decimal("0"))
        step_dp = _qty_decimal_places_for_step(step)
        fmt_dp = min(step_dp, qpi) if qpi >= 0 else step_dp
        cached = {
            "lot_step": lot_step,
            "lot_min": lot_min,
            "market_step": mkt_step,
            "market_min": mkt_min,
            "step": step,
            "min_qty": min_q,
            "qty_precision": qpi,
            "format_dp": fmt_dp,
            "min_notional": min_notional,
        }
        self._symbol_filters[sym] = cached
        return cached

    def invalidate_symbol_filters(self, symbol: str) -> None:
        self._symbol_filters.pop(symbol.upper(), None)

    def lot_size_filter(self, symbol: str) -> tuple[Decimal, Decimal, int]:
        """(step_size, min_qty, qty_precision)"""
        f = self._load_symbol_filters(symbol)
        return f["step"], f["min_qty"], f["qty_precision"]

    @staticmethod
    def _qty_from_float(qty: float) -> Decimal:
        # 避免 str(0.1+0.1+0.1) 长尾导致 -1111
        return Decimal(format(float(qty), ".12f"))

    @staticmethod
    def _round_by_qty_bucket(q: Decimal, *, up: bool) -> Decimal:
        """按数量分段取整（myfuture 口径，减少贴边 -1111）。"""
        if q > Decimal("10"):
            quantum = Decimal("1")
        elif q > Decimal("1"):
            quantum = Decimal("0.1")
        elif q > Decimal("0.1"):
            quantum = Decimal("0.01")
        else:
            quantum = Decimal("0.001")
        mode = ROUND_UP if up else ROUND_DOWN
        return q.quantize(quantum, rounding=mode)

    @staticmethod
    def _fmt_qty(q: Decimal) -> str:
        s = format(q, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"

    def _qty_decimal(self, qty: Union[float, str, Decimal]) -> Decimal:
        if isinstance(qty, Decimal):
            return qty
        if isinstance(qty, str):
            return Decimal(qty.strip())
        return self._qty_from_float(float(qty))

    def _coerce_quantity_str(self, symbol: str, quantity: Union[str, float, Decimal]) -> str:
        """把 quantity 按最新 filter 再次向下规整，兜底 -1111。"""
        q = self._qty_decimal(quantity)
        lot = self._load_symbol_filters(symbol)
        step: Decimal = lot["step"]
        min_qty: Decimal = lot["min_qty"]
        qpi = int(lot.get("qty_precision", -1))
        q = (q / step).to_integral_value(rounding=ROUND_DOWN) * step
        if qpi >= 0:
            q = q.quantize(Decimal("1").scaleb(-qpi), rounding=ROUND_DOWN)
        q = q.quantize(step, rounding=ROUND_DOWN)
        if q < min_qty:
            q = Decimal("0")
        if q <= 0:
            raise ValueError(f"数量 {quantity} 量化后为0")
        return self._fmt_qty(q)

    @staticmethod
    def _adaptive_qty_candidates(quantity: str) -> List[str]:
        """为 -1111 兜底生成逐步更保守的数量候选。"""
        q = Decimal(str(quantity))
        out: List[str] = []
        seen = set()

        def add(x: Decimal) -> None:
            s = BinanceFuturesClient._fmt_qty(x)
            if s not in seen and Decimal(s) > 0:
                seen.add(s)
                out.append(s)

        add(q)
        for dp in range(7, -1, -1):
            quantum = Decimal("1").scaleb(-dp)
            add(q.quantize(quantum, rounding=ROUND_DOWN))
        add(q.to_integral_value(rounding=ROUND_UP))
        return out

    def quantize_qty(self, symbol: str, qty: Union[float, str, Decimal]) -> str:
        d = self._qty_decimal(qty)
        lot = self._load_symbol_filters(symbol)
        step: Decimal = lot["step"]
        min_qty: Decimal = lot["min_qty"]
        q = (d / step).to_integral_value(rounding=ROUND_DOWN) * step
        q = q.quantize(step, rounding=ROUND_DOWN)
        qpi = int(lot.get("qty_precision", -1))
        if qpi >= 0:
            q = q.quantize(Decimal("1").scaleb(-qpi), rounding=ROUND_DOWN)
        if q < min_qty:
            raise ValueError(f"数量 {qty} 低于最小下单 {min_qty} (step={step})")
        if q <= 0:
            raise ValueError(f"数量 {qty} 量化后为0")
        return self._fmt_qty(q)

    def min_open_notional_usdt(self, symbol: str) -> float:
        """开仓最小名义（USDT）；略加缓冲避免贴边拒单。"""
        f = self._load_symbol_filters(symbol)
        base = float(f.get("min_notional", Decimal("20")))
        return max(base * 1.01, base + 0.05)

    def qty_for_min_open_notional(self, symbol: str, mark_or_px: float, min_notional_usdt: Optional[float] = None) -> float:
        """按最小名义向上取整到交易所步长的一手数量（标的个数）。"""
        px = float(mark_or_px)
        if px <= 1e-12:
            return 0.0
        min_n = float(min_notional_usdt) if min_notional_usdt is not None else self.min_open_notional_usdt(symbol)
        return float(self.quantize_qty_for_min_open(symbol, 0.0, px, min_n))

    def quantize_qty_for_min_open(
        self,
        symbol: str,
        qty: float,
        price: float,
        min_notional_usdt: Optional[float] = None,
    ) -> str:
        """开仓数量：不低于 qty，且名义 >= 最小开仓名义（向上取整步长）。"""
        px = Decimal(str(float(price)))
        min_n = Decimal(str(min_notional_usdt if min_notional_usdt is not None else self.min_open_notional_usdt(symbol)))
        q_target = self._qty_decimal(qty)
        if min_n > 0 and px > 0:
            by_notional = min_n / px
            if by_notional > q_target:
                q_target = by_notional
        q_target = self._round_by_qty_bucket(q_target, up=True)
        lot = self._load_symbol_filters(symbol)
        step: Decimal = lot["step"]
        min_qty: Decimal = lot["min_qty"]
        q = (q_target / step).to_integral_value(rounding=ROUND_UP) * step
        q = q.quantize(step, rounding=ROUND_DOWN)
        if q < min_qty:
            q = min_qty
        while q * px + Decimal("1e-9") < min_n:
            q += step
        if q <= 0:
            raise ValueError(f"数量 {qty} 量化后为0")
        return self._fmt_qty(q)

    def quantize_qty_with_min_notional(
        self,
        symbol: str,
        qty: float,
        price: float,
        min_notional_usdt: Optional[float] = None,
    ) -> str:
        """与 myfuture 同名接口；开仓最小名义向上量化。"""
        return self.quantize_qty_for_min_open(symbol, qty, price, min_notional_usdt)

    def get_dual_side_position(self) -> bool:
        data = self._signed("GET", "/fapi/v1/positionSide/dual")
        return bool(data.get("dualSidePosition"))

    def set_dual_side_position(self, *, dual: bool = True) -> Any:
        return self._signed("POST", "/fapi/v1/positionSide/dual", {"dualSidePosition": "true" if dual else "false"})

    def set_leverage(self, symbol: str, leverage: int) -> Any:
        return self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol.upper(), "leverage": int(leverage)})

    def market_order(
        self,
        symbol: str,
        *,
        side: Literal["BUY", "SELL"],
        quantity: str,
        position_side: Literal["LONG", "SHORT"],
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "positionSide": position_side,
            # 市价单优先要求返回成交结果（含 executedQty / avgPrice 等）。
            "newOrderRespType": "RESULT",
        }
        # In hedge mode (positionSide LONG/SHORT), Binance may reject reduceOnly as "not required".
        # Only send reduceOnly in one-way mode (positionSide BOTH).
        if reduce_only and position_side == "BOTH":
            params["reduceOnly"] = "true"
        try:
            params["quantity"] = self._coerce_quantity_str(symbol, quantity)
        except Exception:
            params["quantity"] = self.quantize_qty(symbol, quantity)
        try:
            return self._signed("POST", "/fapi/v1/order", params)
        except RuntimeError as e:
            msg = str(e)
            if "-1111" not in msg or "Precision is over the maximum defined for this asset" not in msg:
                raise
            self.invalidate_symbol_filters(symbol.upper())
            tried = {str(params["quantity"])}
            candidates: List[str] = []
            try:
                c0 = self._coerce_quantity_str(symbol, quantity)
                if c0 not in tried:
                    candidates.append(c0)
                    tried.add(c0)
            except Exception:
                pass
            for c in self._adaptive_qty_candidates(str(quantity)):
                if c not in tried:
                    candidates.append(c)
                    tried.add(c)
            last_err: Optional[Exception] = e
            for cq in candidates:
                try:
                    params["quantity"] = cq
                    return self._signed("POST", "/fapi/v1/order", params)
                except RuntimeError as e2:
                    m2 = str(e2)
                    if "-1111" in m2 and "Precision is over the maximum defined for this asset" in m2:
                        last_err = e2
                        continue
                    raise
            if last_err is not None:
                raise last_err
            raise

    def mark_price(self, symbol: str) -> float:
        """公开接口：合约标记价格。"""
        r = self._session.get(
            f"{self.base}/fapi/v1/premiumIndex",
            params={"symbol": symbol.upper()},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return float(data.get("markPrice") or 0.0)

    def account_balance_usdt(self) -> List[Dict[str, Any]]:
        return self._signed("GET", "/fapi/v2/balance")

    def position_risk_all(self) -> List[Dict[str, Any]]:
        """全合约持仓风险（含多空腿）；对冲模式下 LONG/SHORT 分行。"""
        data = self._signed("GET", "/fapi/v2/positionRisk", None)
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def long_short_qty_for_symbol(self, symbol: str, rows: Optional[List[Dict[str, Any]]] = None) -> tuple[float, float]:
        """标的数量：多头合计、空头合计（与本地 state qty 同口径）。"""
        return _binance_aggregate_long_short(rows if rows is not None else self.position_risk_all(), symbol)

    def long_short_position_snapshot(
        self,
        symbol: str,
        rows: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """返回 {'long': {'qty','entry_price'}, 'short': {...}}（对冲模式优先）。"""
        sym = symbol.upper()
        src = rows if rows is not None else self.position_risk_all()
        out: Dict[str, Dict[str, float]] = {}
        for r in src:
            if not isinstance(r, dict):
                continue
            if str(r.get("symbol", "")).upper() != sym:
                continue
            ps = str(r.get("positionSide", "BOTH")).upper()
            try:
                amt = float(r.get("positionAmt", 0) or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            if abs(amt) <= 1e-12:
                continue
            side: Optional[str] = None
            qty = abs(amt)
            if ps == "LONG":
                side = "long"
            elif ps == "SHORT":
                side = "short"
            elif ps == "BOTH":
                side = "long" if amt > 0 else "short"
            if side is None:
                continue
            try:
                ep = float(r.get("entryPrice", 0) or 0.0)
            except (TypeError, ValueError):
                ep = 0.0
            cur = out.get(side)
            if cur is None:
                out[side] = {"qty": qty, "entry_price": ep}
            else:
                q0 = float(cur.get("qty", 0.0))
                p0 = float(cur.get("entry_price", 0.0))
                q1 = q0 + qty
                if q1 > 1e-12:
                    if ep > 1e-12 and p0 > 1e-12:
                        cur["entry_price"] = (p0 * q0 + ep * qty) / q1
                    elif ep > 1e-12:
                        cur["entry_price"] = ep
                cur["qty"] = q1
        return out


def _binance_aggregate_long_short(rows: List[Dict[str, Any]], symbol: str) -> tuple[float, float]:
    sym = symbol.upper()
    long_q = 0.0
    short_q = 0.0
    for r in rows:
        if str(r.get("symbol", "")).upper() != sym:
            continue
        try:
            amt = float(r.get("positionAmt", 0) or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        if abs(amt) < 1e-12:
            continue
        ps = str(r.get("positionSide", "BOTH")).upper()
        if ps == "LONG":
            long_q += abs(amt)
        elif ps == "SHORT":
            short_q += abs(amt)
        elif ps == "BOTH":
            if amt > 0:
                long_q += amt
            else:
                short_q += -amt
    return long_q, short_q


def fill_to_order_params(symbol: str, fl_action: str, fl_side: str, qty: Union[float, str], quantize) -> Dict[str, Any]:
    """paper Fill -> 对冲模式市价参数。qty 可为已量化字符串，避免 float 长尾。"""
    qstr = qty if isinstance(qty, str) else quantize(qty)
    if fl_action == "open":
        if fl_side == "long":
            return {"side": "BUY", "position_side": "LONG", "reduce_only": False, "quantity": qstr}
        return {"side": "SELL", "position_side": "SHORT", "reduce_only": False, "quantity": qstr}
    if fl_action == "close":
        if fl_side == "long":
            return {"side": "SELL", "position_side": "LONG", "reduce_only": True, "quantity": qstr}
        return {"side": "BUY", "position_side": "SHORT", "reduce_only": True, "quantity": qstr}
    raise ValueError(f"未知 fill action={fl_action}")
