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

    def __str__(self) -> str:
        return self.user.get_username()


class Leader(models.Model):
    name = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=42, unique=True)
    enabled = models.BooleanField(default=True)
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


class Follow(models.Model):
    """用户决定跟这个钱包。只记录选择，不下单。"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="follows")
    leader = models.ForeignKey(Leader, on_delete=models.CASCADE, related_name="follows")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "leader")]


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
