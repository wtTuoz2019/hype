"""独立进程：盯 Hyperliquid 开单、刷新收益、同步热门地址。"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "后台监控钱包（与 gunicorn 分开跑）"

    def handle(self, *args, **options):
        from plaza.monitor import start_monitor

        self.stdout.write("启动钱包监控进程")
        start_monitor()
        # start_monitor 里信号线程是 daemon；这里挡住主进程。
        import time

        while True:
            time.sleep(3600)
