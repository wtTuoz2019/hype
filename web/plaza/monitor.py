"""站点启动后在后台盯钱包，新信号直接进数据库。收益也在这里刷新。"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

_STARTED = False
# 收益/持仓不要求秒级：拉长间隔，少占 Hyperliquid 与本机线程。
_REFRESH_SEC = 60
_HOT_SEC = 6 * 60 * 60
_REFRESH_WORKERS = 2
_FULL_PER_CYCLE = 4
_LIGHT_PER_CYCLE = 12
_FULL_AGE = timedelta(minutes=12)
_LIGHT_AGE = timedelta(minutes=4)


def refresh_due() -> None:
    from django.db import close_old_connections
    from django.utils import timezone

    from plaza.models import Leader, LeaderSnapshot
    from plaza.services import refresh_leader

    close_old_connections()
    now = timezone.now()
    due_full: list = []
    due_light: list = []
    for leader in Leader.objects.filter(enabled=True).select_related("snapshot"):
        snap = None
        try:
            snap = leader.snapshot
        except LeaderSnapshot.DoesNotExist:
            snap = None
        age = None if snap is None else now - snap.fetched_at
        if snap is None or not snap.full or age >= _FULL_AGE:
            due_full.append((leader, True, age or timedelta(days=999)))
        elif age >= _LIGHT_AGE:
            due_light.append((leader, False, age))
        else:
            _paint(snap)

    def oldest_first(item):
        return item[2]

    due_full.sort(key=oldest_first, reverse=True)
    due_light.sort(key=oldest_first, reverse=True)
    jobs = [(row[0], row[1]) for row in due_full[:_FULL_PER_CYCLE]]
    jobs += [(row[0], row[1]) for row in due_light[:_LIGHT_PER_CYCLE]]
    if not jobs:
        return

    def work(pair) -> None:
        leader, full = pair
        close_old_connections()
        try:
            refresh_leader(leader, full)
        except Exception as exc:
            print(f"刷新 {leader.address[:10]} 失败: {exc}", flush=True)
        finally:
            close_old_connections()

    workers = min(_REFRESH_WORKERS, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, job) for job in jobs]
        for future in as_completed(futures):
            future.result()


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
        started = time.time()
        try:
            refresh_due()
        except Exception as exc:
            print(f"收益刷新失败: {exc}", flush=True)
        # 钱包多时拉长间隔，避免一轮还没完又开下一轮
        from plaza.models import Leader

        try:
            count = Leader.objects.filter(enabled=True).count()
        except Exception:
            count = 0
        wait = _REFRESH_SEC if count <= 40 else min(180, _REFRESH_SEC + count // 3)
        elapsed = time.time() - started
        time.sleep(max(15.0, wait - elapsed))


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
