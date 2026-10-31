"""本仓库内嵌的币安 / Gate 合约客户端（自 wjfuture 拷贝，独立部署不依赖旁仓）。"""

from .binance_futures_client import BinanceFuturesClient


def __getattr__(name: str):
    if name in ("GateFuturesClient", "symbol_to_contract"):
        from .gate_futures_client import GateFuturesClient, symbol_to_contract

        return GateFuturesClient if name == "GateFuturesClient" else symbol_to_contract
    raise AttributeError(name)


__all__ = ["BinanceFuturesClient", "GateFuturesClient", "symbol_to_contract"]
