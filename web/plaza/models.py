import secrets

from django.conf import settings
from django.db import models


def new_invite_code() -> str:
    return secrets.token_hex(4)


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    invite_code = models.CharField(max_length=16, unique=True, default=new_invite_code)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitees",
    )
    is_pro = models.BooleanField(default=False)
    telegram_id = models.BigIntegerField(null=True, blank=True, unique=True)
    telegram_name = models.CharField(max_length=80, blank=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    telegram_photo = models.URLField(max_length=300, blank=True)

    def __str__(self) -> str:
        return self.user.get_username()


class Leader(models.Model):
    name = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=42, unique=True)
    enabled = models.BooleanField(default=True)
    source = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name or self.address


class TradeSignal(models.Model):
    leader = models.ForeignKey(Leader, on_delete=models.CASCADE, related_name="signals")
    sig_id = models.CharField(max_length=180, unique=True)
    time_ms = models.BigIntegerField(default=0)
    coin = models.CharField(max_length=40)
    action = models.CharField(max_length=16)
    side = models.CharField(max_length=8, blank=True)
    label = models.CharField(max_length=8)
    prev_szi = models.CharField(max_length=40, blank=True)
    szi = models.CharField(max_length=40, blank=True)
    delta_szi = models.CharField(max_length=40, blank=True)
    px = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def when(self) -> str:
        from datetime import datetime

        try:
            return datetime.fromtimestamp(self.time_ms / 1000).strftime("%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "-"


class LeaderSnapshot(models.Model):
    leader = models.OneToOneField(Leader, on_delete=models.CASCADE, related_name="snapshot")
    payload = models.TextField(default="{}")
    fetched_at = models.DateTimeField(auto_now=True)
    full = models.BooleanField(default=False)


class ExchangeAccount(models.Model):
    """用户自己的交易所密钥。库里只存密文，页面只显示密钥后四位。"""

    VENUE_BINANCE = "binance"
    VENUE_GATE = "gate"
    VENUES = ((VENUE_BINANCE, "币安"), (VENUE_GATE, "Gate"))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exchange_accounts")
    venue = models.CharField(max_length=16)
    name = models.CharField(max_length=40)
    key_last4 = models.CharField(max_length=4)
    key_fp = models.CharField(max_length=64)
    key_enc = models.TextField()
    secret_enc = models.TextField()
    enabled = models.BooleanField(default=True)
    halt_day = models.CharField(max_length=10, blank=True)
    day_equity = models.FloatField(default=0)
    halted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "key_fp")]

    def __str__(self) -> str:
        return f"{self.get_venue_display()} {self.name} ****{self.key_last4}"

    def get_venue_display(self) -> str:
        return dict(self.VENUES).get(self.venue, self.venue)


class Follow(models.Model):
    """用户用哪个交易所账户跟这个钱包。新信号在后台下单。"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="follows")
    leader = models.ForeignKey(Leader, on_delete=models.CASCADE, related_name="follows")
    account = models.ForeignKey(
        ExchangeAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="follows",
    )
    active = models.BooleanField(default=True)
    armed_ms = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "leader")]


class CopyLot(models.Model):
    """某个跟单在一个币上的归属仓位。比例在从空仓开出时冻结。"""

    follow = models.ForeignKey(Follow, on_delete=models.CASCADE, related_name="lots")
    coin = models.CharField(max_length=40)
    side = models.CharField(max_length=8, blank=True)
    qty = models.FloatField(default=0)
    ratio = models.FloatField(null=True, blank=True)
    entry = models.FloatField(default=0)
    pending = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("follow", "coin")]


class CopyLog(models.Model):
    follow = models.ForeignKey(Follow, on_delete=models.CASCADE, related_name="copy_logs")
    coin = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=16)
    qty = models.FloatField(default=0)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class TapeWallet(models.Model):
    """页面上的模拟聪明钱包。"""

    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=40)
    address = models.CharField(max_length=42)
    win = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    roi = models.FloatField(default=0)
    pnl = models.FloatField(default=0)
    dd = models.FloatField(default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.code,
            "name": self.name,
            "address": self.address,
            "win": self.win,
            "wins": self.wins,
            "total": self.total,
            "roi": self.roi,
            "pnl": self.pnl,
            "dd": self.dd,
        }


class TapeEvent(models.Model):
    """模拟开单。免费用户晚 30 分钟看到，Pro 晚约 1 分钟。"""

    wallet = models.ForeignKey(TapeWallet, on_delete=models.CASCADE, related_name="events")
    happened_at = models.DateTimeField(db_index=True)
    symbol = models.CharField(max_length=16)
    act = models.CharField(max_length=8)
    amount = models.FloatField(default=0)

    class Meta:
        ordering = ["-happened_at", "-id"]


class AlertSub(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alert_subs")
    leader = models.ForeignKey(Leader, on_delete=models.CASCADE, related_name="alert_subs")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "leader"], name="plaza_alertsub_user_leader"),
        ]


class TapeSub(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tape_subs")
    wallet = models.ForeignKey(TapeWallet, on_delete=models.CASCADE, related_name="subs")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "wallet"], name="plaza_tapesub_user_wallet"),
        ]


class TelegramAuth(models.Model):
    """用过的 Telegram 登录签名，防止同一条回调被重复使用。"""

    digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Commission(models.Model):
    """分佣流水。结算任务还没接，表先留着。"""

    STATUS_PENDING = "pending"
    STATUS_SETTLED = "settled"
    beneficiary = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="commissions"
    )
    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commission_sources",
    )
    leader = models.ForeignKey(Leader, null=True, blank=True, on_delete=models.SET_NULL)
    amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    rate = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    status = models.CharField(max_length=16, default=STATUS_PENDING)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
