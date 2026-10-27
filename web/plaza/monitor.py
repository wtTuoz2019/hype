"""站点启动后在后台盯钱包，新信号直接进数据库。收益也在这里刷新。"""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta

_STARTED = False
_REFRESH_SEC = 20
_HOT_SEC = 6 * 60 * 60


def refresh_due() -> None:
    from django.db import close_old_connections
    from django.utils import timezone

    from plaza.models import Leader, LeaderSnapshot
    from plaza.services import refresh_leader

    close_old_connections()
    now = timezone.now()
    for leader in Leader.objects.filter(enabled=True).select_related("snapshot"):
        snap = None
        try:
            snap = leader.snapshot
        except LeaderSnapshot.DoesNotExist:
            snap = None
        age = None if snap is None else now - snap.fetched_at
        if snap is None or age >= timedelta(minutes=5):
            full = True
        elif age >= timedelta(seconds=110):
            full = False
        else:
            _paint(snap)
            continue
        try:
            refresh_leader(leader, full)
        except Exception as exc:
            print(f"刷新 {leader.address[:10]} 失败: {exc}", flush=True)


def _paint(snap) -> None:
    """旧缓存还没画好曲线时，在后台补上，不改刷新时间，也不访问链上。"""
    from plaza.models import LeaderSnapshot
    from plaza.services import present

    try:
        data = json.loads(snap.payload)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict) or data.get("shown"):
        return
    LeaderSnapshot.objects.filter(pk=snap.pk).update(
        payload=json.dumps(present(data), ensure_ascii=False)
    )


def _refresh_loop() -> None:
    while True:
        try:
            refresh_due()
        except Exception as exc:
            print(f"收益刷新失败: {exc}", flush=True)
        time.sleep(_REFRESH_SEC)


def _hot_loop() -> None:
    from django.db import close_old_connections

    from plaza.hyperbot import fetch_hot, sync_hot

    while True:
        close_old_connections()
        try:
            result = sync_hot(fetch_hot())
            print(
                "热门地址同步 "
                f"保留 {result['kept']} 新增 {result['created']} "
                f"停用 {result['disabled']}",
                flush=True,
            )
        except Exception as exc:
            print(f"热门地址同步失败: {exc}", flush=True)
        time.sleep(_HOT_SEC)


def start_monitor() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def run() -> None:
        from django.db import close_old_connections

        close_old_connections()
        from plaza.ingest import backfill_signals
        from plaza.services import ensure_leaders
        from signals import SignalHub

        ensure_leaders()
        try:
            count = backfill_signals()
            print(f"历史信号写入 {count} 条", flush=True)
        except Exception as exc:
            print(f"历史信号补写失败: {exc}", flush=True)
        from plaza.copy_run import start_copy_worker

        start_copy_worker()
        threading.Thread(target=_refresh_loop, name="hype-stats", daemon=True).start()
        threading.Thread(target=_hot_loop, name="hype-hot", daemon=True).start()
        hub = SignalHub(debounce=3.0, as_json=False)
        hub.run()

    threading.Thread(target=run, name="hype-signals", daemon=True).start()
