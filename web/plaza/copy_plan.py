"""跟单数量和该不该下。这里不访问交易所。"""

from __future__ import annotations

from dataclasses import dataclass, field

RATIO_CAP = 0.05
CHASE = 0.005
BUMP_CAP = 0.25
DAY_HALT = 0.03
FUSE_EQUITY = 0.015
FUSE_PRICE = 0.08
RULES = (
    "按权益比例下单，比例最高 5%。开仓价差超过 0.5% 不追。"
    "减仓和平仓始终跟。浮亏到权益 1.5% 或价格逆向 8% 会平仓。"
    "当天回撤 3% 不再开新仓。开始跟单时不补他已有的仓。"
)


@dataclass
class LotState:
    side: str = ""
    qty: float = 0.0
    ratio: float | None = None
    entry: float = 0.0
    pending: float = 0.0


@dataclass
class Intent:
    kind: str
    side: str
    qty: float
    reduce_only: bool
    ratio: float | None = None


@dataclass
class Decision:
    intents: list[Intent] = field(default_factory=list)
    lot: LotState = field(default_factory=LotState)
    status: str = "skip"
    detail: str = ""


def apply_intent(lot: LotState, intent: Intent, px: float) -> LotState:
    if intent.kind == "open":
        return LotState(
            side=intent.side,
            qty=lot.qty + intent.qty,
            ratio=intent.ratio,
            entry=_avg(lot.qty, lot.entry, intent.qty, px),
            pending=0.0,
        )
    left = lot.qty - intent.qty
    if left <= 1e-12:
        return LotState()
    return LotState(lot.side, left, lot.ratio, lot.entry, 0.0)


def base_coin(coin: str) -> str:
    text = (coin or "").strip()
    if ":" in text:
        text = text.split(":")[-1]
    return text.upper()


def fresh_ratio(follower_equity: float, leader_equity: float) -> float | None:
    if follower_equity <= 0 or leader_equity <= 0:
        return None
    return min(follower_equity / leader_equity, RATIO_CAP)


def chased(side: str, px: float, mark: float) -> bool:
    if px <= 0 or mark <= 0:
        return True
    if side == "short":
        return mark < px * (1 - CHASE)
    return mark > px * (1 + CHASE)


def fuse_hit(lot: LotState, mark: float, follower_equity: float) -> bool:
    if lot.qty <= 1e-12 or lot.entry <= 0 or mark <= 0:
        return False
    if lot.side == "short":
        adverse = mark >= lot.entry * (1 + FUSE_PRICE)
        loss = max(0.0, (mark - lot.entry) * lot.qty)
    else:
        adverse = mark <= lot.entry * (1 - FUSE_PRICE)
        loss = max(0.0, (lot.entry - mark) * lot.qty)
    if adverse:
        return True
    return follower_equity > 0 and loss >= follower_equity * FUSE_EQUITY


def _min_base(min_qty: float, min_notional: float, px: float) -> float:
    floor = max(0.0, min_qty)
    if min_notional > 0 and px > 0:
        floor = max(floor, min_notional / px)
    return floor


def _bump(raw: float, floor: float, px: float, follower_equity: float) -> tuple[float, str]:
    qty = raw
    if floor > 0 and qty < floor:
        qty = floor
    if qty <= 0:
        return 0.0, "数量是 0"
    if raw + 1e-12 < qty and px > 0 and follower_equity > 0:
        if qty * px > follower_equity * BUMP_CAP:
            return 0.0, "抬到最小下单量会超过权益的 25%"
    return qty, ""


def _avg(old_qty: float, old_entry: float, add_qty: float, px: float) -> float:
    total = old_qty + add_qty
    if total <= 0:
        return 0.0
    if old_qty <= 0 or old_entry <= 0:
        return px
    return (old_qty * old_entry + add_qty * px) / total


