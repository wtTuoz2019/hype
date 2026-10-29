"""按 Hyperliquid userFills 补漏库内缺失的开单信号。

用法:
  ./.venv/bin/python manage.py catchup_signals
  ./.venv/bin/python manage.py catchup_signals --address 0xf97a...
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "从 Hyperliquid 补漏监控钱包的开单信号（只补比库里更新的成交）"

    def add_arguments(self, parser):
        parser.add_argument("--address", default="", help="只补这一个地址；默认全部启用钱包")

    def handle(self, *args, **options):
        from signals import SignalHub

        address = str(options.get("address") or "").strip().lower()
        hub = SignalHub(debounce=0.05, as_json=False)
        # 只读名单，不拉 WebSocket，避免补漏命令误连分片
        hub.enabled = hub._wallet_map()
        if address:
            if address not in hub.enabled:
                self.stderr.write(f"地址不在启用名单: {address}")
                return
            targets = [address]
        else:
            targets = list(hub.enabled)
        total = 0
        for addr in targets:
            n = hub.catchup_address(addr)
            total += n
            self.stdout.write(f"{addr[:10]}… 新成交 {n}")
        # 立刻冲掉 debounce，写入 DB
        hub.flush_all()
        self.stdout.write(self.style.SUCCESS(f"补漏完成，涉及新成交 {total} 条（合并后写入信号表）"))
