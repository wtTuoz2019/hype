# -*- coding: utf-8 -*-
"""Gate.io USDT 永续：公开行情用 REST；私有接口用官方 gate-api SDK。

官方 ApiClient.gen_sign 使用 str(time.time())（带小数秒）。部分环境/文档示例与能跑通的脚本
使用整型 Unix 秒参与签名；此处子类改为整型秒，并对 KEY/SECRET 做规范化，减少 INVALID_SIGNATURE。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Optional, Tuple, Union

# 部署后可在启动日志核对；Gate ETH lot_size=0.2 应预检 size=20 -> 名义≈0.2
GATE_FUTURES_CLIENT_BUILD = "gate-snapshot-debug-20260602e"

import requests
import six
from gate_api import Configuration, FuturesApi, FuturesOrder
from gate_api.api_client import ApiClient as _GateApiClientBase
from gate_api.exceptions import GateApiException

from .kline_live_cache import make_key as _kline_cache_key
from .kline_live_cache import prune_kline_live_cache
from .kline_live_cache import store as _kline_cache_store
from .kline_live_cache import trim_klines as _trim_gate_klines
from .kline_live_cache import try_incremental as _kline_try_incremental

API_V4 = "https://api.gateio.ws/api/v4"
API_HOST = "https://api.gateio.ws"
SETTLE = "usdt"


def _decimal_from_float(x: float) -> Decimal:
    return Decimal(format(float(x), ".12f"))


def _step_decimal_places(step: Decimal) -> int:
    s = step.normalize()
    exp = int(s.as_tuple().exponent)
    return 0 if exp >= 0 else -exp


def _compact_decimal_str(d: Decimal) -> str:
    """Gate size 字段：去掉无意义尾零（20.0000 -> 20，0.3000 -> 0.3）。"""
    if d == d.to_integral_value():
        return str(int(d))
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _env_truthy(name: str) -> bool:
    v = str(os.environ.get(name, "") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def _mask_key(v: str) -> str:
    s = str(v or "").strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}***{s[-4:]}"


def _format_gate_contract_size(n: Decimal, step: Decimal, enable_dec: bool) -> str:
    """Gate create_futures_order.size：先按 step 对齐，再用紧凑十进制串（如 20、0.3，勿 20.0000）。"""
    if not enable_dec:
        ni = int(n.to_integral_value(rounding=ROUND_DOWN))
        if ni <= 0:
            raise ValueError(f"张数须为整数且 >0，当前对齐后={n}")
        return str(ni)
    dp = _step_decimal_places(step)
    if dp <= 0:
        return str(int(n.to_integral_value(rounding=ROUND_DOWN)))
    qn = n.quantize(Decimal(1).scaleb(-dp), rounding=ROUND_DOWN)
    if qn <= 0:
        raise ValueError(f"张数对齐后为 0")
    return _compact_decimal_str(qn)


def _base_qty_tolerance(qm: Decimal, base: float) -> float:
    qb = abs(float(base))
    qmf = abs(float(qm))
    return max(1e-10, qmf * 1e-6, qb * 1e-8)


def _lots_decimal_for_exact_base(
    qm: Decimal,
    step: Decimal,
    min_sz: Decimal,
    integer_lots_only: bool,
    base: float,
) -> Decimal:
    """lot_size（标的数量）-> 张数；须满足 n×qm == base（在容差内）且满足步长/最小张。"""
    tol = _base_qty_tolerance(qm, base)
    base_d = _decimal_from_float(base)
    if base_d <= 0:
        raise ValueError(f"lot_size 须 > 0，收到 {base:g}")
    ideal = base_d / qm
    if integer_lots_only:
        ideal_f = float(ideal)
        n_int = round(ideal_f)
        if abs(ideal_f - n_int) > max(tol / max(float(qm), 1e-18), 1e-9):
            nearest = max(1, n_int) if n_int <= 0 else n_int
            alt = float(Decimal(nearest) * qm)
            raise ValueError(
                f"lot_size={base:g} 无法表示为整数张×qm={float(qm):g}；"
                f"可改为 {alt:g}（{nearest} 张）或 {float(Decimal(nearest + 1) * qm):g}"
            )
        n = Decimal(n_int)
    else:
        if step <= 0:
            raise ValueError("合约 order_size_round 无效")
        steps = ideal / step
        steps_f = float(steps)
        steps_i = round(steps_f)
        if abs(steps_f - steps_i) > max(tol / max(float(qm) * float(step), 1e-18), 1e-9):
            n_down = Decimal(int(steps_f)) * step if steps_f >= 0 else Decimal("0")
            n_up = n_down + step
            raise ValueError(
                f"lot_size={base:g} 无法按步长 {float(step):g} 精确换算张数；"
                f"邻近可选: {float(n_down * qm):g}（{float(n_down):g} 张）"
                f" 或 {float(n_up * qm):g}（{float(n_up):g} 张）"
            )
        n = Decimal(steps_i) * step
    if min_sz > 0 and n < min_sz:
        min_base = float(min_sz * qm)
        raise ValueError(
            f"lot_size={base:g} 对应 {float(n):g} 张 < 最小 {float(min_sz):g} 张（≈{min_base:g} 标的）；"
            f"请把 lot_size 调到 ≥ {min_base:g}"
        )
    implied = float(n * qm)
    if abs(implied - float(base)) > tol:
        raise ValueError(f"内部错误: lot_size={base:g} 对齐后 implied={implied:g}")
    return n


def gate_lot_order_preview(
    client: "GateFuturesClient",
    contract: str,
    lot_base: float,
    *,
    for_open: bool = False,
    exact: bool = False,
) -> tuple[str, str, float]:
    """返回 (合约名, 张数字符串, 张数×quanto 的标的数量)。

    exact=True（开仓推荐）：张数须使 implied == lot_base，否则 ValueError。
    exact=False：沿用向下/向上对齐（平仓/最小名义搜索）。
    """
    c = symbol_to_contract(contract)
    qm_f = float(client.get_contract(c).get("quanto_multiplier") or 1.0)
    if exact and for_open:
        sz, implied = client.quantize_open_base_exact(c, float(lot_base))
    elif for_open:
        sz = client.quantize_qty_str_open(c, float(lot_base))
        implied = float(sz) * qm_f
    else:
        sz = client.quantize_qty_str(c, float(lot_base))
        implied = float(sz) * qm_f
    return c, sz, implied


def base_qty_for_min_notional_gate(
    client: "GateFuturesClient",
    symbol: str,
    mark: float,
    min_notional_usdt: float,
) -> float:
    """按 min_notional USDT 求引擎 lot_size（标的个数）：Gate 张数可精确表示，且名义 >= min。"""
    if mark <= 1e-12 or min_notional_usdt <= 1e-12:
        return 0.0
    c = symbol_to_contract(symbol)
    qm, step, min_sz, enable_dec, integer_lots_only = client._order_size_rules(c)
    if float(qm) <= 0:
        return 0.0
    target = float(min_notional_usdt)
    mark_f = float(mark)
    eff_min_lots = client.effective_min_open_lots(c)
    floor_notional = float(eff_min_lots * qm * Decimal(str(mark_f)))
    if floor_notional > target + 1e-6:
        raise ValueError(
            f"{c}: 配置 min_open_notional={target:g}U 低于 Gate 最小可下单名义≈{floor_notional:.2f}U "
            f"（最小张数≈{float(eff_min_lots):g}，1张={float(qm):g}标的）"
        )
    raw_lots = target / (mark_f * float(qm))
    if integer_lots_only:
        min_l = int(max(min_sz, eff_min_lots)) if min_sz > 0 else int(max(Decimal(1), eff_min_lots))
        n = Decimal(max(min_l, int(raw_lots) if abs(raw_lots - round(raw_lots)) < 1e-9 else int(raw_lots) + 1))
        if float(n) < raw_lots - 1e-12:
            n = Decimal(int(n) + 1)
        if n < eff_min_lots:
            n = eff_min_lots
        step_inc = Decimal(1)
    else:
        step_d = step if step > 0 else Decimal("0.0001")
        min_l_d = max(min_sz if min_sz > 0 else step_d, eff_min_lots)
        raw_d = _decimal_from_float(raw_lots)
        n = (raw_d / step_d).to_integral_value(rounding=ROUND_UP) * step_d
        if n < min_l_d:
            n = min_l_d
        step_inc = step_d
    for _ in range(5000):
        implied = float(n * qm)
        if implied * mark_f + 1e-9 >= target:
            _sz, implied2 = client.quantize_open_base_exact(c, implied)
            if implied2 * mark_f + 1e-9 >= target:
                return float(implied2)
        n = n + step_inc
    raise ValueError(
        f"{c}: 无法在 Gate 合约步长上满足 min_notional={target:g} USDT（mark={mark_f:g}）"
    )


def symbol_to_contract(symbol: str) -> str:
    """ETHUSDT / ETH_USDT -> ETH_USDT。"""
    s = (symbol or "").strip().upper().replace("-", "_")
    if "_" in s:
        return s
    if s.endswith("USDT"):
        return f"{s[:-4]}_USDT"
    return f"{s}_USDT"


def gate_futures_interval_for_htf_minutes(htf_minutes: int) -> Optional[str]:
    """与 Binance 对齐的 interval 名（Gate USDT 永续 candlesticks）。"""
    m = int(htf_minutes)
    return {
        1: "1m",
        3: "3m",
        5: "5m",
        15: "15m",
        30: "30m",
        60: "1h",
        120: "2h",
        240: "4h",
        360: "6h",
        480: "8h",
        720: "12h",
        1440: "1d",
    }.get(m)


def fetch_gate_mark_price_usdt(contract: str) -> float:
    c = symbol_to_contract(contract)
    r = requests.get(f"{API_V4}/futures/usdt/tickers", params={"contract": c}, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:800]}")
    data = r.json()
    if isinstance(data, list) and data:
        return float(data[0]["mark_price"])
    raise RuntimeError(f"tickers 无数据: {c}")


def _parse_candle_row(o: Dict[str, Any]) -> Dict[str, Any]:
    from datetime import datetime, timezone

    ts = int(float(o["t"]))
    return {
        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
        "open": float(o["o"]),
        "high": float(o["h"]),
        "low": float(o["l"]),
        "close": float(o["c"]),
        "volume": float(o.get("v", 0) or 0),
    }


_GATE_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _gate_interval_seconds(interval: str) -> int:
    s = str(interval or "").strip().lower()
    table = {
        "10s": 10,
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "6h": 21600,
        "8h": 28800,
        "12h": 43200,
        "1d": 86400,
        "7d": 604800,
    }
    if s in table:
        return table[s]
    if s.endswith("s") and s[:-1].isdigit():
        return max(1, int(s[:-1]))
    if s.endswith("m") and s[:-1].isdigit():
        return max(1, int(s[:-1]) * 60)
    if s.endswith("h") and s[:-1].isdigit():
        return max(1, int(s[:-1]) * 3600)
    raise ValueError(f"未知 Gate interval: {interval!r}")


def prune_gate_kline_live_cache(
    *,
    max_bars_per_key: Optional[int] = None,
    max_idle_sec: Optional[float] = None,
    drop_empty: bool = True,
    now: Optional[float] = None,
) -> int:
    """裁掉超出策略窗口的旧 K，并丢掉空流 / 长时间未访问的 key。返回删除根数。"""
    return prune_kline_live_cache(
        prefix="gt:",
        max_bars_per_key=max_bars_per_key,
        max_idle_sec=max_idle_sec,
        drop_empty=drop_empty,
        now=now,
    )


def _merge_gate_klines(*parts: List[Dict]) -> List[Dict]:
    by_ts: Dict[Any, Dict] = {}
    for part in parts:
        for r in part:
            by_ts[r["timestamp"]] = r
    return [by_ts[k] for k in sorted(by_ts)]


def _gate_public_get_json(
    sess: requests.Session,
    url: str,
    params: Dict[str, str],
    *,
    timeout: float = 30.0,
    retries: int = 5,
) -> Any:
    """公开 REST；429/5xx 指数退避。Gate candlesticks 偶发 label=INTERNAL。"""
    last_err: Optional[Exception] = None
    for attempt in range(max(1, int(retries))):
        try:
            r = sess.get(url, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError, OSError) as exc:
            last_err = exc
            time.sleep(0.5 * (2**attempt) + 0.2)
            continue
        if r.status_code in _GATE_RETRY_STATUSES:
            last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:800]}")
            if r.status_code == 429:
                time.sleep(2.0 * (3**min(attempt, 2)) + 1.0)
            else:
                time.sleep(0.5 * (2**attempt) + 0.2)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:800]}")
        try:
            return r.json()
        except ValueError:
            last_err = RuntimeError(f"Gate JSON 解析失败: {r.text[:200]}")
            time.sleep(0.5 * (2**attempt) + 0.2)
            continue
    raise last_err if last_err is not None else RuntimeError("Gate REST 失败")


def fetch_gate_futures_candlesticks_latest(
    contract: str,
    interval: str,
    limit: int,
    *,
    max_per_req: int = 2000,
    session: Optional[requests.Session] = None,
    use_live_cache: bool = False,
) -> List[Dict]:
    """最近 limit 根 K（时间升序）。

    Gate：``limit`` 不能与 ``from``/``to`` 同时出现；单次最多 2000 根。
    第二页起只用 ``from``+``to``。``use_live_cache=True`` 时首拉全量，之后每分钟只补最近 10 根。
    """
    c = symbol_to_contract(contract)
    need = max(1, int(limit))
    cap = min(max(1, int(max_per_req)), 2000)
    cache_key = _kline_cache_key("gt", c, interval)

    if use_live_cache:
        hit = _kline_try_incremental(
            cache_key,
            need,
            fetch_tail=lambda: fetch_gate_futures_candlesticks_latest(
                contract, interval, 10, max_per_req=max_per_req, session=session, use_live_cache=False
            ),
            fetch_full=lambda: fetch_gate_futures_candlesticks_latest(
                contract, interval, need, max_per_req=max_per_req, session=session, use_live_cache=False
            ),
        )
        if hit is not None:
            return hit

    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", "wjfuture-live/1.0")
    sess.headers.setdefault("Accept", "application/json")
    iv = _gate_interval_seconds(interval)
    url = f"{API_V4}/futures/usdt/candlesticks"
    merged: List[Dict] = []
    oldest_open: Optional[int] = None

    while len(merged) < need:
        chunk = min(cap, need - len(merged))
        params: Dict[str, str] = {"contract": c, "interval": interval}
        if oldest_open is None:
            params["limit"] = str(chunk)
        else:
            # 文档：limit 与 from/to 互斥；只用时间窗翻页
            to_ts = int(oldest_open) - 1
            from_ts = to_ts - chunk * iv + 1
            params["from"] = str(max(0, from_ts))
            params["to"] = str(to_ts)
        raw = _gate_public_get_json(sess, url, params)
        if not isinstance(raw, list) or not raw:
            break
        rows = [_parse_candle_row(x) for x in raw if isinstance(x, dict)]
        if not rows:
            break
        rows.sort(key=lambda r: r["timestamp"])
        prev_n = len(merged)
        merged = _merge_gate_klines(rows, merged)
        if len(merged) <= prev_n:
            break
        oldest_open = int(rows[0]["timestamp"].timestamp())
        if "limit" in params and len(rows) < chunk:
            break

    merged = _trim_gate_klines(merged, need)
    if use_live_cache:
        return _kline_cache_store(cache_key, merged, need)
    return merged


def _model_to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_model_to_dict(x) for x in obj]
    fn = getattr(obj, "to_dict", None)
    if callable(fn):
        return fn()
    return obj


def _sha512_hex(body: Any) -> str:
    m = hashlib.sha512()
    if body is None:
        return m.hexdigest()
    if not isinstance(body, six.string_types):
        body = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    m.update(body.encode("utf-8"))
    return m.hexdigest()


def _gate_sign_v4(
    *,
    method: str,
    path: str,
    query_string: str,
    body: Any,
    ts: int,
    secret: str,
) -> str:
    hashed_payload = _sha512_hex(body)
    s = "%s\n%s\n%s\n%s\n%s" % (method.upper(), path, query_string or "", hashed_payload, ts)
    return hmac.new(secret.encode("utf-8"), s.encode("utf-8"), hashlib.sha512).hexdigest()


def _raise_gate_http(status: int, data: Any, *, extra: str = "") -> None:
    label = ""
    msg = ""
    if isinstance(data, dict):
        label = str(data.get("label") or "")
        msg = str(data.get("message") or "")
    head = f"HTTP {status}: {label} {msg}".strip()
    if extra:
        head += extra
    raise RuntimeError(head or f"HTTP {status}")  # noqa: TRY003


def _raise_gate(e: GateApiException, *, extra: str = "") -> None:
    label = str(getattr(e, "label", "") or "")
    msg = f"HTTP {getattr(e, 'status', '?')}: {label} {getattr(e, 'message', '')}".strip()
    if extra:
        msg += extra
    if label == "INVALID_SIGNATURE":
        msg += (
            " — 请核对：1) GATE_API_KEY 与 GATE_API_SECRET 为同一组 v4 密钥且未写反；"
            "2) .env 中勿多一层引号或空格/换行；3) 若密钥绑了 IP 白名单，须包含本机出口 IP。"
        )
    if label in ("INVALID_ARGUMENT", "INVALID_PARAM_VALUE") and (
        "invalid size" in str(getattr(e, "message", "") or "").lower()
        or "order_size_min" in str(getattr(e, "message", "") or "").lower()
    ):
        msg += (
            " — 张数未满足合约 order_size_round / min / max；"
            "请核对 lot 预检与 X-Gate-Size-Decimal；见 gate_lot_order_preview。"
        )
    raise RuntimeError(msg or str(e)) from e


def _gate_position_missing(e: GateApiException) -> bool:
    """对冲模式某合约从未持仓时，查仓位可能 404 或 400 POSITION_NOT_FOUND（视为空仓）。"""
    label = str(getattr(e, "label", "") or "").strip().upper()
    if label in ("POSITION_NOT_FOUND", "POSITION_NOT_EXIST"):
        return True
    st = int(getattr(e, "status", 0) or 0)
    return st == 404


def normalize_gate_credentials(api_key: str, api_secret: str) -> Tuple[str, str]:
    """去掉 BOM、\\r、首尾空白；去掉整段被一对引号包住的值（常见于错误粘贴）。"""
    k = (api_key or "").strip().replace("\r", "").replace("\n", "")
    s = (api_secret or "").strip().replace("\r", "").replace("\n", "")
    for prefix in ("\ufeff", "\ufffe"):
        if k.startswith(prefix):
            k = k[len(prefix) :].strip()
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if len(k) >= 2 and k[0] == k[-1] and k[0] in "\"'":
        k = k[1:-1].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return k, s


class _GateApiClientIntTimestamp(_GateApiClientBase):
    """与常见能跑通的 Gate v4 示例一致：签名字符串末段与 Timestamp 均为整型 Unix 秒。"""

    def gen_sign(self, method, url, query_string=None, body=None):
        # 注意：第二个参数名虽为 url，调用方传入的是 urlparse(full_url).path（含 /api/v4/...）
        t = int(time.time())
        m = hashlib.sha512()
        if body is not None:
            if not isinstance(body, six.string_types):
                body = json.dumps(body)
            m.update(body.encode("utf-8"))
        hashed_payload = m.hexdigest()
        s = "%s\n%s\n%s\n%s\n%s" % (method, url, query_string or "", hashed_payload, t)
        sign = hmac.new(
            self.configuration.secret.encode("utf-8"),
            s.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {"KEY": self.configuration.key, "Timestamp": str(t), "SIGN": sign}


class GateFuturesClient:
    def __init__(self, api_key: str, api_secret: str):
        key, secret = normalize_gate_credentials(api_key, api_secret)
        self.api_key = key
        self.api_secret = secret
        self._session = requests.Session()
        cfg = Configuration(key=key, secret=secret, host=f"{API_V4}")
        self._api_client = _GateApiClientIntTimestamp(cfg)
        self._api_client.default_headers["X-Gate-Size-Decimal"] = "1"
        self._futures = FuturesApi(self._api_client)
        self._settle = SETTLE
        self._contract_rules: Dict[str, Dict[str, Any]] = {}

    def get_contract(self, contract: str) -> Dict[str, Any]:
        c = symbol_to_contract(contract)
        r = self._session.get(f"{API_V4}/futures/usdt/contracts/{c}", timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:800]}")
        return r.json()

    def _order_size_rules(self, contract: str) -> tuple[Decimal, Decimal, Decimal, bool, bool]:
        c = symbol_to_contract(contract)
        cached = self._contract_rules.get(c)
        if cached is None:
            info = self.get_contract(c)
            qm = Decimal(str(info.get("quanto_multiplier") or "1"))
            osr_raw = info.get("order_size_round")
            enable_dec = bool(info.get("enable_decimal"))
            if osr_raw is not None and str(osr_raw).strip() != "":
                step = Decimal(str(osr_raw))
            elif enable_dec:
                # 经验规则：
                # - qm==1：整张步长 1
                # - qm>=10：多数币种要求「标的数量为 10 的整数倍」，等价于张数步长 10/qm（ESPORTS/LAB 等）
                # - 其它：保守给 0.0001
                if qm == Decimal("1"):
                    step = Decimal("1")
                elif qm >= Decimal("10"):
                    step = Decimal("10") / qm
                else:
                    step = Decimal("0.0001")
            else:
                step = Decimal("1")
            osm_raw = info.get("order_size_min")
            min_sz = (
                Decimal(str(osm_raw))
                if osm_raw is not None and str(osm_raw) != ""
                else Decimal("0")
            )
            integer_lots_only = not enable_dec
            if integer_lots_only:
                step = Decimal("1")
                if min_sz <= 0:
                    min_sz = Decimal("1")
            elif min_sz <= 0 and enable_dec and qm == Decimal("1"):
                min_sz = Decimal("1")
            elif min_sz <= 0 and enable_dec and qm > Decimal("1") and step > 0:
                min_sz = step
            osx_raw = info.get("order_size_max")
            max_sz = (
                Decimal(str(osx_raw))
                if osx_raw is not None and str(osx_raw).strip() != ""
                else Decimal("0")
            )
            cached = {
                "qm": qm,
                "step": step,
                "min_sz": min_sz,
                "max_sz": max_sz,
                "enable_dec": enable_dec,
                "integer_lots_only": integer_lots_only,
                "contract": c,
            }
            self._contract_rules[c] = cached
        return (
            cached["qm"],
            cached["step"],
            cached["min_sz"],
            cached["enable_dec"],
            cached["integer_lots_only"],
        )

    def _order_size_max(self, contract: str) -> Decimal:
        c = symbol_to_contract(contract)
        if c not in self._contract_rules:
            self._order_size_rules(c)
        return Decimal(str(self._contract_rules[c].get("max_sz") or "0"))

    @staticmethod
    def _coerce_lots_decimal(lots: Union[float, str, Decimal]) -> Decimal:
        if isinstance(lots, Decimal):
            return abs(lots)
        if isinstance(lots, str):
            s = lots.strip().lstrip("+-")
            return abs(Decimal(s)) if s else Decimal(0)
        return abs(_decimal_from_float(float(lots)))

    def effective_min_open_lots(self, contract: str) -> Decimal:
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        if integer_lots_only:
            return max(min_sz, Decimal(1))
        eff = min_sz if min_sz > 0 else Decimal(0)
        if step > 0:
            eff = max(eff, step)
        if enable_dec and qm >= Decimal(10):
            min_lots = Decimal(10) / qm
            if step > 0:
                min_lots = (min_lots / step).to_integral_value(rounding=ROUND_UP) * step
            eff = max(eff, min_lots)
        elif enable_dec:
            eff = max(eff, Decimal(1))
        return eff

    def min_open_notional_floor_usdt(self, contract: str, mark: float) -> float:
        qm, _s, _m, _e, _i = self._order_size_rules(contract)
        return float(self.effective_min_open_lots(contract) * qm * Decimal(str(mark)))

    def min_lot_base_hint(self, contract: str) -> str:
        """lot_size 至少应达到的标的数量（便于 manifest 配置）。"""
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        min_sz = self.effective_min_open_lots(contract)
        base = float(min_sz * qm)
        c = symbol_to_contract(contract)
        if enable_dec:
            return f"{c}: lot_size >= {base:g} (≈{min_sz} 张, qm={qm})"
        return f"{c}: lot_size >= {base:g} (整数张, min={int(min_sz)} 张, qm={qm})"

    def _align_contract_lots(
        self,
        units: Decimal,
        step: Decimal,
        min_sz: Decimal,
        enable_dec: bool,
        integer_lots_only: bool,
        *,
        round_up: bool,
    ) -> Decimal:
        mode = ROUND_UP if round_up else ROUND_DOWN
        if integer_lots_only:
            n = units.to_integral_value(rounding=mode)
            if round_up and n < Decimal("1"):
                n = Decimal("1")
        else:
            n = (units / step).to_integral_value(rounding=mode) * step
        if min_sz > 0 and n < min_sz:
            n = min_sz if round_up else Decimal("0")
        if round_up and step >= Decimal("1") and not integer_lots_only:
            n = max(n, Decimal("1"))
            if not enable_dec:
                n = n.to_integral_value(rounding=ROUND_UP)
        elif not round_up and step >= Decimal("1") and not enable_dec and not integer_lots_only:
            n = n.to_integral_value(rounding=ROUND_DOWN)
        if n <= 0:
            raise ValueError("张数对齐后为 0")
        return n

    def _clamp_lots_to_contract_max(self, contract: str, n: Decimal) -> Decimal:
        max_sz = self._order_size_max(contract)
        if max_sz > 0 and n > max_sz:
            raise ValueError(
                f"{symbol_to_contract(contract)}: 张数 {float(n):g} > order_size_max {float(max_sz):g}"
            )
        return n

    def quantize_qty_str(self, contract: str, qty_base: float) -> str:
        """base 数量 -> 张数（字符串，与 Gate 精度一致，向下取整）。"""
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        if qm <= 0:
            raise ValueError("quanto_multiplier 无效")
        units = _decimal_from_float(qty_base) / qm
        try:
            n = self._align_contract_lots(
                units, step, min_sz, enable_dec, integer_lots_only, round_up=False
            )
        except ValueError as e:
            raise ValueError(
                f"数量 {qty_base} 换算张数为 0；{self.min_lot_base_hint(contract)}"
            ) from e
        fmt_dec = enable_dec and not integer_lots_only
        return _format_gate_contract_size(n, step, fmt_dec)

    def quantize_qty_str_open(self, contract: str, qty_base: float) -> str:
        """开仓：base -> 张数，向上对齐并满足最小张（避免 35 标的量 -> 0.35 张被拒）。"""
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        if qm <= 0:
            raise ValueError("quanto_multiplier 无效")
        units = _decimal_from_float(qty_base) / qm
        try:
            n = self._align_contract_lots(
                units, step, min_sz, enable_dec, integer_lots_only, round_up=True
            )
        except ValueError as e:
            raise ValueError(
                f"开仓数量 {qty_base} 换算张数为 0；{self.min_lot_base_hint(contract)}"
            ) from e
        fmt_dec = enable_dec and not integer_lots_only
        return _format_gate_contract_size(n, step, fmt_dec)

    def quantize_open_base_exact(self, contract: str, qty_base: float) -> tuple[str, float]:
        """开仓：lot_size（标的个数）-> Gate 张数，须 implied == qty_base（manifest 一手量）。"""
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        if qm <= 0:
            raise ValueError("quanto_multiplier 无效")
        n = _lots_decimal_for_exact_base(qm, step, min_sz, integer_lots_only, float(qty_base))
        n = self._clamp_lots_to_contract_max(contract, n)
        fmt_dec = enable_dec and not integer_lots_only
        sz = _format_gate_contract_size(n, step, fmt_dec)
        implied = float(n * qm)
        return sz, implied

    def quantize_open_order_base(self, contract: str, qty_base: float) -> tuple[str, float]:
        """实盘开仓：精确标的 + 不低于 Gate 有效最小张数（如 LAB 0.1 张）。"""
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        eff_min = self.effective_min_open_lots(contract)
        qty_f = max(float(qty_base), float(eff_min * qm))
        sz, implied = self.quantize_open_base_exact(contract, qty_f)
        lots = self._coerce_lots_decimal(sz)
        if lots < eff_min:
            lots = self._clamp_lots_to_contract_max(contract, eff_min)
            fmt_dec = enable_dec and not integer_lots_only
            sz = _format_gate_contract_size(lots, step, fmt_dec)
            implied = float(lots * qm)
        return sz, implied

    def _format_lots_str_passthrough(self, contract: str, lots: Decimal) -> str:
        qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        mag = abs(lots)
        if mag <= 0:
            raise ValueError("张数为 0")
        eff_min = min_sz if min_sz > 0 else (step if step > 0 else Decimal(0))
        if eff_min > 0 and mag < eff_min:
            mag = eff_min
        mag = self._clamp_lots_to_contract_max(contract, mag)
        if step > 0 and not integer_lots_only:
            q, r = divmod(mag, step)
            if r != 0:
                mag = (q + Decimal(1)) * step
                mag = self._clamp_lots_to_contract_max(contract, mag)
        fmt_dec = enable_dec and not integer_lots_only
        return _format_gate_contract_size(mag, step, fmt_dec)

    def quantize_contract_lots_str(
        self,
        contract: str,
        lots: Union[float, str, Decimal],
        *,
        round_up: bool = False,
    ) -> str:
        """张数对齐；用 Decimal 避免 float(0.029) 向下变成 0.028。"""
        _qm, step, min_sz, enable_dec, integer_lots_only = self._order_size_rules(contract)
        units = self._coerce_lots_decimal(lots)
        if units <= 0:
            raise ValueError(f"张数 {lots} 无效")
        try:
            n = self._align_contract_lots(
                units, step, min_sz, enable_dec, integer_lots_only, round_up=round_up
            )
            n = self._clamp_lots_to_contract_max(contract, n)
        except ValueError as e:
            raise ValueError(
                f"张数 {lots} 对齐后为 0；{self.min_lot_base_hint(contract)}"
            ) from e
        fmt_dec = enable_dec and not integer_lots_only
        return _format_gate_contract_size(n, step, fmt_dec)

    def _signed_order_size_str(
        self, contract: str, size: float, *, round_up: bool = False
    ) -> str:
        """带符号张数字符串（与 create_futures_order.size 一致），按合约步长对齐。"""
        sz_f = float(size)
        if abs(sz_f) < 1e-12:
            raise RuntimeError(f"Gate 下单张数为 0 或过小({size})")
        sign = -1 if sz_f < 0 else 1
        mag_str = self.quantize_contract_lots_str(
            contract, _decimal_from_float(abs(sz_f)), round_up=round_up
        )
        if sign < 0:
            return f"-{mag_str}" if not mag_str.startswith("-") else mag_str
        return mag_str

    @staticmethod
    def _account_is_dual(data: Dict[str, Any]) -> bool:
        """兼容账户字段多种命名。"""
        if bool(data.get("dual_mode")):
            return True
        if bool(data.get("in_dual_mode")) or bool(data.get("inDualMode")):
            return True
        if bool(data.get("enable_new_dual_mode")):
            return True
        pm = data.get("position_mode") or data.get("positionMode") or ""
        s = str(pm).lower()
        if s in ("dual", "dual_mode", "hedge", "dual_long_short"):
            return True
        return False

    def get_dual_mode(self) -> bool:
        try:
            acc = self._futures.list_futures_accounts(self._settle)
        except GateApiException as e:
            _raise_gate(e)
        data = _model_to_dict(acc)
        if isinstance(data, dict):
            return self._account_is_dual(data)
        if isinstance(data, list) and data:
            row = data[0] if isinstance(data[0], dict) else {}
            return self._account_is_dual(row) if isinstance(row, dict) else False
        return False

    def set_dual_mode(self, *, dual: bool = True) -> Any:
        try:
            acc = self._futures.set_dual_mode(self._settle, bool(dual))
        except GateApiException as e:
            _raise_gate(e)
        return _model_to_dict(acc)

    def set_leverage(self, contract: str, leverage: int) -> Any:
        c = symbol_to_contract(contract)
        lev = str(int(leverage))
        try:
            if self.get_dual_mode():
                pos = self._futures.update_dual_mode_position_leverage(self._settle, c, lev)
            else:
                pos = self._futures.update_position_leverage(self._settle, c, lev)
        except GateApiException as e:
            _raise_gate(e)
        return _model_to_dict(pos)

    def get_position_contract_lots(self, contract: str) -> Tuple[float, float]:
        """返回 (多头张数, 空头张数)，非负；与 create_futures_order 的 size 字段同单位。"""
        c = symbol_to_contract(contract)

        def _signed_json(method: str, path: str, query: str = "", body: Any = None) -> Any:
            ts = int(time.time())
            sign = _gate_sign_v4(
                method=method,
                path=path,
                query_string=query,
                body=body,
                ts=ts,
                secret=self.api_secret,
            )
            headers = {
                "KEY": self.api_key,
                "Timestamp": str(ts),
                "SIGN": sign,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Gate-Size-Decimal": "1",
            }
            url = f"{API_HOST}{path}" if path.startswith("/api/") else f"{API_V4}{path}"
            r = self._session.request(
                method.upper(),
                url,
                params=(dict(x.split("=", 1) for x in query.split("&") if x) if query else None),
                json=body,
                headers=headers,
                timeout=20,
            )
            try:
                data = r.json()
            except Exception:
                data = r.text
            if r.status_code >= 400:
                if isinstance(data, dict) and str(data.get("label", "")).upper() in (
                    "POSITION_NOT_FOUND",
                    "POSITION_NOT_EXIST",
                ):
                    return []
                if r.status_code == 404:
                    return []
                _raise_gate_http(
                    r.status_code,
                    data if isinstance(data, dict) else {"label": "", "message": str(data)[:500]},
                    extra=f" path={path} contract={c}",
                )
            return data

        long_lots = 0.0
        short_lots = 0.0

        dual_rows: List[Dict[str, Any]] = []
        for dual_path in (
            f"/api/v4/futures/{self._settle}/dual_comp/positions/{c}",
            f"/api/v4/futures/{self._settle}/dual_mode/positions/{c}",
        ):
            rows = _signed_json("GET", dual_path)
            if isinstance(rows, list) and rows:
                dual_rows = [r for r in rows if isinstance(r, dict)]
                break
        for row in dual_rows:
            try:
                sz = float(row.get("size") or 0.0)
            except (TypeError, ValueError):
                continue
            mode = str(row.get("mode") or "").lower()
            mag = abs(sz)
            if mag <= 1e-12:
                continue
            if "long" in mode:
                long_lots += mag
            elif "short" in mode:
                short_lots += mag
            elif sz > 0:
                long_lots += mag
            elif sz < 0:
                short_lots += mag

        if long_lots <= 1e-12 and short_lots <= 1e-12:
            path = f"/api/v4/futures/{self._settle}/positions/{c}"
            d = _signed_json("GET", path)
            if isinstance(d, dict):
                try:
                    sz = float(d.get("size") or 0.0)
                except (TypeError, ValueError):
                    sz = 0.0
                if sz > 0:
                    long_lots = sz
                elif sz < 0:
                    short_lots = -sz

        # 兜底：某些账户/接口形态下单合约查询会空，改用 holding=true 全量持仓再筛合约。
        if long_lots <= 1e-12 and short_lots <= 1e-12:
            path_all = f"/api/v4/futures/{self._settle}/positions"
            rows_all = _signed_json("GET", path_all, query="holding=true")
            if isinstance(rows_all, list):
                for row in rows_all:
                    if not isinstance(row, dict):
                        continue
                    rc = str(row.get("contract") or "").upper()
                    if rc != c.upper():
                        continue
                    mode = str(row.get("mode") or "").lower()
                    try:
                        sz = float(row.get("size") or 0.0)
                    except (TypeError, ValueError):
                        sz = 0.0
                    mag = abs(sz)
                    if mag <= 1e-12:
                        continue
                    if "long" in mode:
                        long_lots += mag
                    elif "short" in mode:
                        short_lots += mag
                    elif sz > 0:
                        long_lots += mag
                    elif sz < 0:
                        short_lots += mag
        return (long_lots, short_lots)

    def get_position_snapshot(self, contract: str) -> Dict[str, Dict[str, float]]:
        """返回 {'long': {'lots','qty_base','entry_price'}, 'short': {...}}。"""
        c = symbol_to_contract(contract)
        out: Dict[str, Dict[str, float]] = {}
        debug_on = _env_truthy("GATE_SNAPSHOT_DEBUG")

        def _signed_json(method: str, path: str, query: str = "", body: Any = None) -> Any:
            ts = int(time.time())
            sign = _gate_sign_v4(
                method=method,
                path=path,
                query_string=query,
                body=body,
                ts=ts,
                secret=self.api_secret,
            )
            headers = {
                "KEY": self.api_key,
                "Timestamp": str(ts),
                "SIGN": sign,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Gate-Size-Decimal": "1",
            }
            url = f"{API_HOST}{path}" if path.startswith("/api/") else f"{API_V4}{path}"
            r = self._session.request(
                method.upper(),
                url,
                params=(dict(x.split("=", 1) for x in query.split("&") if x) if query else None),
                json=body,
                headers=headers,
                timeout=20,
            )
            try:
                data = r.json()
            except Exception:
                data = r.text
            if r.status_code >= 400:
                if isinstance(data, dict) and str(data.get("label", "")).upper() in (
                    "POSITION_NOT_FOUND",
                    "POSITION_NOT_EXIST",
                ):
                    return []
                if r.status_code == 404:
                    return []
                _raise_gate_http(
                    r.status_code,
                    data if isinstance(data, dict) else {"label": "", "message": str(data)[:500]},
                    extra=f" path={path} contract={c}",
                )
            return data

        try:
            info = self.get_contract(c)
            try:
                qm = float(info.get("quanto_multiplier") or 1.0)
            except (TypeError, ValueError):
                qm = 1.0
            if qm <= 0:
                qm = 1.0
            if debug_on:
                print(
                    f"[wjfuture][gate-debug] snapshot start contract={c} settle={self._settle} "
                    f"key={_mask_key(self.api_key)} qm={qm:g}",
                    file=sys.stderr,
                )
            dual_rows: List[Dict[str, Any]] = []
            for dual_path in (
                f"/api/v4/futures/{self._settle}/dual_comp/positions/{c}",
                f"/api/v4/futures/{self._settle}/dual_mode/positions/{c}",
            ):
                rows = _signed_json("GET", dual_path)
                if isinstance(rows, list) and rows:
                    dual_rows = [r for r in rows if isinstance(r, dict)]
                    if debug_on:
                        print(
                            f"[wjfuture][gate-debug] dual_path hit={dual_path} rows={len(dual_rows)}",
                            file=sys.stderr,
                        )
                    break
            rows = dual_rows
            if debug_on:
                sample = rows[0] if rows else {}
                try:
                    sample_s = json.dumps(sample, ensure_ascii=False)[:400]
                except Exception:
                    sample_s = str(sample)[:400]
                print(
                    f"[wjfuture][gate-debug] dual_comp rows={len(rows)} sample={sample_s}",
                    file=sys.stderr,
                )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mode = str(row.get("mode") or "").lower()
                try:
                    sz = float(row.get("size") or 0.0)
                except (TypeError, ValueError):
                    sz = 0.0
                lots = abs(sz)
                if lots <= 1e-12:
                    continue
                side: Optional[str] = None
                if "long" in mode:
                    side = "long"
                elif "short" in mode:
                    side = "short"
                elif sz > 0:
                    side = "long"
                elif sz < 0:
                    side = "short"
                if side is None:
                    continue
                try:
                    ep = float(row.get("entry_price") or row.get("entryPrice") or 0.0)
                except (TypeError, ValueError):
                    ep = 0.0
                cur = out.get(side)
                if cur is None:
                    out[side] = {"lots": lots, "qty_base": lots * qm, "entry_price": ep}
                else:
                    l0 = float(cur.get("lots", 0.0))
                    p0 = float(cur.get("entry_price", 0.0))
                    l1 = l0 + lots
                    if l1 > 1e-12:
                        if ep > 1e-12 and p0 > 1e-12:
                            cur["entry_price"] = (p0 * l0 + ep * lots) / l1
                        elif ep > 1e-12:
                            cur["entry_price"] = ep
                    cur["lots"] = l1
                    cur["qty_base"] = l1 * qm

            if not out:
                path = f"/api/v4/futures/{self._settle}/positions/{c}"
                d = _signed_json("GET", path)
                if debug_on:
                    try:
                        d_s = json.dumps(d, ensure_ascii=False)[:400]
                    except Exception:
                        d_s = str(d)[:400]
                    print(
                        f"[wjfuture][gate-debug] single_position payload={d_s}",
                        file=sys.stderr,
                    )
                if isinstance(d, dict):
                    try:
                        sz = float(d.get("size") or 0.0)
                    except (TypeError, ValueError):
                        sz = 0.0
                    lots = abs(sz)
                    if lots > 1e-12:
                        side = "long" if sz > 0 else "short"
                        try:
                            ep = float(d.get("entry_price") or d.get("entryPrice") or 0.0)
                        except (TypeError, ValueError):
                            ep = 0.0
                        out[side] = {"lots": lots, "qty_base": lots * qm, "entry_price": ep}
            if not out:
                path_all = f"/api/v4/futures/{self._settle}/positions"
                rows_all = _signed_json("GET", path_all, query="holding=true")
                if debug_on:
                    try:
                        all_s = json.dumps(rows_all[0] if isinstance(rows_all, list) and rows_all else {}, ensure_ascii=False)[:400]
                    except Exception:
                        all_s = str(rows_all)[:400]
                    n_all = len(rows_all) if isinstance(rows_all, list) else -1
                    print(
                        f"[wjfuture][gate-debug] holding_positions n={n_all} sample={all_s}",
                        file=sys.stderr,
                    )
                if isinstance(rows_all, list):
                    for row in rows_all:
                        if not isinstance(row, dict):
                            continue
                        rc = str(row.get("contract") or "").upper()
                        if rc != c.upper():
                            continue
                        mode = str(row.get("mode") or "").lower()
                        try:
                            sz = float(row.get("size") or 0.0)
                        except (TypeError, ValueError):
                            sz = 0.0
                        lots = abs(sz)
                        if lots <= 1e-12:
                            continue
                        side: Optional[str] = None
                        if "long" in mode:
                            side = "long"
                        elif "short" in mode:
                            side = "short"
                        elif sz > 0:
                            side = "long"
                        elif sz < 0:
                            side = "short"
                        if side is None:
                            continue
                        try:
                            ep = float(row.get("entry_price") or row.get("entryPrice") or 0.0)
                        except (TypeError, ValueError):
                            ep = 0.0
                        cur = out.get(side)
                        if cur is None:
                            out[side] = {"lots": lots, "qty_base": lots * qm, "entry_price": ep}
                        else:
                            l0 = float(cur.get("lots", 0.0))
                            p0 = float(cur.get("entry_price", 0.0))
                            l1 = l0 + lots
                            if l1 > 1e-12:
                                if ep > 1e-12 and p0 > 1e-12:
                                    cur["entry_price"] = (p0 * l0 + ep * lots) / l1
                                elif ep > 1e-12:
                                    cur["entry_price"] = ep
                            cur["lots"] = l1
                            cur["qty_base"] = l1 * qm
            if debug_on:
                print(f"[wjfuture][gate-debug] snapshot parsed={out}", file=sys.stderr)
        except RuntimeError:
            raise
        except GateApiException as e:
            if not _gate_position_missing(e):
                _raise_gate(e)
        return out

    def list_holding_positions(self) -> List[Dict[str, Any]]:
        """当前 USDT 永续非空仓列表（holding=True）；元素为 Position 字典。"""
        try:
            raw = self._futures.list_positions(self._settle, holding=True)
        except GateApiException as e:
            _raise_gate(e)
        rows = _model_to_dict(raw)
        if not isinstance(rows, list):
            return []
        return [x for x in rows if isinstance(x, dict)]

    def emergency_flatten_contract(self, contract: str, *, max_rounds: int = 4) -> None:
        """按当前快照反复对多空两侧下 reduce_only 市价，尽量把该合约减到无仓（用于部分平仓失败后的风控兜底）。"""
        c = symbol_to_contract(contract)
        for _ in range(max(1, int(max_rounds))):
            long_lots, short_lots = self.get_position_contract_lots(c)
            if long_lots <= 1e-12 and short_lots <= 1e-12:
                return
            if long_lots > 1e-12:
                self.market_order(c, size=-float(long_lots), reduce_only=True)
            long_lots2, short_lots2 = self.get_position_contract_lots(c)
            if short_lots2 > 1e-12:
                self.market_order(c, size=float(short_lots2), reduce_only=True)

    def _normalize_order_size_str(
        self,
        contract: str,
        size: Union[float, str, int],
        *,
        round_up: bool = False,
    ) -> str:
        """统一为带符号、步长对齐的紧凑张数字符串（保留 Decimal）。"""
        if isinstance(size, str):
            s = size.strip()
            if not s:
                raise RuntimeError("Gate 下单 size 为空字符串")
            try:
                v = Decimal(s)
            except Exception as e:
                raise RuntimeError(f"Gate 下单 size 无法解析: {size!r}") from e
            if v == 0:
                raise RuntimeError(f"Gate 下单张数为 0 或过小({size!r})")
            sign = -1 if v < 0 else 1
            mag_str = self._format_lots_str_passthrough(contract, abs(v))
            return f"-{mag_str}" if sign < 0 else mag_str
        return self._signed_order_size_str(contract, float(size), round_up=round_up)

    def market_order(
        self,
        contract: str,
        *,
        size: Union[float, str, int],
        reduce_only: bool = False,
        text: str = "",
    ) -> Dict[str, Any]:
        # NOTE: gate_api SDK 某些版本会把 size 当 int 反序列化，遇到小数张（如 -9.4）会崩溃。
        # 这里直接走 HTTP + 签名，避免 SDK 反序列化。
        c = symbol_to_contract(contract)
        sz_s = self._normalize_order_size_str(
            c, size, round_up=not reduce_only and not isinstance(size, str)
        )
        payload: Dict[str, Any] = {
            "contract": c,
            "size": sz_s,
            "price": "0",
            "tif": "ioc",
            "reduce_only": bool(reduce_only),
        }
        if text:
            payload["text"] = str(text)[:28]
        path = f"/api/v4/futures/{self._settle}/orders"
        ts = int(time.time())
        body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        sign = _gate_sign_v4(
            method="POST",
            path=path,
            query_string="",
            body=body_str,
            ts=ts,
            secret=self.api_secret,
        )
        headers = {
            "KEY": self.api_key,
            "Timestamp": str(ts),
            "SIGN": sign,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Gate-Size-Decimal": "1",
        }
        r = self._session.post(
            f"{API_V4}/futures/{self._settle}/orders",
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=20,
        )
        data = None
        try:
            data = r.json()
        except Exception:
            data = r.text
        if r.status_code >= 400:
            _raise_gate_http(
                r.status_code,
                data if isinstance(data, dict) else {"label": "", "message": str(data)[:500]},
                extra=f" contract={c} size={sz_s!r} reduce_only={reduce_only}",
            )
        return data if isinstance(data, dict) else {}

    def get_order(self, contract: str, order_id: int) -> Dict[str, Any]:
        c = symbol_to_contract(contract)
        try:
            resp = self._futures.get_futures_order(self._settle, str(order_id), c)
        except GateApiException as e:
            _raise_gate(e)
        out = _model_to_dict(resp)
        if isinstance(out, dict):
            return out
        return {}


def fill_to_gate_order_params(
    contract: str,
    fl_action: str,
    fl_side: str,
    qty_base: float,
    quantize,
) -> Dict[str, Any]:
    """paper Fill -> Gate 市价 size（正买负卖，紧凑张数字符串）与 reduce_only。"""
    mag_s = str(quantize(qty_base)).lstrip("+")
    if fl_action == "open":
        if fl_side == "long":
            return {"size": mag_s, "reduce_only": False}
        return {"size": f"-{mag_s}", "reduce_only": False}
    if fl_action == "close":
        if fl_side == "long":
            return {"size": f"-{mag_s}", "reduce_only": True}
        return {"size": mag_s, "reduce_only": True}
    raise ValueError(f"未知 fill action={fl_action}")