def decide(
    *,
    action: str,
    side: str,
    leader_delta: float,
    leader_now: float,
    px: float,
    mark: float,
    follower_equity: float,
    leader_equity: float,
    lot: LotState,
    min_qty: float = 0.0,
    min_notional: float = 0.0,
    halted: bool = False,
) -> Decision:
    state = LotState(lot.side, lot.qty, lot.ratio, lot.entry, lot.pending)
    floor = _min_base(min_qty, min_notional, px)
    if state.qty <= 1e-12 and action != "open":
        return Decision(lot=state, detail="启动时不跟已有仓")
    if halted and action in ("open", "add"):
        return Decision(lot=state, detail="当天回撤已到 3%，停止新开仓")

    if action in ("open", "add") or (action == "flip" and state.qty <= 1e-12):
        if px <= 0 or mark <= 0:
            return Decision(lot=state, detail="没有标记价")
        if chased(side, px, mark):
            return Decision(lot=state, detail="开仓价差超过 0.5%，不追")
        ratio = state.ratio if state.qty > 1e-12 and state.ratio else fresh_ratio(follower_equity, leader_equity)
        if ratio is None:
            return Decision(lot=state, detail="权益不足，算不出比例")
        raw = abs(leader_delta) * ratio
        qty, reason = _bump(raw, floor, px, follower_equity)
        if qty <= 0:
            return Decision(lot=state, detail=reason or "数量是 0")
        state.side = side
        state.entry = _avg(state.qty, state.entry, qty, px)
        state.qty += qty
        state.ratio = ratio
        state.pending = 0.0
        return Decision(
            intents=[Intent("open", side, qty, False, ratio)],
            lot=state,
            status="sent",
            detail="开仓" if action == "open" else "加仓",
        )

    if action == "reduce":
        if state.side and side != state.side:
            return Decision(lot=state, detail="方向不一致")
        ratio = state.ratio or fresh_ratio(follower_equity, leader_equity) or 0.0
        want = min(state.qty, abs(leader_delta) * ratio + state.pending)
        if want + 1e-12 < state.qty and floor > 0 and want < floor:
            state.pending += abs(leader_delta) * ratio
            return Decision(lot=state, detail="减仓还没到最小下单量")
        qty = min(state.qty, want)
        if qty <= 1e-12:
            return Decision(lot=state, detail="没有可减的仓")
        held = state.side or side
        state.qty -= qty
        state.pending = 0.0
        if state.qty <= 1e-12:
            state.qty = 0.0
            state.side = ""
            state.ratio = None
            state.entry = 0.0
        return Decision(
            intents=[Intent("reduce", held, qty, True)],
            lot=state,
            status="sent",
            detail="减仓",
        )

    if action == "close":
        if state.qty <= 1e-12:
            return Decision(lot=state, detail="没有可平的仓")
        qty = state.qty
        closing = state.side or side
        state.qty = 0.0
        state.side = ""
        state.ratio = None
        state.entry = 0.0
        state.pending = 0.0
        return Decision(
            intents=[Intent("close", closing, qty, True)],
            lot=state,
            status="sent",
            detail="平仓",
        )

    if action == "flip":
        closing = state.side or ("short" if side == "long" else "long")
        close_qty = state.qty
        ratio = fresh_ratio(follower_equity, leader_equity)
        if ratio is None:
            state.qty = 0.0
            state.side = ""
            state.ratio = None
            state.entry = 0.0
            state.pending = 0.0
            return Decision(
                intents=[Intent("close", closing, close_qty, True)],
                lot=state,
                status="sent",
                detail="翻仓时权益不足，只平旧仓",
            )
        if px <= 0 or mark <= 0 or chased(side, px, mark):
            state.qty = 0.0
            state.side = ""
            state.ratio = None
            state.entry = 0.0
            state.pending = 0.0
            return Decision(
                intents=[Intent("close", closing, close_qty, True)],
                lot=state,
                status="sent",
                detail="翻仓的新开仓价差超过 0.5%，只平旧仓",
            )
        raw = abs(leader_now) * ratio
        qty, reason = _bump(raw, floor, px, follower_equity)
        intents = [Intent("close", closing, close_qty, True)]
        state.pending = 0.0
        if qty <= 0:
            state.qty = 0.0
            state.side = ""
            state.ratio = None
            state.entry = 0.0
            return Decision(intents=intents, lot=state, status="sent", detail=reason or "翻仓只平旧仓")
        state.side = side
        state.qty = qty
        state.ratio = ratio
        state.entry = px
        intents.append(Intent("open", side, qty, False, ratio))
        return Decision(intents=intents, lot=state, status="sent", detail="翻仓")

    return Decision(lot=state, detail="没有对应动作")
