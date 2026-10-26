#!/usr/bin/env python3
"""添加、停用、删除要监控的 Hyperliquid 钱包。

名单写在 hype/data/wallets.json。以后网页也改这个文件，信号进程会自己载入。

用法:
  python3 hype/wallets.py add 0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1 --name 主号
  python3 hype/wallets.py list
  python3 hype/wallets.py disable 主号的id或地址
  python3 hype/wallets.py enable hl-f97ad670
  python3 hype/wallets.py remove 0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1
"""

from __future__ import annotations

import argparse
import sys

from wallet_store import add_wallet, load_wallets, remove_wallet, set_enabled


def _print_row(row: dict) -> None:
    flag = "开" if row["enabled"] else "关"
    name = row["name"] or "-"
    print(f"{flag}  {row['id']}  {name}  {row['address']}")


def cmd_list(_args: argparse.Namespace) -> None:
    rows = load_wallets()
    if not rows:
        print("还没有钱包。例: python3 hype/wallets.py add 0x地址 --name 主号")
        return
    for row in rows:
        _print_row(row)


def cmd_add(args: argparse.Namespace) -> None:
    try:
        row, created = add_wallet(args.address, args.name or "")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    if created:
        print("已添加")
    else:
        print("地址已在名单里")
    _print_row(row)


def cmd_enable(args: argparse.Namespace, enabled: bool) -> None:
    try:
        row = set_enabled(args.key, enabled)
    except KeyError:
        print(f"找不到钱包: {args.key}", file=sys.stderr)
        sys.exit(2)
    print("已启用" if enabled else "已停用")
    _print_row(row)


def cmd_remove(args: argparse.Namespace) -> None:
    try:
        row = remove_wallet(args.key)
    except KeyError:
        print(f"找不到钱包: {args.key}", file=sys.stderr)
        sys.exit(2)
    print("已删除")
    _print_row(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="管理要监控的 Hyperliquid 钱包")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="添加钱包")
    p_add.add_argument("address")
    p_add.add_argument("--name", default="", help="显示名，网页上以后也可以改")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="列出钱包")
    p_list.set_defaults(func=cmd_list)

    p_on = sub.add_parser("enable", help="恢复监控")
    p_on.add_argument("key", help="id 或地址")
    p_on.set_defaults(func=lambda a: cmd_enable(a, True))

    p_off = sub.add_parser("disable", help="暂停监控，不删记录")
    p_off.add_argument("key", help="id 或地址")
    p_off.set_defaults(func=lambda a: cmd_enable(a, False))

    p_rm = sub.add_parser("remove", help="从名单删除")
    p_rm.add_argument("key", help="id 或地址")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
