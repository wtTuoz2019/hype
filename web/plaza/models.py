import secrets

from django.conf import settings
from django.db import models


def new_invite_code() -> str:
    return secrets.token_hex(4)


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="用户",
    )
    invite_code = models.CharField("邀请码", max_length=16, unique=True, default=new_invite_code)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitees",
        verbose_name="邀请人",
    )
    is_pro = models.BooleanField("Pro 会员", default=False)
    telegram_id = models.BigIntegerField("Telegram ID", null=True, blank=True, unique=True)
    telegram_name = models.CharField("Telegram 昵称", max_length=80, blank=True)
    telegram_username = models.CharField("Telegram 用户名", max_length=64, blank=True)
    telegram_photo = models.URLField("头像", max_length=300, blank=True)

    class Meta:
        verbose_name = "用户资料"
        verbose_name_plural = "用户资料"

    def __str__(self) -> str:
        return self.user.get_username()


class Leader(models.Model):
    name = models.CharField("名称", max_length=40, blank=True)
    address = models.CharField("钱包地址", max_length=42, unique=True)
    enabled = models.BooleanField("启用监控", default=True)
    source = models.CharField("来源", max_length=16, blank=True, help_text="手工添加请留空；热门同步为 hyperbot")
    created_at = models.DateTimeField("添加时间", auto_now_add=True)

    class Meta:
        verbose_name = "跟踪钱包"
        verbose_name_plural = "跟踪钱包"

    def __str__(self) -> str:
        return self.name or self.address


