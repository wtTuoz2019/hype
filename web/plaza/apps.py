from django.apps import AppConfig


class PlazaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plaza"
    verbose_name = "钱包跟踪"

    def ready(self) -> None:
        import os
        import sys

        from django.db.backends.signals import connection_created

        def _sqlite_wal(sender, connection, **kwargs):
            if connection.vendor != "sqlite":
                return
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")

        connection_created.connect(_sqlite_wal)

        # 生产：监控用 manage.py run_monitor 单独起。
        # 开发：runserver 子进程仍可内嵌；gunicorn 即使带 HYPE_MONITOR=1 也不再内嵌，
        # 避免和页面抢同一个工人 / SQLite。
        if "run_monitor" in sys.argv:
            return
        if "runserver" not in sys.argv:
            return
        if os.environ.get("RUN_MAIN") != "true":
            return
        from plaza.monitor import start_monitor

        start_monitor()
