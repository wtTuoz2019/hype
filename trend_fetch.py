#!/usr/bin/env python3
"""拉取聪明钱趋势参考，供本地行情分析脚本使用。

示例:
  python3 trend_fetch.py
  python3 trend_fetch.py --top 10 --order roi
  python3 trend_fetch.py --url https://p.c7.pro --json out.json
"""

from __future__ import annotations

import argparse
import json
import ssl
import urllib.parse
import urllib.request


def fetch(base: str, top: int, order: str, timeout: float = 20) -> dict:
    qs = urllib.parse.urlencode({"top": top, "order": order})
    url = f"{base.rstrip('/')}/api/trend/?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "hype-trend-fetch"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser(description="拉取 /api/trend/ 聪明钱趋势参考")
    parser.add_argument("--url", default="https://p.c7.pro", help="站点根，默认 https://p.c7.pro")
    parser.add_argument("--top", type=int, default=5, help="前 N 钱包，1..20")
    parser.add_argument("--order", choices=("roi", "win"), default="roi")
    parser.add_argument("--json", dest="out", default="", help="把完整 JSON 写到文件")
    args = parser.parse_args()

    data = fetch(args.url, args.top, args.order)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.out}")

    summary = data.get("summary") or {}
    today = data.get("today") or {}
    print(f"as_of={data.get('as_of')} top={data.get('params')}")
    print(
        f"仓位偏={summary.get('position_bias')} "
        f"score={summary.get('position_score')} | "
        f"当日开仓流偏={summary.get('flow_bias')} "
        f"score={summary.get('flow_score')} | "
        f"一致={summary.get('agree')}"
    )
    print(f"hint: {summary.get('hint')}")
    print(f"今日开仓 {today.get('open_count')} / 平仓 {today.get('close_count')}")
    print("--- 币种净偏向 ---")
    for row in (data.get("coins") or [])[:10]:
        print(
            f"  {row['coin']}: {row['bias']} "
            f"score={row['score']} "
            f"L={row['long_notional']}({row['long_wallets']}) "
            f"S={row['short_notional']}({row['short_wallets']})"
        )
    print("--- 钱包 ---")
    for w in data.get("wallets") or []:
        coins = ",".join(f"{p['coin']}:{p['side']}" for p in (w.get("positions") or [])[:6])
        print(f"  #{w['id']} {w['name']} roi={w['roi_pct']}% bias={w['bias']} [{coins}]")


if __name__ == "__main__":
    main()
