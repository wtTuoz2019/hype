from django.apps import AppConfig


class PlazaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plaza"
    verbose_name = "跟单广场"

    def ready(self) -> None:
        import os
        import sys

        runserver = "runserver" in sys.argv
        if runserver and os.environ.get("RUN_MAIN") != "true":
            return
        if not runserver and os.environ.get("HYPE_MONITOR") != "1":
            return
        from plaza.monitor import start_monitor

        start_monitor()
