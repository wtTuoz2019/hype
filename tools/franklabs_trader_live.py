#!/usr/bin/env python3
"""自动拉取 franklabs.cc「K线实盘」全部交易员开/加/减/平仓。

数据源：站点解析 Discord 实盘通知后的统一 JSON（非交易所官方 API）。
登录态 Cookie；请求必须带本站 Origin/Referer。

环境变量（勿写进 git）:
  FRANKLABS_EMAIL / FRANKLABS_PASSWORD
  也可放同目录 franklabs.env（KEY=VALUE），或 ../web/.env

示例:
  python3 tools/franklabs_trader_live.py --sync
  python3 tools/franklabs_trader_live.py --traders aoying --symbol BTC
  python3 tools/franklabs_trader_live.py --directory
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None  # type: ignore

BASE = "https://franklabs.cc"
API = f"{BASE}/api"
UA = "Mozilla/5.0 (franklabs-trader-live-fetch)"
ROOT = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = ROOT / "data" / "franklabs_trader_live"


def _ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Client:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()),
            urllib.request.HTTPCookieProcessor(self.jar),
        )

    def _req(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        referer: str = f"{BASE}/trader-live",
        retries: int = 3,
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        headers = {
            "User-Agent": UA,
            "Accept": "application/json",
            "Origin": BASE,
            "Referer": referer,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        last_err: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=45) as resp:
                    raw = resp.read().decode()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                if exc.code in (502, 503, 504) and attempt + 1 < retries:
                    time.sleep(1.5 * (attempt + 1))
                    last_err = RuntimeError(f"HTTP {exc.code} {url}: {detail[:300]}")
                    continue
                raise RuntimeError(f"HTTP {exc.code} {url}: {detail[:300]}") from exc
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 < retries:
                    time.sleep(1.5 * (attempt + 1))
                    last_err = exc
                    continue
                raise
        raise RuntimeError(str(last_err or "request failed"))

    def login(self, email: str, password: str) -> dict:
        return self._req(
            "POST",
            f"{API}/auth/login",
            body={"email": email, "password": password},
            referer=f"{BASE}/login",
        )

    def me(self) -> dict:
        return self._req("GET", f"{API}/auth/me")

    def trader_live(self, **params: Any) -> dict:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        return self._req("GET", f"{API}/trader-live?{qs}")

    def directory(self) -> dict:
        return self.trader_live(action="directory")

    def events(
        self,
        *,
        traders: str,
        symbol: str = "",
        limit: int = 200,
        cursor: str = "",
        include_snapshots: bool = True,
    ) -> dict:
        return self.trader_live(
            action="events",
            traders=traders,
            symbol=symbol or None,
            limit=str(limit),
            cursor=cursor or None,
            include_snapshots="true" if include_snapshots else "false",
        )

    def pull_trader_events(
        self,
        *,
        trader_id: str,
        symbol: str = "",
        page_size: int = 200,
        max_pages: int = 80,
        include_snapshots: bool = True,
    ) -> list[dict]:
        rows: list[dict] = []
        cursor = ""
        for _ in range(max_pages):
            page = self.events(
                traders=trader_id,
                symbol=symbol,
                limit=page_size,
                cursor=cursor,
                include_snapshots=include_snapshots,
            )
            items = page.get("items") or []
            rows.extend(items)
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor") or page.get("cursor") or ""
            if not cursor:
                break
        return rows

    def pull_all_events(
        self,
        *,
        traders: str,
        symbol: str = "",
        page_size: int = 200,
        max_pages: int = 80,
        include_snapshots: bool = True,
    ) -> list[dict]:
        """默认按交易员逐个拉取后合并，避免多人全标的 502。"""
        ids = [x.strip() for x in traders.split(",") if x.strip()]
        if not ids:
            return []
        rows: list[dict] = []
        seen: set[str] = set()
        for tid in ids:
            for item in self.pull_trader_events(
                trader_id=tid,
                symbol=symbol,
                page_size=page_size,
                max_pages=max_pages,
                include_snapshots=include_snapshots,
            ):
                eid = str(item.get("event_id") or "")
                if eid and eid in seen:
                    continue
                if eid:
                    seen.add(eid)
                rows.append(item)
        rows.sort(key=lambda r: str(r.get("message_ts") or ""), reverse=True)
        return rows


def resolve_trader_ids(client: Client, traders_arg: str) -> tuple[str, list[dict]]:
    """traders_arg 空 / all / * → directory 全部；否则用指定列表。"""
    directory = client.directory()
    all_traders = [t for t in (directory.get("traders") or []) if isinstance(t, dict) and t.get("id")]
    raw = (traders_arg or "").strip().lower()
    if raw in ("", "all", "*", "全部"):
        ids = ",".join(str(t["id"]) for t in all_traders)
        return ids, all_traders
    want = {x.strip() for x in traders_arg.split(",") if x.strip()}
    picked = [t for t in all_traders if str(t.get("id")) in want]
    missing = want - {str(t.get("id")) for t in picked}
    if missing:
        ids = ",".join(sorted(want))
    else:
        ids = ",".join(str(t["id"]) for t in picked)
    return ids, all_traders


def write_sync(out_dir: Path, *, traders: str, symbol: str, directory: dict, items: list[dict]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "fetched_at": _utc_now(),
        "traders": traders,
        "symbol": symbol,
        "count": len(items),
        "directory": {
            "traders": directory.get("traders") or [],
            "hot_symbols": directory.get("hot_symbols") or [],
            "symbols": directory.get("symbols") or [],
            "coverage": directory.get("coverage"),
        },
        "items": items,
    }
    latest = out_dir / "latest.json"
    snap = out_dir / f"events_{stamp}.json"
    text = json.dumps(payload, ensure_ascii=False)
    latest.write_text(text, encoding="utf-8")
    snap.write_text(text, encoding="utf-8")
    snaps = sorted(out_dir.glob("events_*.json"), reverse=True)
    for old in snaps[48:]:
        try:
            old.unlink()
        except OSError:
            pass
    meta = {
        "fetched_at": payload["fetched_at"],
        "count": len(items),
        "traders": traders,
        "symbol": symbol or "*",
        "latest": str(latest),
        "snapshot": str(snap),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return latest


def main() -> int:
    _load_dotenv(ROOT / "franklabs.env")
    _load_dotenv(ROOT.parent / "web" / ".env")  # /www/wwwroot/hype/tools → ../web/.env
    _load_dotenv(ROOT.parent / "hype" / "web" / ".env")  # 本机 future_test/tools 布局

    parser = argparse.ArgumentParser(description="拉取 franklabs K线实盘开单信息（默认全部交易员）")
    parser.add_argument("--email", default=os.environ.get("FRANKLABS_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("FRANKLABS_PASSWORD", ""))
    parser.add_argument("--traders", default="all", help="交易员 id 逗号分隔；默认 all=目录全部")
    parser.add_argument("--symbol", default="", help="如 BTC；空=全部标的")
    parser.add_argument("--directory", action="store_true", help="只拉交易员目录/热门标的")
    parser.add_argument("--sync", action="store_true", help="写入 data/franklabs_trader_live/latest.json（适合 cron）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="--sync 输出目录")
    parser.add_argument("--json", dest="out", default="", help="额外写出一份完整 JSON")
    parser.add_argument("--limit-print", type=int, default=12)
    parser.add_argument("--no-snapshots", action="store_true", help="不拉持仓 snapshot")
    args = parser.parse_args()

    if not args.email or not args.password:
        print("请设置 FRANKLABS_EMAIL / FRANKLABS_PASSWORD，或写 tools/franklabs.env", file=sys.stderr)
        return 2

    client = Client()
    login = client.login(args.email, args.password)
    user = login.get("user") or {}
    print(f"login ok user_id={user.get('id')} email={user.get('email')}")

    if args.directory:
        data = client.directory()
        traders = data.get("traders") or []
        print(
            f"traders={len(traders)} hot={len(data.get('hot_symbols') or [])} "
            f"symbols={len(data.get('symbols') or [])}"
        )
        for t in traders:
            print(f"  {t.get('id'):20} {t.get('name')} events={t.get('events_count')}")
        if args.out:
            Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"wrote {args.out}")
        return 0

    trader_ids, directory_traders = resolve_trader_ids(client, args.traders)
    directory = {
        "traders": directory_traders,
        "hot_symbols": [],
        "symbols": [],
        "coverage": None,
    }
    if args.sync or args.traders.strip().lower() in ("", "all", "*", "全部"):
        directory = client.directory()
        if args.traders.strip().lower() in ("", "all", "*", "全部"):
            trader_ids = ",".join(str(t["id"]) for t in (directory.get("traders") or []) if t.get("id"))

    n_traders = len([x for x in trader_ids.split(",") if x])
    print(f"pulling traders={n_traders} symbol={args.symbol or '*'} …")
    items = client.pull_all_events(
        traders=trader_ids,
        symbol=args.symbol,
        include_snapshots=not args.no_snapshots,
    )
    print(f"events={len(items)}")
    for row in items[: args.limit_print]:
        print(
            f"{row.get('message_ts')} {str(row.get('trader_id') or ''):16} "
            f"{str(row.get('action') or ''):8} {str(row.get('side') or '-'):5} "
            f"{str(row.get('symbol') or '-'):8} "
            f"entry={row.get('position_entry_price') or row.get('execution_price') or '-'}"
        )

    if args.sync:
        path = write_sync(
            Path(args.out_dir),
            traders=trader_ids,
            symbol=args.symbol,
            directory=directory,
            items=items,
        )
        print(f"synced {path}")

    if args.out:
        payload = {
            "fetched_at": _utc_now(),
            "traders": trader_ids,
            "symbol": args.symbol,
            "count": len(items),
            "items": items,
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
