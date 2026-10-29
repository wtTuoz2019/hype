from django.conf import settings
from django.core.management.base import BaseCommand

from plaza.telegram_notify import send_text


class Command(BaseCommand):
    help = "往 TELEGRAM_NOTIFY_CHAT_ID 发一条测试消息，确认机器人能发到群"

    def handle(self, *args, **options):
        chat = (settings.TELEGRAM_NOTIFY_CHAT_ID or "").strip()
        token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
        if not token:
            self.stderr.write("缺少 TELEGRAM_BOT_TOKEN")
            return
        if not chat:
            self.stderr.write(
                "缺少 TELEGRAM_NOTIFY_CHAT_ID。把机器人拉进群后，打开 "
                f"https://api.telegram.org/bot{token[:10]}…/getUpdates 查看 chat.id"
            )
            return
        send_text("AlphaScope 测试：群通知已接通。之后新开单会自动推到这里。")
        self.stdout.write(self.style.SUCCESS(f"已发送到 {chat}"))
