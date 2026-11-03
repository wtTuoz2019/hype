# AlphaScope 聪明钱趋势参考 API

给外部「分析趋势」脚本用的只读接口：取排行前 N 钱包的**当前持仓** + **当日开/平仓**，并给出多空汇总。

## 地址

```
GET https://p.c7.pro/api/trend/?top=5&order=roi
GET https://p.c7.pro/api/trend/?top=10&order=roi
GET https://p.c7.pro/api/trend/?top=10&order=win
```

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `top` | `5` | 前 N 个钱包，范围 `1`–`20` |
| `order` | `roi` | `roi`=30 日收益率；`win`=胜率 |

## 响应字段

```text
as_of / day                     快照时间 / 日期
params.top / params.order       实际参数
wallets[]                       前 N 钱包
  id, name, address
  roi_30d, roi_pct, win_rate
  bias                          long | short | neutral
  long_pct                      多头名义价值占比
  position_value
  positions[]                   coin, side, szi, entry, mark, value, upnl, roe, lev
coins[]                         按币种汇总（已按影响力排序）
  coin
  long_notional / short_notional
  long_wallets / short_wallets
  score                         -1~1，正=偏多
  bias                          long | short | neutral
today
  opens[] / closes[]            这批钱包当天开仓 / 平仓明细
  open_count / close_count
  long_notional / short_notional
  score / bias                  当日开仓流偏向
summary                         策略直接用的结论
  position_bias / position_score
  flow_bias / flow_score
  agree                         仓位与开仓流是否同向
  hint                          一句话结论
```

## 解读

- `summary.agree == true` 且 `position_bias` 为 `long`/`short`：前 N 仓位与当日开仓同向，参考权重可加大
- `coins[].score` 接近 ±1 且名义价值大：该币方向更清晰
- 数据来自站内缓存快照 + 当日信号，不是逐笔实时链上

## Python 调用

```python
import json
import urllib.request

BASE = "https://p.c7.pro"


def load_trend(top: int = 10, order: str = "roi", timeout: float = 20) -> dict:
    url = f"{BASE}/api/trend/?top={top}&order={order}"
    req = urllib.request.Request(url, headers={"User-Agent": "trend-analysis"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


if __name__ == "__main__":
    data = load_trend(top=10, order="roi")
    s = data["summary"]
    print(s["hint"])
    print("仓位", s["position_bias"], s["position_score"],
          "| 开仓流", s["flow_bias"], s["flow_score"],
          "| 同向", s["agree"])
    for c in data["coins"][:10]:
        print(f"{c['coin']:8} {c['bias']:7} score={c['score']:+.3f} "
              f"L={c['long_notional']:.0f}({c['long_wallets']}) "
              f"S={c['short_notional']:.0f}({c['short_wallets']})")
    for w in data["wallets"]:
        pos = ", ".join(f"{p['coin']}:{p['side']}" for p in w["positions"][:6])
        print(f"#{w['id']} {w['name']} roi={w['roi_pct']}% bias={w['bias']} [{pos}]")
```

## curl

```bash
curl -sS "https://p.c7.pro/api/trend/?top=10&order=roi" | python3 -m json.tool
```

## 本地命令行（仓库内）

```bash
python3 trend_fetch.py --url https://p.c7.pro --top 10 --json out.json
```
