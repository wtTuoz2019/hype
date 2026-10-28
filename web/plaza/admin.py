import csv

from django.contrib import admin
from django.http import HttpResponse

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
    list_display = (
        "telegram_name",
        "telegram_username",
        "telegram_id",
        "is_pro",
        "telegram_login_count",
        "telegram_last_login_at",
        "telegram_first_login_at",
        "tg_link",
    )
    list_filter = ("is_pro",)
    search_fields = (
        "telegram_username",
        "telegram_name",
        "telegram_first_name",
        "telegram_id",
        "user__username",
        "invite_code",
    )
    list_editable = ("is_pro",)
    readonly_fields = (
        "user",
        "telegram_id",
        "telegram_first_name",
        "telegram_last_name",
        "telegram_name",
        "telegram_username",
        "telegram_photo",
        "telegram_auth_date",
        "telegram_first_login_at",
        "telegram_last_login_at",
        "telegram_login_count",
        "invite_code",
        "tg_link",
    )
    actions = ("export_telegram_csv",)

    @admin.display(description="拉群链接")
    def tg_link(self, obj: Profile) -> str:
        return obj.telegram_link or "—"

    @admin.action(description="导出 Telegram 会员 CSV（方便拉群）")
    def export_telegram_csv(self, request, queryset):
        rows = queryset.filter(telegram_id__isnull=False).order_by("-telegram_last_login_at")
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = 'attachment; filename="telegram_members.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "telegram_id",
                "username",
                "display_name",
                "first_name",
                "last_name",
                "is_pro",
                "login_count",
                "first_login",
                "last_login",
                "tme_link",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.telegram_id or "",
                    row.telegram_username or "",
                    row.telegram_name or "",
                    row.telegram_first_name or "",
                    row.telegram_last_name or "",
                    "1" if row.is_pro else "0",
                    row.telegram_login_count,
                    row.telegram_first_login_at.isoformat(sep=" ", timespec="seconds")
                    if row.telegram_first_login_at
                    else "",
                    row.telegram_last_login_at.isoformat(sep=" ", timespec="seconds")
                    if row.telegram_last_login_at
                    else "",
                    row.telegram_link,
                ]
            )
        return response


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