class TradeSignal(models.Model):
    leader = models.ForeignKey(
        Leader, on_delete=models.CASCADE, related_name="signals", verbose_name="跟踪钱包"
    )
    sig_id = models.CharField("信号编号", max_length=180, unique=True)
    time_ms = models.BigIntegerField("成交时间戳", default=0)
    coin = models.CharField("币种", max_length=40)
    action = models.CharField("动作", max_length=16)
    side = models.CharField("方向", max_length=8, blank=True)
    label = models.CharField("标签", max_length=8)
    prev_szi = models.CharField("变动前仓位", max_length=40, blank=True)
    szi = models.CharField("变动后仓位", max_length=40, blank=True)
    delta_szi = models.CharField("仓位变化", max_length=40, blank=True)
    px = models.CharField("均价", max_length=40, blank=True)
    created_at = models.DateTimeField("写入时间", auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "开单信号"
        verbose_name_plural = "开单信号"
        indexes = [
            models.Index(fields=["-time_ms", "-id"], name="plaza_sig_time_id"),
            models.Index(fields=["leader", "-time_ms"], name="plaza_sig_leader_time"),
        ]

    def when(self) -> str:
        from datetime import datetime

        try:
            return datetime.fromtimestamp(self.time_ms / 1000).strftime("%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "-"

    def __str__(self) -> str:
        return f"{self.label} {self.coin}"


class LeaderSnapshot(models.Model):
    leader = models.OneToOneField(
        Leader, on_delete=models.CASCADE, related_name="snapshot", verbose_name="跟踪钱包"
    )
    payload = models.TextField("收益缓存", default="{}")
    fetched_at = models.DateTimeField("刷新时间", auto_now=True)
    full = models.BooleanField("完整刷新", default=False)

    class Meta:
        verbose_name = "收益快照"
        verbose_name_plural = "收益快照"

    def __str__(self) -> str:
        return f"{self.leader} 快照"


class ExchangeAccount(models.Model):
    """用户自己的交易所密钥。库里只存密文，页面只显示密钥后四位。"""

    VENUE_BINANCE = "binance"
    VENUE_GATE = "gate"
    VENUES = ((VENUE_BINANCE, "币安"), (VENUE_GATE, "Gate"))

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exchange_accounts",
        verbose_name="用户",
    )
    venue = models.CharField("交易所", max_length=16)
    name = models.CharField("账户备注", max_length=40)
    key_last4 = models.CharField("密钥后四位", max_length=4)
    key_fp = models.CharField("密钥指纹", max_length=64)
    key_enc = models.TextField("密钥密文")
    secret_enc = models.TextField("私钥密文")
    enabled = models.BooleanField("启用", default=True)
    halt_day = models.CharField("熔断日期", max_length=10, blank=True)
    day_equity = models.FloatField("日初权益", default=0)
    halted = models.BooleanField("已熔断", default=False)
    created_at = models.DateTimeField("添加时间", auto_now_add=True)

    class Meta:
        unique_together = [("user", "key_fp")]
        verbose_name = "交易所账户"
        verbose_name_plural = "交易所账户"

    def __str__(self) -> str:
        return f"{self.get_venue_display()} {self.name} ****{self.key_last4}"

    def get_venue_display(self) -> str:
        return dict(self.VENUES).get(self.venue, self.venue)


class Follow(models.Model):
    """用户用哪个交易所账户跟这个钱包。新信号在后台下单。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follows",
        verbose_name="用户",
    )
    leader = models.ForeignKey(
        Leader, on_delete=models.CASCADE, related_name="follows", verbose_name="跟踪钱包"
    )
    account = models.ForeignKey(
        ExchangeAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="follows",
        verbose_name="交易所账户",
    )
    active = models.BooleanField("跟单中", default=True)
    armed_ms = models.BigIntegerField("跟单起始时间", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        unique_together = [("user", "leader")]
        verbose_name = "跟单关系"
        verbose_name_plural = "跟单关系"

    def __str__(self) -> str:
        return f"{self.user} → {self.leader}"


class CopyLot(models.Model):
    """某个跟单在一个币上的归属仓位。比例在从空仓开出时冻结。"""

    follow = models.ForeignKey(
        Follow, on_delete=models.CASCADE, related_name="lots", verbose_name="跟单关系"
    )
    coin = models.CharField("币种", max_length=40)
    side = models.CharField("方向", max_length=8, blank=True)
    qty = models.FloatField("数量", default=0)
    ratio = models.FloatField("跟单比例", null=True, blank=True)
    entry = models.FloatField("均价", default=0)
    pending = models.FloatField("待成交", default=0)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        unique_together = [("follow", "coin")]
        verbose_name = "跟单仓位"
        verbose_name_plural = "跟单仓位"


class CopyLog(models.Model):
    follow = models.ForeignKey(
        Follow, on_delete=models.CASCADE, related_name="copy_logs", verbose_name="跟单关系"
    )
    coin = models.CharField("币种", max_length=40, blank=True)
    status = models.CharField("状态", max_length=16)
    qty = models.FloatField("数量", default=0)
    detail = models.CharField("说明", max_length=200, blank=True)
    created_at = models.DateTimeField("时间", auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "跟单日志"
        verbose_name_plural = "跟单日志"


class TapeWallet(models.Model):
    """页面上的模拟聪明钱包。"""

    code = models.CharField("编码", max_length=8, unique=True)
    name = models.CharField("名称", max_length=40)
    address = models.CharField("地址", max_length=42)
    win = models.PositiveIntegerField("胜率", default=0)
    wins = models.PositiveIntegerField("盈利笔数", default=0)
    total = models.PositiveIntegerField("总笔数", default=0)
    roi = models.FloatField("收益率", default=0)
    pnl = models.FloatField("盈亏", default=0)
    dd = models.FloatField("回撤", default=0)

    class Meta:
        verbose_name = "模拟钱包"
        verbose_name_plural = "模拟钱包"

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

    wallet = models.ForeignKey(
        TapeWallet, on_delete=models.CASCADE, related_name="events", verbose_name="模拟钱包"
    )
    happened_at = models.DateTimeField("发生时间", db_index=True)
    symbol = models.CharField("币种", max_length=16)
    act = models.CharField("动作", max_length=8)
    amount = models.FloatField("名义金额", default=0)

    class Meta:
        ordering = ["-happened_at", "-id"]
        verbose_name = "模拟开单"
        verbose_name_plural = "模拟开单"


class AlertSub(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="alert_subs",
        verbose_name="用户",
    )
    leader = models.ForeignKey(
        Leader, on_delete=models.CASCADE, related_name="alert_subs", verbose_name="跟踪钱包"
    )

    class Meta:
        verbose_name = "开单订阅"
        verbose_name_plural = "开单订阅"
        constraints = [
            models.UniqueConstraint(fields=["user", "leader"], name="plaza_alertsub_user_leader"),
        ]

    def __str__(self) -> str:
        return f"{self.user} 订阅 {self.leader}"


class TapeSub(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tape_subs",
        verbose_name="用户",
    )
    wallet = models.ForeignKey(
        TapeWallet, on_delete=models.CASCADE, related_name="subs", verbose_name="模拟钱包"
    )

    class Meta:
        verbose_name = "模拟订阅"
        verbose_name_plural = "模拟订阅"
        constraints = [
            models.UniqueConstraint(fields=["user", "wallet"], name="plaza_tapesub_user_wallet"),
        ]


class TelegramAuth(models.Model):
    """用过的 Telegram 登录签名，防止同一条回调被重复使用。"""

    digest = models.CharField("签名", max_length=64, unique=True)
    created_at = models.DateTimeField("使用时间", auto_now_add=True)

    class Meta:
        verbose_name = "Telegram 登录记录"
        verbose_name_plural = "Telegram 登录记录"


class Commission(models.Model):
    """分佣流水。结算任务还没接，表先留着。"""

    STATUS_PENDING = "pending"
    STATUS_SETTLED = "settled"
    STATUS_CHOICES = (
        (STATUS_PENDING, "待结算"),
        (STATUS_SETTLED, "已结算"),
    )
    beneficiary = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="commissions",
        verbose_name="受益人",
    )
    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="commission_sources",
        verbose_name="来源用户",
    )
    leader = models.ForeignKey(
        Leader, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="跟踪钱包"
    )
    amount = models.DecimalField("金额", max_digits=20, decimal_places=8, default=0)
    rate = models.DecimalField("比例", max_digits=8, decimal_places=4, default=0)
    status = models.CharField("状态", max_length=16, default=STATUS_PENDING, choices=STATUS_CHOICES)
    note = models.CharField("备注", max_length=200, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "分佣流水"
        verbose_name_plural = "分佣流水"
