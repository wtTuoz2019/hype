import csv
import json

from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html

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


def _leader_roi_30d(leader: Leader) -> float | None:
    try:
        snap = leader.snapshot
    except LeaderSnapshot.DoesNotExist:
        return None
    try:
        payload = json.loads(snap.payload or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    period = (payload.get("periods") or {}).get("30d") or {}
    try:
        return float(period.get("roi") or 0) * 100
    except (TypeError, ValueError):
        return None


@admin.register(Leader)
class LeaderAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "source", "enabled", "roi_30d", "snap_at", "created_at")
    list_filter = ("enabled", "source")
    search_fields = ("name", "address")
    list_editable = ("enabled",)
    ordering = ("-id",)
    fields = ("name", "address", "enabled", "source", "roi_30d", "snap_at", "created_at")
    readonly_fields = ("roi_30d", "snap_at", "created_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("snapshot")

    @admin.display(description="30日ROI")
    def roi_30d(self, obj: Leader):
        from plaza.hyperbot import min_roi_30d

        pct = _leader_roi_30d(obj)
        if pct is None:
            return format_html('<span style="color:#999">刷新中</span>')
        text = f"{pct:+.1f}%"
        # 热门源门槛见 HYPE_MIN_ROI_30D；手工源不受限
        thr_pct = min_roi_30d() * 100
        manual = not (obj.source or "").strip()
        ok = manual or pct > thr_pct
        color = "#0a7" if ok else "#c40"
        tip = "上榜" if ok else f"未上榜(热门需ROI>{thr_pct:g}%)"
        return format_html('<span style="color:{}" title="{}">{}</span>', color, tip, text)

    @admin.display(description="收益刷新")
    def snap_at(self, obj: Leader):
        try:
            return obj.snapshot.fetched_at
        except LeaderSnapshot.DoesNotExist:
            return "—"

    def save_model(self, request, obj, form, change):
        # 后台手工添加：来源务必留空，否则按热门规则过 HYPE_MIN_ROI_30D 才上榜
        if obj.source and str(obj.source).strip().lower() in ("manual", "手工", "admin"):
            obj.source = ""
        super().save_model(request, obj, form, change)
        # 后台新增/启用后后台预热收益，否则前端要等监控轮询才有快照
        if obj.enabled:
            import threading

            from plaza.views import _warm_leader

            threading.Thread(
                target=_warm_leader, args=(obj.id,), name=f"admin-warm-{obj.id}", daemon=True
            ).start()


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
        "wallet_address",
        "is_pro",
        "telegram_login_count",
        "wallet_login_count",
        "telegram_last_login_at",
        "wallet_last_login_at",
        "tg_link",
    )
    list_filter = ("is_pro",)
    search_fields = (
        "telegram_username",
        "telegram_name",
        "telegram_first_name",
        "telegram_id",
        "wallet_address",
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
        "wallet_address",
        "wallet_first_login_at",
        "wallet_last_login_at",
        "wallet_login_count",
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
