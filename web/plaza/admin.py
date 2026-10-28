from django.contrib import admin

from .models import (
    AlertSub,
    Commission,
    ExchangeAccount,
    Follow,
    Leader,
    LeaderSnapshot,
    Profile,
    TradeSignal,
)

admin.site.site_header = "AlphaScope 管理后台"
admin.site.site_title = "AlphaScope"
admin.site.index_title = "站点管理"


@admin.register(Leader)
class LeaderAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "source", "enabled", "created_at")
    list_filter = ("enabled", "source")
    search_fields = ("name", "address")
    list_editable = ("enabled",)
    ordering = ("-id",)


@admin.register(TradeSignal)
class TradeSignalAdmin(admin.ModelAdmin):
    list_display = ("when", "leader", "coin", "label", "px", "delta_szi", "created_at")
    list_filter = ("label", "coin")
    search_fields = ("leader__name", "leader__address", "coin", "sig_id")
    readonly_fields = (
        "leader",
        "sig_id",
        "time_ms",
        "coin",
        "action",
        "side",
        "label",
        "prev_szi",
        "szi",
        "delta_szi",
        "px",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(LeaderSnapshot)
class LeaderSnapshotAdmin(admin.ModelAdmin):
    list_display = ("leader", "full", "fetched_at")
    search_fields = ("leader__name", "leader__address")
    readonly_fields = ("leader", "payload", "fetched_at", "full")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_pro", "telegram_username", "invite_code", "invited_by")
    list_filter = ("is_pro",)
    search_fields = ("user__username", "telegram_username", "invite_code")
    list_editable = ("is_pro",)


@admin.register(ExchangeAccount)
class ExchangeAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "venue", "key_last4", "enabled", "halted")
    list_filter = ("venue", "enabled", "halted")
    search_fields = ("user__username", "name", "key_last4")
    exclude = ("key_enc", "secret_enc", "key_fp")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("user", "leader", "account", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("user__username", "leader__name", "leader__address")


@admin.register(AlertSub)
class AlertSubAdmin(admin.ModelAdmin):
    list_display = ("user", "leader")
    search_fields = ("user__username", "leader__name", "leader__address")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = ("beneficiary", "source_user", "amount", "rate", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("beneficiary__username", "source_user__username")
